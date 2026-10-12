"""Bootstrap tests: the Controller wires the layers together correctly.

These run offscreen with fake capture/engine/translator, so they exercise the
real wiring without hardware, models or network.
"""

from __future__ import annotations

import pytest

from subbyai.core.events import CaptionSegment, PrivacyTier
from subbyai.core.health import HealthReport, HealthState
from subbyai.core.settings import OverlayPreset, SettingsStore


@pytest.fixture
def controller(qt_app, tmp_path, monkeypatch):
    from subbyai import app as app_module

    # Never touch audio hardware or the model cache.
    monkeypatch.setattr(app_module, "create_capture", lambda: _FakeCapture())
    monkeypatch.setattr(app_module.ModelManager, "is_downloaded", lambda self, model: True)
    store = SettingsStore(tmp_path / "settings.json")
    controller = app_module.Controller(qt_app, store)
    controller.captioner = _FakeCaptioner()
    controller._on_devices_changed(_FakeCapture.list_devices())
    yield controller
    controller._level_timer.stop()
    controller._model_timer.stop()
    controller._retention_timer.stop()
    if controller._retention_thread:
        controller._retention_thread.join(timeout=5)
    controller.sessions.close()
    controller.overlay.close()
    controller.shell.close()
    controller.deleteLater()


class _FakeCapture:
    @staticmethod
    def list_devices():
        from subbyai.audio.base import AudioDevice

        return [
            AudioDevice(
                id="0",
                name="Fake Speakers",
                sample_rate=16000,
                channels=1,
                is_loopback=True,
                is_default=True,
            )
        ]

    def start(self, device, callback):
        return self.list_devices()[0]

    def stop(self):
        pass


class _FakeCaptioner:
    def __init__(self):
        self.started = 0
        self.stopped_count = 0
        self.is_active = False
        self.has_workers = False
        self.health = HealthReport(HealthState.OFF)
        self.last_config = None

    def start(self, config, device):
        self.started += 1
        self.last_config = config
        self.is_active = True
        return True

    def stop(self):
        self.stopped_count += 1
        self.is_active = False

    def wait(self, timeout=0):
        return True

    def record_rendered(self, segment, **kwargs):
        pass


def _segment(text="hello", translation=None):
    seg = CaptionSegment(text=text, language="ja", audio_duration=1.0)
    if translation:
        seg = seg.with_translation(translation, "en", "Fake", PrivacyTier.ON_DEVICE)
    return seg


def test_surfaces_are_registered_in_order(controller):
    assert controller.shell.stack.count() == 3
    assert controller.shell.current_segment == 0  # Live is the default destination


def test_tray_open_restores_minimized_window_without_restarting_captioning(controller, qt_app):
    from PySide6.QtTest import QTest

    controller.shell.showMinimized()
    qt_app.processEvents()
    assert controller.shell.isMinimized()
    controller.tray._open_action.trigger()
    QTest.qWait(30)
    assert controller.shell.isVisible() and not controller.shell.isMinimized()
    assert controller.captioner.started == 0
    assert controller.captioner.stopped_count == 0


def test_caption_reaches_overlay_live_and_history(controller):
    controller.settings.history.enabled = True
    controller._start_history()
    controller._on_segment(_segment("こんにちは"))

    assert controller.live._segments[-1].text == "こんにちは"
    assert controller.overlay.visible_pairs
    assert controller.overlay.isVisible()
    controller.sessions.flush()
    assert controller.sessions.segments(controller._session_id)[0].text == "こんにちは"


def test_start_maps_overlay_with_default_auto_hide_and_toggle_is_live(controller):
    assert controller.settings.overlay.auto_hide
    controller._begin_session()
    assert controller.overlay.isVisible()
    controller._on_overlay_toggled(False)
    controller._on_segment(_segment("New caption"))
    assert not controller.overlay.isVisible()
    controller._on_overlay_toggled(True)
    assert controller.overlay.isVisible()
    assert controller.overlay._ring[-1].text == "New caption"


def test_remote_runtime_does_not_claim_a_local_model_is_selected(controller):
    from subbyai.asr.lifecycle import SpeechPhase, SpeechState

    controller.settings.processing.asr_backend = "remote"
    controller.settings.processing.model = "server-speech"
    controller._on_speech_state(
        SpeechState(SpeechPhase.ACTIVE, "server-speech", "server-speech", backend="remote")
    )
    assert "server-speech · Active" in controller.live.speech_indicator.text()
    assert "Selected:" not in controller.live.speech_indicator.text()
    controller._on_stopped()
    assert not controller._speech_state.model
    assert "not loaded" in controller.live.speech_indicator.text()


