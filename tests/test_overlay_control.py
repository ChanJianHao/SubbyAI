"""Release blocker: real visible acknowledgment independent of speech startup."""

import threading
import time

import pytest

from subbyai.core.health import HealthReport, HealthState
from test_app import controller as controller


def test_empty_overlay_enable_paints_status_without_starting_speech(controller, qt_app):
    controller._on_overlay_toggled(False)
    controller.live.overlay_chip.click()
    qt_app.processEvents()
    assert controller.live.overlay_chip.isChecked()
    assert controller.overlay.isVisible()
    assert controller.overlay._render_pairs(), (
        "A mapped but transparent empty window is not feedback"
    )
    assert "on" in controller.live.overlay_chip.text().casefold()
    assert controller.captioner.started == 0


def test_stopped_capture_cannot_leave_pink_control_for_hidden_overlay(controller, qt_app):
    controller._on_overlay_toggled(True)
    controller._on_health(HealthReport(HealthState.STARTING))
    controller._on_stopped()
    qt_app.processEvents()
    assert controller.settings.overlay.visible
    assert controller.live.overlay_chip.isChecked()
    assert controller.overlay.isVisible()
    assert controller.overlay._render_pairs()


def test_slow_persistence_cannot_block_click_feedback_or_second_click(
    controller, monkeypatch, qt_app
):
    controller._on_overlay_toggled(False)
    entered, release = threading.Event(), threading.Event()
    original = controller.store._write

    def slow_write(settings):
        entered.set()
        release.wait(0.6)
        original(settings)

    monkeypatch.setattr(controller.store, "_write", slow_write)
    started = time.monotonic()
    controller.live.overlay_chip.click()
    elapsed = time.monotonic() - started
    controller.live.overlay_chip.click()
    release.set()
    assert controller.store.flush_pending(2)
    qt_app.processEvents()
    assert elapsed < 0.15, f"Overlay click blocked for {elapsed:.3f} seconds"
    assert not controller.live.overlay_chip.isChecked()
    assert not controller.overlay.isVisible()


def test_health_feedback_tracks_startup_error_and_recovery(controller, qt_app):
    controller._on_overlay_toggled(True)
    for state, label in ((HealthState.STARTING, "Starting"), (HealthState.ERROR, "Error")):
        controller._on_health(HealthReport(state, "An actionable message."))
        qt_app.processEvents()
        assert label in controller.live.overlay_chip.text()
        assert controller.overlay.isVisible() and controller.overlay._render_pairs()
        assert controller.live.overlay_chip.isChecked()
    controller._on_health(HealthReport(HealthState.LISTENING))
    assert "Waiting" in controller.live.overlay_chip.text()
    controller._on_overlay_toggled(False)
    controller._on_health(HealthReport(HealthState.STARTING))
    assert not controller.overlay.isVisible()
    assert controller.live.overlay_chip.text() == "Overlay off"
    controller.store.flush_pending(2)


def test_mapping_failure_returns_control_to_off_with_error(controller, monkeypatch):
    def cannot_map(enabled):
        if enabled:
            raise RuntimeError("native window could not be mapped")

    monkeypatch.setattr(controller.overlay, "set_enabled", cannot_map)
    controller._on_overlay_toggled(True)
    assert not controller.settings.overlay.visible
    assert not controller.live.overlay_chip.isChecked()
    assert not controller.overlay.isVisible()
    assert controller.live.overlay_chip.text() == "Overlay off"
    assert not controller.live.banner.isHidden()
    controller.store.flush_pending(2)


@pytest.mark.parametrize("state", [HealthState.OFF, HealthState.STARTING, HealthState.ERROR])
def test_late_health_cannot_reopen_overlay_during_quit(controller, state):
    controller._on_overlay_toggled(True)
    controller._quitting = True
    controller.overlay.close()
    controller._on_health(HealthReport(state))
    assert not controller.overlay.isVisible()
    controller.store.flush_pending(2)


