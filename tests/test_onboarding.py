"""First-run wizard, offscreen. Hardware detection is always faked."""

from __future__ import annotations

import sys
import threading

import pytest

from subbyai.asr import capability as capability_module
from subbyai.asr.capability import MachineCapability, recommended_tier
from subbyai.core.settings import OverlayPreset, QualityTier, Settings
from subbyai.ui.onboarding import OnboardingWizard
from subbyai.ui.onboarding_widgets import SOUND_LEVEL

# Plenty of processor and memory, no graphics card: Maximum is out of reach.
CPU_ONLY = MachineCapability(
    has_cuda=False, cpu_cores=8, ram_gb=16.0, free_disk_gb=200.0, platform="win32"
)
WITH_GPU = MachineCapability(
    has_cuda=True,
    gpu_name="Test card",
    vram_gb=12.0,
    cpu_cores=16,
    ram_gb=32.0,
    free_disk_gb=200.0,
    platform="win32",
)


@pytest.fixture(autouse=True)
def _never_probe_real_hardware(monkeypatch):
    monkeypatch.setattr(capability_module, "detect", lambda *a, **k: CPU_ONLY)


@pytest.fixture
def make_wizard(qt_app):
    built: list[OnboardingWizard] = []

    def _make(settings: Settings | None = None, **kwargs) -> OnboardingWizard:
        kwargs.setdefault("capability", CPU_ONLY)
        wizard = OnboardingWizard(settings or Settings(), **kwargs)
        built.append(wizard)
        return wizard

    yield _make
    for wizard in built:
        wizard.close()
        wizard.deleteLater()


def _last_index(wizard: OnboardingWizard) -> int:
    return len(wizard._steps) - 1


def test_wizard_advances_and_goes_back(make_wizard):
    wizard = make_wizard()
    assert wizard.current_index == 0
    assert not wizard._back.isVisible()

    wizard._primary.click()
    assert wizard.current_index == 1
    wizard._primary.click()
    assert wizard.current_index == 2

    wizard._back.click()
    assert wizard.current_index == 1
    wizard._back.click()
    assert wizard.current_index == 0
    wizard._back.click()
    assert wizard.current_index == 0


def test_skipping_from_welcome_leaves_usable_defaults(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)

    wizard._skip.click()

    assert settings.general.onboarding_complete is True
    assert settings.general.usage_intents == ["everything"]
    assert settings.captions.quality is recommended_tier(CPU_ONLY)
    assert settings.captions.target_language == ""  # skip never turns translation on
    assert settings.overlay.preset is OverlayPreset.GLASS
    assert wizard.current_index == _last_index(wizard)


def test_accessibility_row_applies_the_whole_bundle(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)

    wizard._usage.select("accessibility")

    assert settings.general.accessibility_mode is True
    assert settings.overlay.preset is OverlayPreset.HIGH_CONTRAST
    assert settings.overlay.auto_hide is False
    assert settings.overlay.font_size >= 32
    assert settings.history.enabled is False  # accessibility never grants storage consent


def test_accessibility_wins_over_games_and_unticking_restores(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)

    wizard._usage.select("games")
    wizard._usage.select("accessibility")
    assert settings.overlay.preset is OverlayPreset.HIGH_CONTRAST

    wizard._usage.select("accessibility", on=False)
    assert settings.general.accessibility_mode is False
    assert settings.overlay.preset is OverlayPreset.SOLID


def test_choosing_games_sets_the_solid_preset(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)

    wizard._usage.select("games")

    assert settings.general.usage_intents == ["games"]
    assert settings.overlay.preset is OverlayPreset.SOLID
    assert wizard._ready._games_tip.isVisibleTo(wizard._ready)


def test_style_card_writes_the_preset(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)
    seen: list[object] = []
    wizard.style_previewed.connect(seen.append)

    wizard._style.select(OverlayPreset.MINIMAL)

    assert settings.overlay.preset is OverlayPreset.MINIMAL
    assert seen == [OverlayPreset.MINIMAL]


def test_language_choice_shows_names_and_stores_codes(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)
    step = wizard._language

    step.set_languages("ja", "en")

    assert settings.captions.source_language == "ja"
    assert settings.captions.target_language == "en"
    assert step._source.currentText() == "Japanese"
    assert step._target.currentText() == "English"
    assert step._preview.isVisibleTo(step)
    assert not step._same.isVisibleTo(step)


def test_matching_languages_say_it_only_transcribes(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)
    step = wizard._language

    step.set_languages("de", "de")

    assert settings.translation_enabled is False
    assert step._same.isVisibleTo(step)
    assert not step._preview.isVisibleTo(step)


def test_selecting_a_tier_writes_the_quality(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)

    wizard._quality._cards[QualityTier.DETAILED].click()

    assert settings.captions.quality is QualityTier.DETAILED


def test_an_unavailable_tier_cannot_be_selected(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)
    card = wizard._quality._cards[QualityTier.MAXIMUM]

    assert not card.isEnabled()
    assert card._reason == "Needs a graphics card."

    card.click()

    assert settings.captions.quality is not QualityTier.MAXIMUM