def test_remote_initialization_failure_leaves_a_retryable_state(controller, qt_app):
    from subbyai.asr.engine import EngineError
    from subbyai.asr.lifecycle import SpeechPhase
    from subbyai.core.settings import SessionConfig

    controller.settings.processing.asr_backend = "remote"
    controller.settings.processing.model = "server-speech"
    controller.settings.processing.base_url = "http://localhost:8000"
    controller.settings.processing.consent_url = ""  # client must reject missing consent
    with pytest.raises(EngineError):
        controller._provide_engine(SessionConfig.from_settings(controller.settings))
    qt_app.processEvents()
    assert controller._speech_state.phase is SpeechPhase.FAILED
    assert controller.live.start_button.isEnabled()


def test_language_changes_reconnect_with_updated_runtime_config(controller, wait_for_ui):
    controller._begin_session()
    controller.settings_view.languages.hear.choose("ja")
    assert controller._pending_restart
    controller._on_stopped()
    wait_for_ui(lambda: controller.captioner.started == 2)
    assert controller.captioner.last_config.source_language == "ja"
    controller.settings_view.languages.hear.choose("")
    assert controller._pending_restart
    controller._on_stopped()
    wait_for_ui(lambda: controller.captioner.started == 3)
    assert controller.captioner.last_config.source_language == ""


def test_translation_update_reaches_both_views(controller):
    original = _segment("こんにちは")
    controller._on_segment(original)
    updated = original.with_translation("Hello", "en", "Fake", PrivacyTier.ON_DEVICE)
    controller._on_segment_updated(updated)

    assert controller.live._segments[-1].display_translation == "Hello"


def test_health_is_broadcast_to_every_surface(controller):
    report = HealthReport(HealthState.SILENT, "We can't hear anything.", "Fake Speakers", 0.0)
    controller._on_health(report)

    # isHidden(), not isVisible(): offscreen parents are never "visible".
    assert not controller.live.banner.isHidden()
    assert "hear anything" in controller.live._empty_body.text()


def test_silent_health_offers_the_device_fix(controller):
    controller._on_health(HealthReport(HealthState.SILENT, "We can't hear anything."))
    controller.live.banner.action_clicked.emit("choose_device")
    assert controller.shell.current_segment == 2  # Settings


