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
    monkeypatch.setattr(
        app_module.ModelManager, "is_downloaded", lambda self, model: True
    )
    store = SettingsStore(tmp_path / "settings.json")
    controller = app_module.Controller(qt_app, store)
    controller.captioner = _FakeCaptioner()
    yield controller
    controller.sessions.close()
    controller.overlay.close()
    controller.shell.close()


class _FakeCapture:
    @staticmethod
    def list_devices():
        from subbyai.audio.base import AudioDevice

        return [
            AudioDevice(
                id="0", name="Fake Speakers", sample_rate=16000, channels=1,
                is_loopback=True, is_default=True,
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


def _segment(text="hello", translation=None):
    seg = CaptionSegment(text=text, language="ja", audio_duration=1.0)
    if translation:
        seg = seg.with_translation(translation, "en", "Fake", PrivacyTier.ON_DEVICE)
    return seg


def test_surfaces_are_registered_in_order(controller):
    assert controller.shell.stack.count() == 3
    assert controller.shell.current_segment == 0  # Live is the default destination


def test_caption_reaches_overlay_live_and_history(controller):
    controller.settings.history.enabled = True
    controller._session_id = controller.sessions.create_session("Test")
    controller._on_segment(_segment("こんにちは"))

    assert controller.live._segments[-1].text == "こんにちは"
    assert controller.overlay.visible_pairs
    controller.sessions.flush()
    assert controller.sessions.segments(controller._session_id)[0].text == "こんにちは"


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
# Promises the app makes about privacy, enforced on the path it actually runs.
#
# Both of these were false. The cloud-consent gate lived in build_chain, which
# the live pipeline never called, and the API-key store was advertised in four
# documents and the UI while nothing ever stored a key.
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


def test_history_failure_does_not_break_stop_or_keep_session_ownership(controller, monkeypatch):
    import sqlite3

    def failed(_session):
        raise sqlite3.OperationalError("Database is unavailable")

    controller._session_id = 42
    monkeypatch.setattr(controller.sessions, "end_session", failed)
    controller._end_session()
    assert controller._session_id is None
