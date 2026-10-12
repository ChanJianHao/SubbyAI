"""Regression tests for the user-visible runtime contracts, not saved selections."""

import threading
import time

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest

from subbyai.asr.catalogue import spec
from subbyai.asr.engine import EngineCache
from subbyai.asr.lifecycle import ModelDownloads, SpeechPhase, SpeechState
from subbyai.asr.models import DownloadProgress
from subbyai.core.events import CaptionSegment, PrivacyTier
from subbyai.core.settings import Settings, SettingsStore
from subbyai.languages import speech_language_support
from subbyai.ui.live_view import LiveView
from subbyai.ui.model_catalogue import ModelCatalogue
from subbyai.ui.overlay import CaptionOverlay, OverlayState
from subbyai.ui.overlay_geometry import clamp_rect
from subbyai.ui.shell import Shell
from subbyai.ui.widgets import LanguagePicker


def settle(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate(), "The asynchronous operation did not finish"


def test_explicit_quit_bypasses_close_to_tray(qt_app):
    settings = Settings()
    settings.general.close_to_tray = True
    shell = Shell(settings)
    closed_to_tray, quit_requested = [], []
    shell.close_to_tray_requested.connect(lambda: closed_to_tray.append(True))
    shell.quit_requested.connect(lambda: quit_requested.append(True))
    shell.show()
    shell.close()
    assert closed_to_tray and not quit_requested
    shell.show()
    shell.close_for_quit()
    assert quit_requested == [True]
    assert not shell.isVisible()
    assert settings.general.close_to_tray  # the user's preference is preserved


def test_default_auto_hide_maps_on_first_real_caption(qt_app):
    settings = Settings()
    settings.general.reduce_motion = True
    overlay = CaptionOverlay(settings)
    assert settings.overlay.auto_hide
    assert not overlay.isVisible()
    caption = CaptionSegment(text="The browser is in the foreground.")
    overlay.show_segment(caption)
    qt_app.processEvents()
    assert overlay.isVisible()
    assert overlay.state is OverlayState.VISIBLE
    assert overlay.windowOpacity() > 0
    assert overlay.parent() is None
    translated = caption.with_translation("Le navigateur.", "fr", "Local", PrivacyTier.ON_DEVICE)
    overlay.update_segment(translated)
    assert overlay._ring[-1].display_translation == "Le navigateur."


def test_disabled_overlay_keeps_latest_caption_and_reenables_immediately(qt_app):
    settings = Settings()
    settings.general.reduce_motion = True
    overlay = CaptionOverlay(settings)
    overlay.set_enabled(False)
    overlay.show_segment(CaptionSegment(text="Latest caption"))
    assert not overlay.isVisible()
    overlay.set_enabled(True)
    assert overlay.isVisible()
    assert overlay.windowOpacity() > 0
    assert overlay._ring[-1].text == "Latest caption"
    overlay.set_enabled(False)
    assert overlay.state is OverlayState.DISABLED


def test_sleep_and_live_setting_change_cannot_leave_overlay_invisible(qt_app):
    settings = Settings()
    settings.general.reduce_motion = True
    overlay = CaptionOverlay(settings)
    overlay.show_segment(CaptionSegment(text="One"))
    overlay._sleep()
    assert overlay.state is OverlayState.IDLE
    settings.overlay.auto_hide = False
    settings.overlay.opacity = 0  # malformed live configuration must remain readable
    overlay.apply_settings(settings)
    assert overlay.windowOpacity() >= 0.2
    assert overlay.state is OverlayState.VISIBLE


def test_live_toggle_applies_from_settings_and_preserves_native_window(qt_app):
    settings = Settings()
    settings.general.reduce_motion = True
    overlay = CaptionOverlay(settings)
    overlay.show_segment(CaptionSegment(text="Hello"))
    handle = int(overlay.winId())
    settings.overlay.visible = False
    overlay.apply_settings(settings)
    assert not overlay.isVisible()
    settings.overlay.visible = True
    overlay.apply_settings(settings)
    assert overlay.isVisible()
    assert int(overlay.winId()) == handle


def test_height_changes_keep_bottom_anchor(qt_app):
    settings = Settings()
    settings.general.reduce_motion = True
    settings.overlay.max_pairs = 1
    overlay = CaptionOverlay(settings)
    overlay.setGeometry(QRect(20, 200, 400, 100))
    overlay.show_segment(CaptionSegment(text="Short"))
    bottom = overlay.geometry().bottom()
    for text in ("A longer sentence with several words. " * 10, "Short again", "Long " * 90):
        overlay.show_segment(CaptionSegment(text=text))
        assert overlay.geometry().bottom() == bottom


@pytest.mark.parametrize("area", [QRect(-1920, -300, 1920, 1080), QRect(1920, 0, 640, 360)])
def test_geometry_respects_negative_origins_and_small_logical_displays(area):
    rect = clamp_rect(QRect(-9000, 9000, 1800, 900), area)
    assert area.contains(rect)


def test_search_picker_mouse_and_keyboard_commit_codes(qt_app):
    picker = LanguagePicker((("", "Auto detect"), ("en", "English"), ("ja", "Japanese")))
    seen = []
    picker.code_changed.connect(seen.append)
    picker.show()
    picker.showPopup()
    assert picker._popup.isVisible()
    QTest.keyClicks(picker.search, "japan")
    assert picker.results.count() == 1
    QTest.keyClick(picker.search, Qt.Key.Key_Return)
    assert picker.currentText() == "Japanese"
    assert picker.current_code() == "ja"
    assert seen == ["ja"]
    picker.showPopup()
    picker.search.setText("auto")
    QTest.keyClick(picker.search, Qt.Key.Key_Return)
    assert picker.current_code() == ""
    assert not picker._popup.isVisible()


def test_unsupported_languages_are_explained_and_disabled(qt_app):
    picker = LanguagePicker((("", "Auto detect"), ("en", "English"), ("ja", "Japanese")))
    supported, auto = speech_language_support(spec("parakeet-tdt-0.6b-v3"))
    picker.set_supported(supported, auto_detect=auto)
    picker.showPopup()
    assert not picker.results.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
    picker.search.setText("Japanese")
    item = picker.results.item(0)
    assert "unsupported" in item.text()
    picker._pick_language(item)
    assert picker.current_code() == ""


class InstallManager:
    def __init__(self):
        self.installed = set()
        self.release = threading.Event()
        self.calls = []
        self.fail = False

    def is_downloaded(self, model):
        return model in self.installed

    def disk_bytes(self, model):
        return 100 if model in self.installed else 0

    def download(self, model, report, cancel):
        self.calls.append(model)
        report(DownloadProgress(64, 100, "downloading"))
        self.release.wait(2)
        if cancel.is_set():
            return False
        if self.fail:
            report(DownloadProgress(64, 100, "error", "Connection interrupted."))
            return False
        report(DownloadProgress(100, 100, "verifying"))
        self.installed.add(model)
        return True


def test_shared_install_has_progress_dedup_verification_and_does_not_activate(qt_app):
    manager = InstallManager()
    jobs = ModelDownloads(manager)
    catalogue = ModelCatalogue(manager)
    catalogue.downloads = jobs
    catalogue.runtime = SpeechState(SpeechPhase.ACTIVE, "small", "small")
    jobs.changed.connect(lambda *_: catalogue.refresh())
    assert jobs.start("tiny")
    assert jobs.start("tiny")
    assert not jobs.start("base")
    settle(qt_app, lambda: jobs.progress["tiny"].phase == "downloading")
    row = catalogue.rows["tiny"]
    assert "64%" in row.status.text()
    assert not row.install_button.isEnabled()
    assert row.progress.value() == 64
    assert jobs.thread.is_alive()
    manager.release.set()
    settle(qt_app, lambda: not jobs.busy)
    catalogue.refresh()
    assert manager.calls == ["tiny"]
    assert jobs.progress["tiny"].phase == "done"
    assert row.use_button.isVisibleTo(row)
    assert catalogue.runtime.model == "small"


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_install_can_retry(qt_app, cancelled):
    manager = InstallManager()
    manager.fail = not cancelled
    jobs = ModelDownloads(manager)
    jobs.start("tiny")
    if cancelled:
        jobs.cancel()
    manager.release.set()
    settle(qt_app, lambda: not jobs.busy)
    assert jobs.progress["tiny"].phase == ("cancelled" if cancelled else "error")
    assert not manager.is_downloaded("tiny")
    manager.fail = False
    jobs.start("tiny")
    settle(qt_app, lambda: not jobs.busy)
    assert jobs.progress["tiny"].phase == "done"


def test_active_indicator_reports_loaded_engine_and_not_selected_model(qt_app):
    live = LiveView(Settings())
    live.set_speech_state(SpeechState(SpeechPhase.ACTIVE, "tiny", "tiny", "ja", "en"), "small")
    assert "Whisper tiny · Active" in live.speech_indicator.text()
    assert "Selected: Whisper small · not active" in live.speech_indicator.text()
    assert "Japanese → English" in live.language_indicator.text()
    live.set_speech_state(SpeechState(SpeechPhase.LOADING, requested="small"), "small")
    assert "loading Whisper small" in live.speech_indicator.text()
    assert "Active" not in live.speech_indicator.text()


def test_model_switch_releases_old_weights_and_restores_after_load_failure():
    order = []

    class Engine:
        def __init__(self, model, device):
            self.model_name = model
            self.is_loaded = False

        def load(self):
            order.append(("load", self.model_name))
            if self.model_name == "broken":
                raise RuntimeError("load failed")
            self.is_loaded = True

        def unload(self):
            order.append(("unload", self.model_name))
            self.is_loaded = False

    cache = EngineCache(Engine)
    previous, failure = cache.load("tiny")
    assert failure is None
    restored, failure = cache.load("broken")
    assert failure is not None
    assert not previous.is_loaded
    assert restored.is_loaded and restored.model_name == "tiny"
    assert order.index(("unload", "tiny")) < order.index(("load", "broken"))
    assert cache.current is restored


def test_overlay_and_language_preferences_survive_restart(tmp_path):
    file = tmp_path / "settings.json"
    store = SettingsStore(file)
    store.settings.overlay.visible = False
    store.settings.captions.source_language = "ja"
    store.settings.captions.target_language = "en"
    store.settings.captions.model_override = "tiny"
    store.notify("captions")
    loaded = SettingsStore(file).settings
    assert loaded.captions.source_language == "ja"
    assert loaded.captions.target_language == "en"
    assert loaded.model_name == "tiny"
    assert loaded.overlay.visible is False


def test_snapshot_without_tokenizer_is_not_installed(tmp_path):
    from subbyai.asr.models import ModelManager

    model = spec("tiny")
    snapshot = tmp_path / "models--Systran--faster-whisper-tiny" / "snapshots" / model.revision
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"weights")
    (snapshot / "config.json").write_text("{}")
    manager = ModelManager(tmp_path)
    assert not manager.is_downloaded("tiny")
    (snapshot / "tokenizer.json").write_text("{}")
    (snapshot / "vocabulary.txt").write_text("hello")
    assert manager.is_downloaded("tiny")
    (snapshot / ".subbyai-invalid").write_text("failed")
    assert not manager.is_downloaded("tiny")


def test_integrity_failure_retry_downloads_fresh_files(tmp_path, monkeypatch):
    import subbyai.asr.models as models

    model = spec("tiny")
    snapshot = tmp_path / "models--Systran--faster-whisper-tiny" / "snapshots" / model.revision
    snapshot.mkdir(parents=True)
    attempts = []

    def snapshot_download(*args, **kwargs):
        attempts.append(kwargs["force_download"])
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (snapshot / name).write_bytes(b"data")
        return str(snapshot)

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot_download)
    monkeypatch.setattr(models, "_verify_blob_hashes", lambda _: len(attempts) > 1)
    manager = models.ModelManager(tmp_path)
    assert not manager.download("tiny", lambda _: None, threading.Event())
    assert not manager.is_downloaded("tiny")
    # A new manager represents an app restart: rejection remains on disk.
    restarted = models.ModelManager(tmp_path)
    assert restarted.download("tiny", lambda _: None, threading.Event())
    assert attempts == [False, True]
    assert restarted.is_downloaded("tiny")
