"""Settings screen: instant apply, computed privacy, and safe destruction.

Everything here runs offscreen against fakes. No audio device is opened, no
model is downloaded and no socket is created — a settings test that needed any
of those would be testing the wrong thing.
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit, QPushButton

from subbyai.core.events import PrivacyTier
from subbyai.core.settings import OverlayPreset, ProviderSettings, QualityTier, SettingsStore
from subbyai.ui import theme
from subbyai.ui.settings_intelligence import provider_tier
from subbyai.ui.settings_view import SettingsDeps, SettingsView
from subbyai.ui.shortcut_editor import ShortcutEditor

OPENAI_URL = "https://api.openai.com"
OLLAMA_URL = "http://localhost:11434"


# ---------- fixtures and fakes ----------


class NotifySpy:
    """Records every ``notify`` while still persisting, as the real one does."""

    def __init__(self, store: SettingsStore) -> None:
        self.calls: list[str] = []
        self._original = store.notify
        store.notify = self  # type: ignore[method-assign]

    def __call__(self, section: str) -> None:
        self.calls.append(section)
        self._original(section)

    def reset(self) -> None:
        self.calls.clear()


class FakeSessions:
    def __init__(self) -> None:
        self.deleted = 0

    def storage_bytes(self) -> int:
        return 41 * 1024 * 1024

    def delete_all(self) -> None:
        self.deleted += 1


class FakeModels:
    """Reports everything as already downloaded, and refuses to fetch anything."""

    def __init__(self, downloaded: bool = True) -> None:
        self.downloaded = downloaded

    def is_downloaded(self, model_name: str) -> bool:
        return self.downloaded

    def disk_bytes(self, model_name: str) -> int:
        return 500_000_000 if self.downloaded else 0

    def delete(self, model_name: str) -> int:
        raise AssertionError("tests must never delete a model")

    def download(self, *args, **kwargs):
        raise AssertionError("tests must never download a model")


class FakeDevice:
    def __init__(self, device_id: str, name: str, is_default: bool = False) -> None:
        self.id = device_id
        self.name = name
        self.is_default = is_default


@pytest.fixture(scope="session", autouse=True)
def _themed(qt_app):
    """Once per session: applying a stylesheet restyles every widget alive."""
    theme.apply(qt_app, "dark")


def shown(widget) -> bool:
    """Whether a widget would be on screen. Offscreen, nothing is truly visible."""
    return not widget.isHidden()


@pytest.fixture
def settings_store(tmp_path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def spy(settings_store) -> NotifySpy:
    return NotifySpy(settings_store)


def build_view(store: SettingsStore, **deps) -> SettingsView:
    view = SettingsView(store, SettingsDeps(**deps))
    return view


def add_provider(view: SettingsView, label: str, base_url: str, model: str = "a-model") -> str:
    form = view.intelligence.form
    form.name_edit.setText(label)
    form.base_url_edit.setText(base_url)
    form.model_combo.setEditText(model)
    form.submit()
    return view.intelligence.provider_ids()[-1]


# ---------- instant apply ----------


def test_building_the_view_changes_nothing(settings_store, spy):
    build_view(settings_store)
    assert spy.calls == []


@pytest.mark.parametrize(
    ("act", "section", "check"),
    [
        (
            lambda v: v.languages.show_original.setChecked(False),
            "captions",
            lambda s: s.captions.show_original is False,
        ),
        (
            lambda v: v.languages.hear.set_code("ja"),
            "captions",
            lambda s: s.captions.source_language == "ja",
        ),
        (
            lambda v: v.captions.font_size.slider.setValue(40),
            "overlay",
            lambda s: s.overlay.font_size == 40,
        ),
        (
            lambda v: v.captions.never_hide.setChecked(True),
            "overlay",
            lambda s: s.overlay.auto_hide is False,
        ),
        (
            lambda v: v.audio.sensitivity.setCurrentIndex(2),
            "captions",
            lambda s: s.captions.speech_sensitivity == "high",
        ),
        (
            lambda v: v.general.close_to_tray.setChecked(False),
            "general",
            lambda s: s.general.close_to_tray is False,
        ),
        (
            lambda v: v.general.retention.setCurrentIndex(1),
            "history",
            lambda s: s.history.retention_days == 30,
        ),
        (
            lambda v: v.general.theme_choice.setCurrentIndex(2),
            "general",
            lambda s: s.general.theme == "dark",
        ),
        (
            lambda v: v.intelligence.assistant_toggle.setChecked(True),
            "intelligence",
            lambda s: s.intelligence.assistant_enabled is True,
        ),
    ],
)
def test_a_control_writes_once_to_its_own_section(settings_store, spy, act, section, check):
    view = build_view(settings_store)
    spy.reset()
    act(view)
    assert spy.calls == [section]
    assert check(settings_store.settings)


def test_the_settings_screen_has_no_apply_button(settings_store):
    view = build_view(settings_store)
    labels = {b.text() for b in view.findChildren(QPushButton)}
    assert not labels & {"Apply", "OK", "Save", "Save settings"}


def test_a_capture_restart_is_requested_silently(settings_store):
    view = build_view(settings_store)
    restarts: list[int] = []
    view.restart_capture_requested.connect(lambda: restarts.append(1))
    view.languages.hear.set_code("de")
    assert restarts == [1]


def test_language_pickers_show_names_and_store_codes(settings_store):
    view = build_view(settings_store)
    picker = view.languages.hear
    for index in range(picker.count()):
        name, code = picker.itemText(index), str(picker.itemData(index) or "")
        assert name and name != code
        assert not re.fullmatch(r"[a-z]{2,3}", name)


# ---------- captions ----------


def test_the_custom_editor_only_enables_on_the_custom_preset(settings_store, spy):
    view = build_view(settings_store)
    assert settings_store.settings.overlay.preset is OverlayPreset.GLASS
    assert view.captions.custom_group.isEnabled() is False

    spy.reset()
    view.captions.preset_cards.card("custom").clicked.emit()
    assert spy.calls == ["overlay"]
    assert settings_store.settings.overlay.preset is OverlayPreset.CUSTOM
    assert view.captions.custom_group.isEnabled() is True

    view.captions.preset_cards.card("solid").clicked.emit()
    assert view.captions.custom_group.isEnabled() is False


def test_style_changes_are_announced_for_the_shell_to_paint(settings_store):
    view = build_view(settings_store)
    changes: list[int] = []
    view.style_changed.connect(lambda: changes.append(1))
    view.captions.preset_cards.card("solid").clicked.emit()
    assert changes == [1]


def test_reset_position_forgets_every_remembered_display(settings_store, spy):
    settings_store.settings.overlay.geometry = {"screen-1": [0, 0, 100, 40]}
    view = build_view(settings_store)
    spy.reset()
    view.captions.reset_position()
    assert settings_store.settings.overlay.geometry == {}
    assert spy.calls == ["overlay"]


# ---------- audio ----------


def test_the_device_list_names_its_empty_state(settings_store):
    view = build_view(settings_store, audio_devices=list)
    assert "look again" in view.audio.empty_note.text().lower()
    assert shown(view.audio.empty_note)


def test_choosing_a_device_stores_it_and_asks_for_a_restart(settings_store, spy):
    devices = [FakeDevice("a", "Speakers", is_default=True), FakeDevice("b", "Headphones")]
    view = build_view(settings_store, audio_devices=lambda: devices)
    restarts: list[int] = []
    view.restart_capture_requested.connect(lambda: restarts.append(1))
    spy.reset()

    view.audio._rows[1].button.click()
    assert settings_store.settings.audio.device_id == "b"
    assert spy.calls == ["audio"]
    assert restarts == [1]


def test_the_level_meter_takes_levels_from_the_shell(settings_store):
    view = build_view(settings_store, audio_devices=lambda: [FakeDevice("a", "Speakers")])
    view.set_level(0.4)  # must not raise; the shell owns capture, the view only draws


# ---------- intelligence ----------


def test_the_privacy_badge_is_computed_from_the_address_not_the_name(settings_store):
    view = build_view(settings_store)
    cloud_id = add_provider(view, "Local AI", OPENAI_URL)
    local_id = add_provider(view, "Ollama", OLLAMA_URL)

    rows = view.intelligence.rows()
    assert rows[cloud_id].badge.text() == PrivacyTier.CLOUD.label == "Cloud"
    assert rows[local_id].badge.text() == PrivacyTier.ON_DEVICE.label == "On this device"
    assert rows["builtin"].badge.text() == "On this device"


def test_provider_tier_ignores_a_reassuring_label():
    reassuring = ProviderSettings(id="x", kind="ollama", label="On-device AI", base_url=OPENAI_URL)
    assert provider_tier(reassuring) is PrivacyTier.CLOUD
    honest = ProviderSettings(id="y", kind="openai", label="OpenAI", base_url=OLLAMA_URL)
    assert provider_tier(honest) is PrivacyTier.ON_DEVICE


def test_the_form_badge_follows_the_address_as_it_is_typed(settings_store):
    view = build_view(settings_store)
    form = view.intelligence.form
    form.base_url_edit.setText(OLLAMA_URL)
    assert form.badge.text() == "On this device"
    form.base_url_edit.setText(OPENAI_URL)
    assert form.badge.text() == "Cloud"


def test_the_api_key_field_never_exposes_what_is_stored(settings_store):
    keys: dict[str, str] = {}
    view = build_view(
        settings_store,
        api_key_get=lambda provider_id: keys.get(provider_id),
        api_key_set=keys.__setitem__,
    )
    form = view.intelligence.form
    form.api_key_edit.setText("sk-super-secret")
    provider_id = add_provider(view, "My provider", OPENAI_URL)
    from subbyai.core.secrets import endpoint_key_id

    assert keys == {endpoint_key_id(provider_id, OPENAI_URL): "sk-super-secret"}
    assert form.api_key_edit.text() == ""

    view.intelligence.edit_provider(provider_id)
    assert form.api_key_edit.text() == ""
    assert form.api_key_state.text() == "Saved"
    assert shown(form.replace_key_button) and not shown(form.api_key_edit)
    assert form.api_key_edit.echoMode() is QLineEdit.EchoMode.Password
    assert "sk-super-secret" not in _all_text(view)

    form.replace_key()
    assert form.api_key_edit.text() == ""
    assert form.api_key_state.text() == ""


def test_reordering_providers_rewrites_the_fallback_order(settings_store, spy):
    view = build_view(settings_store)
    provider_id = add_provider(view, "Ollama", OLLAMA_URL)
    assert view.intelligence.provider_ids() == ["builtin", provider_id]

    spy.reset()
    view.intelligence.move_provider(0, 1)
    assert settings_store.settings.intelligence.translation_order == [provider_id, "builtin"]
    assert spy.calls == ["intelligence"]
    assert view.intelligence.provider_ids() == [provider_id, "builtin"]


def test_a_cloud_provider_asks_before_it_is_used(settings_store):
    view = build_view(settings_store)
    provider_id = add_provider(view, "Some cloud", OPENAI_URL)
    row = view.intelligence.rows()[provider_id]
    config = view.intelligence.config_for(provider_id)
    config.enabled = False
    row.set_use(False)

    row.use.setChecked(True)
    assert config.enabled is False
    assert shown(row.consent_button)
    assert "Some cloud" in row.note.text()

    row.consent_button.click()
    assert config.enabled is True
    assert config.consent_url == "https://api.openai.com"


def test_quality_cards_track_the_stored_tier(settings_store, spy):
    view = build_view(settings_store, model_manager=FakeModels())
    spy.reset()
    view.intelligence.quality.cards.card(QualityTier.QUICK.value).clicked.emit()
    assert settings_store.settings.captions.quality is QualityTier.QUICK
    assert spy.calls == ["captions"]
    assert not shown(view.intelligence.quality.download_button)


def test_a_missing_model_offers_a_download_and_says_its_size(settings_store):
    view = build_view(settings_store, model_manager=FakeModels(downloaded=False))
    assert shown(view.intelligence.quality.download_button)
    assert "MB download" in view.intelligence.quality.note.text()


# ---------- general ----------


def test_deleting_every_transcript_needs_a_second_press(settings_store):
    sessions = FakeSessions()
    view = build_view(settings_store, session_store=sessions)

    view.general.delete_button.click()
    assert sessions.deleted == 0

    view.general.cancel_button.click()
    assert sessions.deleted == 0

    view.general.delete_button.click()
    view.general.confirm_button.click()
    assert sessions.deleted == 1


def test_storage_used_is_shown_in_plain_units(settings_store):
    view = build_view(settings_store, session_store=FakeSessions())
    assert "41 MB" in view.general.storage_label.text()


def test_run_setup_again_calls_back_to_the_shell(settings_store):
    called: list[int] = []
    view = build_view(settings_store, on_rerun_onboarding=lambda: called.append(1))
    view.general.rerun_button.click()
    assert called == [1]


# ---------- shortcuts ----------


def press(editor: ShortcutEditor, action_id: str, key, modifiers) -> None:
    editor.start_recording(action_id)
    row = editor.row(action_id)
    row.chip.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier


def test_the_editor_records_a_chord(qt_app):
    editor = ShortcutEditor()
    seen: list[dict] = []
    editor.bindings_changed.connect(seen.append)

    press(editor, "toggle_captions", Qt.Key.Key_K, CTRL_ALT)
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+K"
    assert seen and seen[-1]["toggle_captions"] == "Ctrl+Alt+K"
    assert editor.row("toggle_captions").chip.text() == "Ctrl+Alt+K"


def test_a_modifier_only_chord_is_refused_with_a_reason(qt_app):
    editor = ShortcutEditor()
    reason = editor.propose("toggle_captions", "Ctrl+Alt")
    assert reason
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"
    assert editor.row("toggle_captions").note.text() == reason


def test_an_unmodified_key_is_refused_for_a_global_shortcut(qt_app):
    editor = ShortcutEditor()
    assert editor.propose("toggle_captions", "K")
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"


def test_a_clash_is_explained_inline_and_taken_over_on_the_second_press(qt_app):
    editor = ShortcutEditor()
    reason = editor.propose("toggle_overlay", "Ctrl+Alt+C")
    assert "Also used by" in reason
    assert "Captions on or off" in reason
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"
    assert editor.bindings()["toggle_overlay"] == "Ctrl+Alt+O"

    assert editor.propose("toggle_overlay", "Ctrl+Alt+C") == ""
    assert editor.bindings()["toggle_overlay"] == "Ctrl+Alt+C"
    assert editor.bindings()["toggle_captions"] == ""
    assert editor.row("toggle_captions").chip.text() == "Not set"


def test_resetting_a_row_restores_its_default(qt_app):
    editor = ShortcutEditor()
    assert editor.propose("toggle_captions", "Ctrl+Alt+K") == ""
    editor.reset("toggle_captions")
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"


def test_reset_all_can_be_undone(qt_app):
    editor = ShortcutEditor()
    editor.propose("toggle_captions", "Ctrl+Alt+K")
    editor.reset_all()
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"
    assert "Undo" not in editor.toast.message  # the action is the button, not the text
    editor.undo_reset()
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+K"


def test_backspace_clears_a_binding(qt_app):
    editor = ShortcutEditor()
    press(editor, "toggle_captions", Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
    assert editor.bindings()["toggle_captions"] == ""


def test_escape_leaves_the_binding_alone(qt_app):
    editor = ShortcutEditor()
    press(editor, "toggle_captions", Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"


def test_registration_failures_are_shown_on_their_row(qt_app):
    editor = ShortcutEditor()
    editor.set_failures({"toggle_captions": "Another app is already using Ctrl+Alt+C."})
    row = editor.row("toggle_captions")
    assert "Another app" in row.note.text()
    assert shown(row.retry_button)


def test_the_view_saves_and_re_registers_shortcuts(settings_store, spy):
    class FakeHotkeys:
        def __init__(self) -> None:
            self.registered: list[dict] = []

        def register_all(self, bindings: dict) -> dict:
            self.registered.append(dict(bindings))
            return {}

    hotkeys = FakeHotkeys()
    view = build_view(settings_store, hotkeys=hotkeys)
    spy.reset()
    view.shortcuts.propose("toggle_captions", "Ctrl+Alt+J")

    assert spy.calls == ["shortcuts"]
    assert settings_store.settings.shortcuts.globals["toggle_captions"] == "Ctrl+Alt+J"
    assert hotkeys.registered[-1]["toggle_captions"] == "Ctrl+Alt+J"


def _all_text(view: SettingsView) -> str:
    from PySide6.QtWidgets import QLabel

    parts = [w.text() for w in view.findChildren(QLabel)]
    parts += [w.text() for w in view.findChildren(QPushButton)]
    parts += [w.text() for w in view.findChildren(QLineEdit)]
    return " ".join(parts)