def test_caption_stop_keeps_last_final_words_available_to_paint(controller):
    from subbyai.core.events import CaptionSegment

    controller._on_overlay_toggled(True)
    controller._on_health(HealthReport(HealthState.LISTENING))
    controller._on_segment(CaptionSegment("The last buffered words."))
    controller._on_stopped()
    assert controller.overlay._ring[-1].text == "The last buffered words."
    controller.store.flush_pending(2)


def test_translation_only_history_removes_superseded_fragment(controller):
    from dataclasses import replace

    from subbyai.core.events import CaptionSegment, PrivacyTier, TranslationState

    controller.settings.history.enabled = True
    controller.settings.history.save_original = False
    controller.settings.history.save_translation = True
    controller._begin_session()
    original = CaptionSegment("Ask not!")
    controller._on_segment(original)
    controller._on_segment_updated(original.with_translation(
        "Fragment translation", "fr", "local", PrivacyTier.ON_DEVICE,
    ))
    following = CaptionSegment("what your country can do for you.")
    controller._on_segment(following)
    controller._on_segment_updated(replace(original, translation_state=TranslationState.GROUPED))
    controller._on_segment_updated(following.with_translation(
        "Complete sentence translation", "fr", "local", PrivacyTier.ON_DEVICE,
    ))
    controller.sessions.flush()
    saved = controller.sessions.segments(controller._session_id)
    assert [s.translation for s in saved] == ["Complete sentence translation"]
    assert not controller.sessions.search("Fragment")


def test_caption_download_is_visible_and_cancelled_start_cannot_resume(controller, monkeypatch):
    from subbyai.asr.models import DownloadProgress

    requested = []
    monkeypatch.setattr(
        controller.downloads, "start", lambda model: requested.append(model) or True,
    )
    controller._on_overlay_toggled(True)
    controller._start_download("small")
    assert controller.overlay._health.state is HealthState.DOWNLOADING
    assert "Downloading" in controller.live.overlay_chip.text()
    controller.cancel_download()
    controller._on_install_progress("small", DownloadProgress(phase="downloading"))
    controller._on_install_finished("small", True)
    assert controller.overlay._health.state is HealthState.OFF
    assert controller.captioner.started == 0
    controller.store.flush_pending(2)


def test_failed_caption_download_clears_pending_start_and_exposes_error(controller, monkeypatch):
    monkeypatch.setattr(controller.downloads, "start", lambda model: True)
    controller._on_overlay_toggled(True)
    controller._start_download("small")
    controller._on_install_finished("small", False)
    assert controller._install_continuation is None
    assert controller.overlay._health.state is HealthState.ERROR
    assert "Error" in controller.live.overlay_chip.text()
    assert not controller.live.banner.isHidden()
    controller.store.flush_pending(2)


@pytest.mark.parametrize("operation", ["start", "stop"])
def test_slow_history_lifecycle_cannot_block_overlay_feedback(controller, monkeypatch, operation):
    import contextlib

    controller.settings.history.enabled = True
    if operation == "stop":
        controller._session_id = controller.sessions.create_session("Buffered history")
    entered, release = threading.Event(), threading.Event()
    connect = controller.sessions._connect

    @contextlib.contextmanager
    def slow_connection():
        if not entered.is_set():
            entered.set()
            release.wait(0.6)
        with connect() as connection:
            yield connection

    monkeypatch.setattr(controller.sessions, "_connect", slow_connection)
    began = time.monotonic()
    (controller._begin_session if operation == "start" else controller._on_stopped)()
    elapsed = time.monotonic() - began
    controller._on_overlay_toggled(False)
    controller._on_overlay_toggled(True)
    release.set()
    controller.sessions.flush()
    controller.store.flush_pending(2)
    assert elapsed < 0.15, f"{operation} blocked UI feedback for {elapsed:.3f}s"
    assert controller.live.overlay_chip.isChecked() and controller.overlay.isVisible()