def test_recommended_badge_follows_the_machine(make_wizard):
    wizard = make_wizard(Settings(), capability=WITH_GPU)

    badged = [tier for tier, card in wizard._quality._cards.items() if card._badge]

    assert badged == [recommended_tier(WITH_GPU)]
    assert wizard._quality._cards[QualityTier.MAXIMUM].isEnabled()


def test_closing_the_dialog_still_marks_onboarding_complete(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)
    seen: list[bool] = []
    wizard.finished_setup.connect(seen.append)

    wizard.close()

    assert settings.general.onboarding_complete is True
    assert seen == [False]


def test_start_captions_emits_true(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings)
    seen: list[bool] = []
    wizard.finished_setup.connect(seen.append)

    wizard._show_step(_last_index(wizard))
    wizard._primary.click()

    assert seen == [True]
    assert settings.general.onboarding_complete is True


def test_open_the_app_first_emits_false(make_wizard):
    wizard = make_wizard()
    seen: list[bool] = []
    wizard.finished_setup.connect(seen.append)

    wizard._show_step(_last_index(wizard))
    wizard._secondary.click()

    assert seen == [False]


def test_finished_setup_fires_only_once(make_wizard):
    wizard = make_wizard()
    seen: list[bool] = []
    wizard.finished_setup.connect(seen.append)

    wizard._show_step(_last_index(wizard))
    wizard._primary.click()
    wizard.close()
    wizard.reject()

    assert seen == [True]


def test_audio_check_reports_that_it_can_hear(make_wizard):
    wizard = make_wizard()
    step = wizard._audio

    step.set_level(SOUND_LEVEL * 4)

    assert step._status.text() == "We can hear it. You're set."


def test_audio_check_names_the_silence_and_offers_a_device(make_wizard):
    class FakeDevice:
        source = "system"

        def __init__(self, ident, name):
            self.id = ident
            self.name = name
            self.is_default = True

    settings = Settings()
    wizard = make_wizard(settings, devices=[FakeDevice("dev-1", "Speakers")])
    step = wizard._audio

    step._report_silence()

    if sys.platform == "darwin":
        assert "permission" in step._status.text()
    else:
        assert "Nothing yet" in step._status.text()
        assert step._picker.isVisibleTo(step)

    step._pick_device(0)
    assert settings.audio.device_id == "dev-1"
    assert settings.audio.device_name == "Speakers"


def test_capability_is_detected_off_the_ui_thread(qt_app, monkeypatch):
    seen: dict[str, object] = {}

    def fake_detect(*_args, **_kwargs):
        seen["off_main"] = threading.current_thread() is not threading.main_thread()
        return WITH_GPU

    monkeypatch.setattr(capability_module, "detect", fake_detect)
    wizard = OnboardingWizard(Settings())
    try:
        assert wizard._probe is not None
        assert wizard._probe.wait(10_000)
        qt_app.processEvents()
        assert seen["off_main"] is True
        assert wizard._capability == WITH_GPU
        assert wizard._quality._cards[QualityTier.MAXIMUM].isEnabled()
    finally:
        wizard.close()
        wizard.deleteLater()


def test_every_step_paints(make_wizard):
    """The cards are custom-painted, so construction alone proves very little."""
    wizard = make_wizard()
    wizard._usage.select("accessibility")
    wizard._language.set_languages("ja", "en")
    for index in range(len(wizard._steps)):
        wizard._show_step(index)
        assert not wizard._steps[index].grab().isNull()


def test_restore_link_is_hidden_without_a_callback(make_wizard):
    wizard = make_wizard()
    assert not wizard._welcome._restore.isVisibleTo(wizard._welcome)


def test_restore_link_reports_when_nothing_was_found(make_wizard):
    wizard = make_wizard(Settings(), restore_callback=lambda: False)

    assert wizard._welcome._restore.isVisibleTo(wizard._welcome)
    wizard._welcome._restore.click()

    assert "couldn't find" in wizard._welcome._note.text()
    assert wizard.current_index == 0


def test_restore_success_jumps_to_the_end(make_wizard):
    settings = Settings()
    wizard = make_wizard(settings, restore_callback=lambda: True)

    wizard._welcome._restore.click()

    assert wizard.current_index == _last_index(wizard)
    assert settings.general.onboarding_complete is True


def test_advanced_sheet_is_the_only_place_model_ids_appear(make_wizard):
    from subbyai.ui.onboarding_hardware import AdvancedEngineSheet

    settings = Settings()
    wizard = make_wizard(settings)
    sheet = AdvancedEngineSheet(settings, wizard)
    sheet._model.setCurrentIndex(sheet._model.findData("large-v3"))
    sheet._device.setCurrentIndex(sheet._device.findData("cpu"))

    sheet._save()

    assert settings.captions.model_override == "large-v3"
    assert settings.captions.compute_device == "cpu"
    assert settings.model_name == "large-v3"
    sheet.deleteLater()
