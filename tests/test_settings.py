import json

from subbyai.core.settings import (
    OverlayPreset,
    QualityTier,
    SessionConfig,
    Settings,
    SettingsStore,
)


def test_defaults_written_on_first_load(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert path.exists()
    assert store.settings.captions.quality is QualityTier.BALANCED
    assert store.settings.overlay.preset is OverlayPreset.GLASS


def test_roundtrip_preserves_nested_values(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.settings.captions.target_language = "en"
    store.settings.overlay.font_size = 34
    store.settings.shortcuts.globals["toggle_captions"] = "Ctrl+Alt+K"
    store.notify("captions")

    reloaded = SettingsStore(path).settings
    assert reloaded.captions.target_language == "en"
    assert reloaded.overlay.font_size == 34
    assert reloaded.shortcuts.globals["toggle_captions"] == "Ctrl+Alt+K"


def test_int_value_for_float_field_is_accepted(tmp_path):
    """Integer opacity values are valid numeric settings."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"overlay": {"opacity": 1, "auto_hide_seconds": 6}}))
    settings = SettingsStore(path).settings
    assert settings.overlay.opacity == 1.0
    assert settings.overlay.auto_hide_seconds == 6.0


def test_out_of_range_values_are_clamped(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"overlay": {"font_size": 900, "max_pairs": 99}}))
    settings = SettingsStore(path).settings
    assert settings.overlay.font_size == 72
    assert settings.overlay.max_pairs == 4


def test_wrong_type_falls_back_to_default(tmp_path):
    """A hand-edited string font size must not crash the overlay later."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"overlay": {"font_size": "huge"}}))
    assert SettingsStore(path).settings.overlay.font_size == 26


def test_corrupt_file_is_backed_up_not_discarded(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    store = SettingsStore(path)
    assert store.settings.captions.quality is QualityTier.BALANCED
    assert path.with_suffix(".corrupt.json").exists()


def test_utf8_bom_file_is_readable(tmp_path):
    """UTF-8 settings with a BOM remain readable."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"general": {"theme": "dark"}}), encoding="utf-8-sig")
    assert SettingsStore(path).settings.general.theme == "dark"


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"bogus": 1, "captions": {"nope": 2, "target_language": "ja"}}))
    assert SettingsStore(path).settings.captions.target_language == "ja"


def test_subscribers_notified_on_change(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    seen = []
    store.subscribe(seen.append)
    store.notify("overlay")
    assert seen == ["overlay"]


def test_model_name_resolves_from_tier_and_override():
    settings = Settings()
    settings.captions.quality = QualityTier.QUICK
    assert settings.model_name == "base"
    settings.captions.model_override = "distil-large-v3"
    assert settings.model_name == "distil-large-v3"


def test_translation_enabled_requires_different_target():
    settings = Settings()
    assert settings.translation_enabled is False
    settings.captions.target_language = "en"
    settings.captions.source_language = "en"
    assert settings.translation_enabled is False  # same language: transcribe only
    settings.captions.source_language = "ja"
    assert settings.translation_enabled is True


def test_session_config_snapshot_is_immutable():
    settings = Settings()
    settings.captions.source_language = "ja"
    settings.captions.target_language = "en"
    config = SessionConfig.from_settings(settings)
    assert config.target_language == "en"
    settings.captions.target_language = "de"
    assert config.target_language == "en"  # snapshot unaffected by later edits


def test_vocabulary_survives_a_settings_round_trip(tmp_path):
    from subbyai.core.settings import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    store.settings.captions.vocabulary = "Midgar, Nibelheim"
    store.save()

    reloaded = SettingsStore(tmp_path / "settings.json")
    assert reloaded.settings.captions.vocabulary == "Midgar, Nibelheim"
    assert SessionConfig.from_settings(reloaded.settings).vocabulary == "Midgar, Nibelheim"
