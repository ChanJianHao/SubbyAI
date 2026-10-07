"""Product boundaries: source consent, persistence, typography and resource use."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont

from subbyai.audio.base import AudioCaptureError, AudioDevice, resolve_device
from subbyai.core.diagnostics import safe_snapshot
from subbyai.core.events import CaptionSegment, PrivacyTier
from subbyai.core.overlay_themes import apply_theme, save_theme
from subbyai.core.profiles import apply_profile
from subbyai.core.settings import HistorySettings, OverlayPreset, Settings, SettingsStore, from_dict
from subbyai.storage.policy import HistoryPolicy
from subbyai.storage.session_store import SessionStore
from subbyai.ui.overlay_geometry import clamp_rect
from subbyai.ui.overlay_layout import wrap_lines


def devices():
    return [
        AudioDevice("1", "Headphones", 48000, 2, True, True),
        AudioDevice("2", "Microphone", 44100, 1, False, True),
    ]


def test_default_source_never_opens_a_microphone():
    mic = devices()[1]
    assert resolve_device([mic], None) is None
    with pytest.raises(AudioCaptureError, match="selected source"):
        resolve_device([mic], None, strict=True)
    assert resolve_device([mic], None, source="microphone") is mic


@pytest.mark.parametrize("source,index", [("system", 0), ("microphone", 1)])
def test_each_source_has_its_own_default(source, index):
    available = devices()
    assert resolve_device(available, None, source=source) is available[index]


def test_reconnect_matches_name_within_the_chosen_kind():
    source = AudioDevice("7", "USB Audio", 48000, 1, False)
    output = AudioDevice("2", "USB Audio", 48000, 2, True, True)
    assert resolve_device([output, source], "2", "USB Audio", True, source="microphone") is source


def test_windows_lists_only_wasapi_inputs_and_does_not_duplicate_loopback(monkeypatch):
    from subbyai.audio.windows import WasapiLoopbackCapture

    base = {"defaultSampleRate": 48000, "maxInputChannels": 2, "hostApi": 3}
    entries = [
        base | {"index": 0, "name": "Output [Loopback]", "isLoopbackDevice": True},
        base | {"index": 1, "name": "Microphone"},
        base | {"index": 2, "name": "Microphone WME", "hostApi": 1},
    ]
    pa = Mock()
    pa.__enter__ = Mock(return_value=pa)
    pa.__exit__ = Mock(return_value=False)
    pa.get_default_wasapi_loopback.return_value = entries[0]
    pa.get_loopback_device_info_generator.return_value = iter(entries[:1])
    pa.get_host_api_info_by_type.return_value = {"index": 3, "defaultInputDevice": 1}
    pa.get_device_count.return_value = 3
    pa.get_device_info_by_index.side_effect = entries.__getitem__
    monkeypatch.setitem(
        sys.modules, "pyaudiowpatch", SimpleNamespace(PyAudio=lambda: pa, paWASAPI=13)
    )
    available = WasapiLoopbackCapture.list_devices()
    assert [(d.name, d.source, d.is_default) for d in available] == [
        ("Output", "system", True),
        ("Microphone", "microphone", True),
    ]


def test_windows_releases_handles_even_when_the_driver_refuses_stop():
    from subbyai.audio.windows import WasapiLoopbackCapture

    capture = WasapiLoopbackCapture()
    stream, driver = Mock(), Mock()
    stream.stop_stream.side_effect = OSError("Disconnected")
    driver.terminate.side_effect = OSError("Already unavailable")
    capture._stream, capture._pa = stream, driver
    capture.stop()
    capture.stop()
    stream.close.assert_called_once()
    driver.terminate.assert_called_once()
    assert capture._stream is None and capture._pa is None


def test_macos_closes_a_stream_that_failed_to_start(monkeypatch):
    from subbyai.audio.macos import CoreAudioInputCapture

    stream = Mock()
    stream.start.side_effect = PermissionError("Microphone permission denied")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=lambda **_: stream))
    capture = CoreAudioInputCapture()
    with pytest.raises(AudioCaptureError):
        capture.start(devices()[1], lambda *_: None)
    stream.close.assert_called_once()
    assert capture._stream is None


@pytest.mark.parametrize("original,translated", [(True, True), (True, False), (False, True)])
def test_history_filters_before_any_disk_write(tmp_path, original, translated):
    policy = HistoryPolicy(True, original, translated)
    segment = CaptionSegment("original-private-sentinel").with_translation(
        "translated-private-sentinel",
        "fr",
        "Private server",
        PrivacyTier.CLOUD,
    )
    stored = policy.for_storage(segment)
    assert stored is not None and stored.translation_provider is None
    store = SessionStore(tmp_path / "字幕 🌸.db")
    session = store.create_session("Session")
    store.add_segment(session, stored)
    store.flush()
    recovered = store.segments(session)[0]
    assert recovered.text == (segment.text if original else "")
    assert recovered.translation == (segment.translation if translated else None)
    store.close()
    payload = b"".join(path.read_bytes() for path in tmp_path.iterdir() if path.is_file())
    assert (segment.text.encode() in payload) is original
    assert (segment.translation.encode() in payload) is translated


def test_translation_only_does_not_enqueue_original_or_failed_translation():
    policy = HistoryPolicy(True, False, True)
    assert policy.for_storage(CaptionSegment("original-private-sentinel")) is None


def test_history_off_and_session_only_never_offer_text_for_storage():
    segment = CaptionSegment("private")
    for history in (HistorySettings(), HistorySettings(enabled=True, retention_days=-1)):
        assert HistoryPolicy.from_settings(history).for_storage(segment) is None


def test_live_only_history_reads_and_maintenance_do_not_create_a_database(tmp_path):
    path = tmp_path / "session.db"
    store = SessionStore(path, lazy=True)
    store.start_writer()
    assert store.list_sessions() == []
    assert store.get_session(1) is None
    assert store.segments(1) == []
    assert store.search("anything") == []
    assert store.apply_retention(30) == 0
    store.delete_all()
    store.close()
    assert not list(tmp_path.iterdir())


def test_metadata_only_history_keeps_no_words(tmp_path):
    store = SessionStore(tmp_path / "session.db", lazy=True)
    policy = HistoryPolicy(True, False, False)
    assert policy.for_storage(CaptionSegment("private")) is None
    session = store.create_session("Session", "ja", "en")
    store.end_session(session, keep_empty=True)
    assert store.get_session(session).segment_count == 0
    assert store.segments(session) == []
    store.close()


def test_saved_theme_round_trip_preserves_unicode_and_excludes_display_identifiers(tmp_path):
    store = SettingsStore(tmp_path / "設定 🌸" / "settings.json")
    overlay = store.settings.overlay
    overlay.preset = OverlayPreset.CUSTOM
    overlay.font_size = 42
    overlay.geometry = {"private-display-name": [1, 2, 400, 300]}
    overlay.screen_name = "private-display-name"
    save_theme(overlay, "字幕 🌸")
    assert "private-display-name" not in json.dumps(overlay.saved_themes)
    store.save()
    restored = SettingsStore(store.path).settings.overlay
    restored.font_size = 20
    apply_theme(restored, "字幕 🌸")
    assert restored.font_size == 42
    assert restored.screen_name == overlay.screen_name
    assert restored.geometry == overlay.geometry


def test_saved_theme_data_is_bounded_and_validated():
    themes = {
        f"Look {index}": {"font_size": 90000, "custom_text_color": "bad", "api_key": "secret"}
        for index in range(100)
    }
    settings = from_dict(Settings, {"overlay": {"saved_themes": themes}})
    assert len(settings.overlay.saved_themes) == 20
    for look in settings.overlay.saved_themes.values():
        assert look["font_size"] == 72
        assert look["custom_text_color"] == "#FFFFFF"
        assert "api_key" not in look
    with pytest.raises(ValueError):
        save_theme(settings.overlay, "Too many")
    with pytest.raises(ValueError):
        save_theme(Settings().overlay, "Control\ncharacter")


@pytest.mark.parametrize(
    "text", ["👩‍💻á🇯🇵" * 4, "字幕こんにちは안녕하세요" * 4, "مرحبا بالعالم שלום עולם", "नमस्ते दुनिया"]
)
def test_unicode_caption_wrapping_preserves_every_character(qt_app, text):
    font = QFont("Segoe UI")
    font.setPixelSize(26)
    wrapped = wrap_lines(text, font, 40)
    assert "".join(wrapped).replace(" ", "") == text.replace(" ", "")
    assert all(not line.startswith("\u200d") and not line.endswith("\u200d") for line in wrapped)
    assert all(not line.startswith("\u0301") for line in wrapped)


def test_tiny_logical_display_clamps_both_size_and_position():
    area = QRect(-240, 0, 240, 400)
    clamped = clamp_rect(QRect(-1000, 390, 900, 800), area)
    assert area.contains(clamped)
    assert clamped.size() == area.size()


def test_topmost_can_be_disabled_without_a_reassertion_timer(qt_app, monkeypatch):
    from subbyai.system import window_effects
    from subbyai.ui.overlay import CaptionOverlay

    settings = Settings()
    settings.overlay.always_on_top = False
    calls = []
    monkeypatch.setattr(window_effects, "set_topmost", lambda *_: calls.append(True))
    overlay = CaptionOverlay(settings)
    overlay.show()
    overlay._apply_topmost()
    assert not overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert not overlay._topmost_timer.isActive() and not calls
    overlay.close()


def test_maximum_profile_uses_conservative_hardware_limits():
    from subbyai.asr.capability import MachineCapability
    from subbyai.core.settings import QualityTier

    settings = Settings()
    apply_profile(settings, "maximum", MachineCapability(has_cuda=True, vram_gb=12, ram_gb=32))
    assert settings.captions.quality is QualityTier.MAXIMUM
    apply_profile(settings, "maximum", MachineCapability(has_cuda=True, vram_gb=0, ram_gb=16))
    assert settings.captions.quality is QualityTier.DETAILED
    apply_profile(settings, "maximum", MachineCapability(ram_gb=2))
    assert settings.captions.quality is QualityTier.QUICK


def test_diagnostic_report_drops_free_form_and_nonfinite_fields():
    report = safe_snapshot(
        {
            "phase": "running",
            "processing": "local",
            "audio_source": "microphone",
            "history_pending": 2,
            "history_dropped": 0,
            "api_key": "private-key",
            "transcript": "private-words",
            "pipeline": {
                "asr_seconds": 1.123456,
                "translation_seconds": float("nan"),
                "recognition_queue": "private-words",
                "unrecognised": "private-path",
            },
        }
    )
    assert "private" not in json.dumps(report)
    assert report["pipeline"] == {"asr_seconds": 1.1235}


def test_app_text_scale_keeps_caption_size_independent(qt_app):
    from subbyai.ui import theme

    theme.apply(qt_app, "dark", text_scale=1.5)
    try:
        assert "font-size: 30px" in qt_app.styleSheet()
        assert theme.caption_font(26, 600).pixelSize() == 26
    finally:
        theme.apply(qt_app, "dark")


def test_caption_sliders_expose_their_setting_name_and_keyboard_focus(qt_app):
    from subbyai.ui.settings_captions import _SliderRow
    from subbyai.ui.settings_widgets.common import SettingRow

    control = _SliderRow(10, 72, str)
    row = SettingRow("Subtitle font size", control)
    assert control.focusProxy() is control.slider
    assert row._label.buddy() is control.slider
    assert control.slider.accessibleName() == "Subtitle font size"


def test_status_and_user_named_hints_are_plain_text(qt_app):
    from subbyai.ui.settings_widgets.common import hint_label
    from subbyai.ui.widgets import StatusBanner

    markup = '<img src="file:///private-image.png">'
    hint = hint_label(markup)
    banner = StatusBanner()
    banner.show_message(markup)
    assert hint.textFormat() is Qt.TextFormat.PlainText
    assert banner._text.textFormat() is Qt.TextFormat.PlainText