def test_errors_never_open_a_modal(controller, monkeypatch):
    """A pipeline error must be a banner, not a dialog over someone's game."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: pytest.fail("no modal dialogs on error")
    )
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: pytest.fail("no modal dialogs on error")
    )
    controller._on_error("SubbyAI couldn't open your audio device.")
    assert not controller.live.banner.isHidden()


def test_translation_toggle_sets_a_target_language(controller):
    controller._on_translation_toggled(True)
    assert controller.settings.captions.target_language
    controller._on_translation_toggled(False)
    assert controller.settings.captions.target_language == ""


def test_pipeline_receives_an_immutable_snapshot(controller):
    controller.settings.captions.target_language = "en"
    controller.settings.captions.source_language = "ja"
    controller._begin_session()

    config = controller.captioner.last_config
    assert config.target_language == "en"
    controller.settings.captions.target_language = "de"
    assert config.target_language == "en"  # a later edit cannot reach the worker


def test_preset_change_from_tray_applies_to_overlay(controller):
    controller._on_preset_selected(OverlayPreset.HIGH_CONTRAST)
    assert controller.settings.overlay.preset is OverlayPreset.HIGH_CONTRAST
    style = controller.overlay.caption_style
    # The accessibility preset must not inherit a smaller size from before.
    assert style.font_size >= 32
    assert style.animate is False
    assert style.dim_original == 1.0


def test_high_contrast_still_honours_a_larger_user_size(controller):
    controller.settings.overlay.font_size = 48
    controller._on_preset_selected(OverlayPreset.HIGH_CONTRAST)
    assert controller.overlay.caption_style.font_size == 48


def test_session_is_discarded_when_nothing_was_captioned(controller):
    controller.settings.history.enabled = True
    controller._begin_session()
    session_id = controller._session_id
    controller._end_session()
    assert controller.sessions.get_session(session_id) is None


def test_font_size_hotkey_clamps(controller):
    controller.settings.overlay.font_size = 71
    controller._resize_captions(2)
    assert controller.settings.overlay.font_size == 72
    controller.settings.overlay.font_size = 11
    controller._resize_captions(-2)
    assert controller.settings.overlay.font_size == 10


def test_overlay_never_takes_focus(controller):
    from PySide6.QtCore import Qt

    assert controller.overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_level_tick_survives_a_stopped_pipeline(controller):
    controller._tick_level()  # must not raise when nothing is running


# ---------------------------------------------------------------------------
# Consent and credential storage must be enforced by the live application wiring.
# ---------------------------------------------------------------------------


def _cloud_provider(**overrides):
    from subbyai.core.settings import ProviderSettings

    defaults = {
        "id": "openai",
        "kind": "openai",
        "label": "A cloud translator",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "enabled": True,
    }
    return ProviderSettings(**{**defaults, **overrides})


def test_a_cloud_translator_is_not_used_without_consent(controller):
    """Live captions must not reach a cloud endpoint on an unconsented setup."""
    from subbyai.core.events import PrivacyTier
    from subbyai.core.settings import SessionConfig

    settings = controller.settings
    settings.captions.source_language = "ja"
    settings.captions.target_language = "en"
    settings.intelligence.providers = [_cloud_provider()]
    settings.intelligence.translation_order = ["openai", "builtin"]

    chain = controller._provide_translator(SessionConfig.from_settings(settings))
    tiers = [p.tier for p in chain.providers]
    assert PrivacyTier.CLOUD not in tiers, (
        "a cloud provider reached the live pipeline without consent"
    )


def test_consent_lets_the_cloud_translator_through(controller):
    from subbyai.core.events import PrivacyTier
    from subbyai.core.settings import SessionConfig

    settings = controller.settings
    settings.captions.source_language = "ja"
    settings.captions.target_language = "en"
    settings.intelligence.providers = [_cloud_provider()]
    settings.intelligence.translation_order = ["openai", "builtin"]
    settings.intelligence.providers[0].consent_url = "https://api.openai.com"

    chain = controller._provide_translator(SessionConfig.from_settings(settings))
    assert PrivacyTier.CLOUD in [p.tier for p in chain.providers]


def test_translation_always_has_something_to_fall_back_on(controller):
    """Refusing a cloud provider must not leave the user with no translator."""
    from subbyai.core.settings import SessionConfig

    settings = controller.settings
    settings.captions.source_language = "ja"
    settings.captions.target_language = "en"
    settings.intelligence.providers = [_cloud_provider()]
    settings.intelligence.translation_order = ["openai"]

    chain = controller._provide_translator(SessionConfig.from_settings(settings))
    assert chain.providers, "the on-device translator should still be there"


def test_the_settings_screen_can_actually_store_a_key(controller):
    """The UI says keys go to the OS credential store; it must be wired up."""
    deps = controller.settings_view._deps
    assert callable(deps.api_key_get)
    assert callable(deps.api_key_set)


def test_history_failure_does_not_break_stop_or_keep_session_ownership(
    controller, monkeypatch, qt_app,
):
    import contextlib
    import sqlite3

    from subbyai.storage.session_store import HistoryWriteError

    controller.settings.history.enabled = True
    controller._start_history()
    controller.sessions.flush()

    @contextlib.contextmanager
    def failed():
        raise sqlite3.OperationalError("Database is unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(controller.sessions, "_connect", failed)
    controller._end_session()
    assert controller._session_id is None
    with pytest.raises(HistoryWriteError):
        controller.sessions.flush()
    qt_app.processEvents()
    assert not controller.live.history_warning.isHidden()


def test_storage_warning_survives_healthy_audio(controller):
    controller._on_history_storage_failed()
    controller._on_health(HealthReport(HealthState.LISTENING))
    assert not controller.live.history_warning.isHidden()
    assert "couldn't be saved" in controller.live.history_warning._text.text()


def test_failed_history_retry_warns_again_without_consuming_prior_write_loss(
    controller, monkeypatch, wait_for_ui,
):
    import contextlib
    import sqlite3

    from subbyai.storage.session_store import HistoryWriteError

    @contextlib.contextmanager
    def unavailable():
        raise sqlite3.OperationalError("Storage unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(controller.sessions, "_connect", unavailable)
    controller.settings.history.enabled = True
    for _ in range(2):
        controller._end_session()
        controller._start_history()
        owner = controller._session_id
        wait_for_ui(
            lambda owner=owner: owner.failed and not controller.live.history_warning.isHidden()
        )
        assert owner.failed
        assert not controller.live.history_warning.isHidden()
    # Warning delivery/retry must not acknowledge away the real write failure.
    with pytest.raises(HistoryWriteError):
        controller.sessions.flush()


def test_unrelated_device_changes_do_not_interrupt_capture(controller, monkeypatch):
    from dataclasses import replace

    from subbyai.audio.base import AudioDevice

    selected = _FakeCapture.list_devices()[0]
    controller._capture_device = selected
    controller.settings.audio.device_id = selected.id
    controller.settings.audio.device_name = selected.name
    controller.captioner.is_active = True
    restarts = []
    monkeypatch.setattr(controller, "_restart_capture", lambda: restarts.append(True))
    controller._on_devices_changed(
        [
            replace(selected, is_default=False),
            AudioDevice("5", "Other speakers", 48000, 2, True, True),
        ]
    )
    assert restarts == []


def test_manual_stop_cancels_device_recovery(controller):
    controller.captioner.is_active = True
    controller._on_devices_changed([])
    assert controller._recover_device
    controller.stop_captions()
    controller._on_devices_changed(_FakeCapture.list_devices())
    assert controller.captioner.started == 0
    assert not controller._recover_device


def test_history_preference_change_applies_before_next_caption(controller):
    settings = controller.settings.history
    settings.enabled = True
    controller.captioner.is_active = True
    controller._start_history()
    first = controller._session_id
    controller._on_segment(_segment("first original", "first translation"))
    settings.save_original = False
    controller.store.notify("history")
    second = controller._session_id
    assert first != second
    controller._on_segment(_segment("second original", "second translation"))
    controller.sessions.flush()
    saved = controller.sessions.segments(second)
    assert saved[0].text == ""
    assert saved[0].translation == "second translation"


def test_late_caption_update_cannot_cross_a_history_policy_change(controller):
    controller.settings.history.enabled = True
    controller.captioner.is_active = True
    controller._start_history()
    first = controller._session_id
    original = _segment("first original", "first translation")
    controller._on_segment(original)
    controller.settings.history.save_original = False
    controller.store.notify("history")
    second = controller._session_id
    controller._on_segment_updated(original.with_translation(
        "late first translation", "en", "Fake", PrivacyTier.ON_DEVICE,
    ))
    controller._on_segment(_segment("second original", "second translation"))
    controller.sessions.flush()
    assert [s.translation for s in controller.sessions.segments(first)] == ["first translation"]
    assert [s.translation for s in controller.sessions.segments(second)] == ["second translation"]
    assert not controller.sessions.search("late first")


def test_quit_drains_final_caption_to_opt_in_history_without_reopening_ui(controller):
    controller.settings.history.enabled = True
    controller._start_history()
    session = controller._session_id
    controller._quitting = True
    controller.overlay.close()
    final = _segment("final buffered words")
    controller._on_segment(final)
    controller._on_segment_updated(final.with_translation(
        "final translation", "en", "Fake", PrivacyTier.ON_DEVICE,
    ))
    controller._end_session()
    controller.sessions.flush()
    assert not controller.overlay.isVisible()
    assert not controller.live._segments
    saved = controller.sessions.segments(session)
    assert [(s.text, s.translation) for s in saved] == [
        ("final buffered words", "final translation"),
    ]
    assert controller.sessions.get_session(session).ended_at is not None


def test_actual_quit_waits_for_queued_final_results_before_storage_close(controller, monkeypatch):
    import threading
    import time

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtTest import QTest

    from subbyai.translation import packages

    # This test keeps pytest's process alive after a real application shutdown.
    # Isolate its process-wide cancellation event, and verify cancellation too.
    shutdown_cancel = threading.Event()
    monkeypatch.setattr(packages, "_shutdown_cancel", shutdown_cancel)

    class Results(QObject):
        ready = Signal(object)

    results = Results()
    results.ready.connect(controller._on_segment)
    controller.settings.history.enabled = True
    controller._start_history()
    session = controller._session_id
    final = _segment("queued at capture shutdown")

    def wait_for_capture(timeout=0):
        producer = threading.Thread(target=lambda: results.ready.emit(final))
        producer.start()
        producer.join()
        return True

    controller.captioner.wait = wait_for_capture
    controller.shutdown_ready.disconnect(controller._finish_quit)
    closed = []
    controller.shutdown_ready.connect(lambda: closed.append(True))
    controller.quit()
    deadline = time.monotonic() + 5
    while not closed and time.monotonic() < deadline:
        QTest.qWait(10)
    assert closed == [True]
    assert shutdown_cancel.is_set()
    assert not controller.overlay.isVisible()
    assert [s.text for s in controller.sessions.segments(session)] == [final.text]
    assert controller.sessions.get_session(session).ended_at is not None


def test_missing_audio_is_reported_before_downloading_a_model(controller, monkeypatch):
    downloads = []
    controller.settings.audio.source = "microphone"
    monkeypatch.setattr(controller.models, "is_downloaded", lambda _model: False)
    monkeypatch.setattr(controller, "_start_download", downloads.append)
    controller.start_captions()
    assert not downloads and controller.captioner.started == 0
    assert "microphone" in controller.live.banner._text.text().lower()


def test_start_waits_for_initial_inventory_before_downloading(controller, monkeypatch):
    downloads = []
    controller._devices_ready = False
    monkeypatch.setattr(controller.models, "is_downloaded", lambda _model: False)
    monkeypatch.setattr(controller, "_start_download", downloads.append)
    controller.start_captions()
    assert controller._pending_start and not downloads
    controller._on_devices_changed(_FakeCapture.list_devices())
    assert len(downloads) == 1
