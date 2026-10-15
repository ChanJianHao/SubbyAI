"""Discoverable settings and transactional shortcut editing through real widgets."""

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from subbyai.core.settings import SettingsStore
from subbyai.core.shortcuts import SHELL_ACTIONS, TRANSCRIPT_ACTIONS, default_bindings
from subbyai.system.hotkeys import HotkeyManager, is_valid_global
from subbyai.ui import theme
from subbyai.ui.onboarding_steps import ReadyStep
from subbyai.ui.settings_view import SettingsDeps, SettingsView
from subbyai.ui.shell import Shell
from subbyai.ui.shortcut_editor import ShortcutEditor
from subbyai.ui.transcript_view import TranscriptView

CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier


class Registrar:
    is_supported = True

    def __init__(self):
        self.active = {}
        self.calls = []
        self.blocked = set()

    def register_all(self, bindings):
        self.calls.append(dict(bindings))
        failures = {
            action: f"Another app is already using {sequence}."
            for action, sequence in bindings.items()
            if sequence in self.blocked
        }
        self.active = {a: s for a, s in bindings.items() if s and a not in failures}
        return failures

    def unregister_all(self):
        self.active = {}


@pytest.fixture
def view(qt_app, tmp_path):
    registrar = Registrar()
    store = SettingsStore(tmp_path / "settings.json")
    widget = SettingsView(store, SettingsDeps(hotkeys=registrar))
    widget.shortcut_controller.reload()
    return widget


def visible_pages(view):
    return {
        view.nav.item(i).text() for i in range(view.nav.count()) if not view.nav.item(i).isHidden()
    }


def test_shortcuts_stays_visible_searchable_and_does_not_enable_advanced(view, tmp_path):
    assert "Shortcuts" in visible_pages(view)
    view.search.setText("shortcut")
    assert "Shortcuts" in visible_pages(view)
    view.show_section("shortcuts")
    assert view._current_key() == "shortcuts"
    assert not view.advanced_toggle.isChecked()
    assert not view._store.settings.general.advanced_mode
    view._store.save()
    reopened = SettingsView(SettingsStore(tmp_path / "settings.json"))
    assert "Shortcuts" in visible_pages(reopened)
    reopened.show_section("shortcuts")
    assert not reopened.advanced_toggle.isChecked()


def test_partial_saved_bindings_share_defaults_between_editor_and_runtime(qt_app, tmp_path):
    profile = tmp_path / "partial.json"
    profile.write_text(
        json.dumps(
            {
                "shortcuts": {
                    "globals": {
                        "toggle_captions": "",
                        "toggle_overlay": "Ctrl+Alt+J",
                        "obsolete_action": "Ctrl+Alt+K",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = SettingsStore(profile)
    expected = default_bindings() | {"toggle_captions": "", "toggle_overlay": "Ctrl+Alt+J"}
    assert store.settings.shortcuts.globals == expected
    manager = Registrar()
    view = SettingsView(store, SettingsDeps(hotkeys=manager))
    view.shortcut_controller.reload()
    assert view.shortcuts.bindings() == expected
    assert manager.active == {a: s for a, s in expected.items() if s}
    assert "obsolete_action" not in manager.active


@pytest.mark.parametrize("page", ["intelligence", "engine", "processing", "diagnostics"])
def test_technical_pages_remain_gated_and_disabling_advanced_moves_selection(view, page):
    index = view._keys.index(page)
    assert view.nav.item(index).isHidden()
    view.show_section(page)
    assert view.advanced_toggle.isChecked()
    assert view._current_key() == page
    view.advanced_toggle.setChecked(False)
    assert view.nav.item(index).isHidden()
    assert view._current_key()
    assert not view.nav.currentItem().isHidden()
    assert view.stack.currentIndex() == view.nav.currentRow()


def test_search_with_no_visible_result_never_leaves_a_hidden_selected_page(view):
    view.show_section("diagnostics")
    view.search.setText("diagnostics")
    view.advanced_toggle.setChecked(False)
    assert view.nav.currentRow() == -1
    assert view.empty_page.parentWidget().isVisibleTo(view.stack.currentWidget())
    assert view._current_key() == ""
    view.search.clear()
    assert view.nav.currentRow() >= 0
    assert not view.nav.currentItem().isHidden()
    view.search.setText("no such setting 8675309")
    assert view.nav.currentRow() == -1
    view.show_section("shortcuts")
    assert view._current_key() == "shortcuts"
    assert not view.advanced_toggle.isChecked()


def test_edits_clear_reset_takeover_reset_all_and_undo_survive_reload(view, tmp_path):
    editor = view.shortcuts
    manager = view.shortcut_controller.manager
    manager.calls.clear()
    assert editor.propose("toggle_captions", "Ctrl+Alt+J") == ""
    assert len(manager.calls) == 1
    assert manager.active["toggle_captions"] == "Ctrl+Alt+J"
    assert SettingsStore(tmp_path / "settings.json").settings.shortcuts.globals == editor.bindings()
    editor.clear("toggle_overlay")
    assert "toggle_overlay" not in manager.active
    assert (
        SettingsStore(tmp_path / "settings.json").settings.shortcuts.globals["toggle_overlay"] == ""
    )
    editor.reset("toggle_overlay")
    assert manager.active["toggle_overlay"] == "Ctrl+Alt+O"
    assert editor.propose("toggle_overlay", "Ctrl+Alt+J")
    assert editor.propose("toggle_overlay", "Ctrl+Alt+J") == ""
    assert "toggle_captions" not in manager.active
    before = editor.bindings()
    editor.reset_all()
    assert manager.active == default_bindings()
    editor.undo_reset()
    assert editor.bindings() == before
    assert view._store.settings.shortcuts.globals == before
    assert SettingsStore(tmp_path / "settings.json").settings.shortcuts.globals == before


def test_rejected_edit_restores_registration_ui_and_disk_then_retry_applies_it(view, tmp_path):
    manager = view.shortcut_controller.manager
    previous = view.shortcuts.bindings()
    manager.blocked.add("Ctrl+Alt+J")
    view.shortcuts.propose("toggle_captions", "Ctrl+Alt+J")
    assert manager.active == previous
    assert view.shortcuts.bindings() == previous
    assert view._store.settings.shortcuts.globals == previous
    view._store.save()
    assert SettingsStore(tmp_path / "settings.json").settings.shortcuts.globals == previous
    row = view.shortcuts.row("toggle_captions")
    assert row.chip.text() == previous["toggle_captions"]
    assert "wasn't saved" in row.note.text() and "Ctrl+Alt+J" in row.note.text()
    view.refresh()
    assert "wasn't saved" in row.note.text()
    manager.blocked.clear()
    row.retry_button.click()
    assert manager.active["toggle_captions"] == "Ctrl+Alt+J"
    assert row.chip.text() == "Ctrl+Alt+J"
    assert not row.note.text()
    assert SettingsStore(tmp_path / "settings.json").settings.shortcuts.globals == manager.active


def test_failed_reset_all_and_takeover_do_not_discard_other_bindings(view):
    manager = view.shortcut_controller.manager
    view.shortcuts.propose("toggle_captions", "Ctrl+Alt+J")
    before = view.shortcuts.bindings()
    manager.blocked.add("Ctrl+Alt+C")
    view.shortcuts.reset_all()
    assert view.shortcuts.bindings() == before
    assert view.shortcuts.toast.isHidden()
    manager.blocked = {"Ctrl+Alt+J"}
    assert view.shortcuts.propose("toggle_overlay", "Ctrl+Alt+J")
    view.shortcuts.propose("toggle_overlay", "Ctrl+Alt+J")
    assert view.shortcuts.bindings() == before
    assert (
        "toggle_captions" in manager.active
        or "toggle_captions" in view.shortcut_controller.failures
    )


def test_startup_failure_and_external_changes_remain_visible_after_refresh(view):
    manager = view.shortcut_controller.manager
    manager.blocked.add("Ctrl+Alt+C")
    view.shortcut_controller.reload()
    view.refresh()
    assert "toggle_captions" not in manager.active
    assert "Another app" in view.shortcuts.row("toggle_captions").note.text()
    view._store.settings.shortcuts.globals["toggle_captions"] = "Ctrl+Alt+J"
    view._store.notify("shortcuts")
    assert manager.active["toggle_captions"] == "Ctrl+Alt+J"
    assert view.shortcuts.row("toggle_captions").chip.text() == "Ctrl+Alt+J"
    assert not view.shortcuts.row("toggle_captions").note.text()


def test_multiple_unavailable_saved_keys_can_be_cleared_one_at_a_time(view):
    manager = view.shortcut_controller.manager
    manager.blocked.update({"Ctrl+Alt+C", "Ctrl+Alt+O"})
    view.shortcut_controller.reload()
    view.shortcuts.clear("toggle_captions")
    assert view._store.settings.shortcuts.globals["toggle_captions"] == ""
    assert "Another app" in view.shortcuts.row("toggle_overlay").note.text()
    view.shortcuts.clear("toggle_overlay")
    assert view._store.settings.shortcuts.globals["toggle_overlay"] == ""
    assert view.shortcut_controller.failures == {}


def test_unsupported_platform_keeps_the_page_and_explains_disabled_global_controls(view):
    manager = Registrar()
    manager.is_supported = False
    other = SettingsView(view._store, SettingsDeps(hotkeys=manager))
    other.show_section("shortcuts")
    assert "Shortcuts" in visible_pages(other)
    assert "available on Windows" in other.shortcuts.unavailable_note.text()
    assert not other.shortcuts.row("toggle_captions").chip.isEnabled()
    assert not other.shortcuts.reset_all_button.isEnabled()


def test_keyboard_capture_pauses_global_actions_until_primary_key_release(view):
    manager = view.shortcut_controller.manager
    view.show_section("shortcuts")
    view.show()
    row = view.shortcuts.row("toggle_captions")
    row.chip.setFocus()
    QTest.keyClick(row.chip, Qt.Key.Key_Return)
    assert row.chip.recording
    assert not manager.active
    QTest.keyPress(row.chip, Qt.Key.Key_J, CTRL_ALT)
    assert not manager.active
    QTest.keyRelease(row.chip, Qt.Key.Key_J, CTRL_ALT)
    assert not row.chip.recording
    assert manager.active["toggle_captions"] == "Ctrl+Alt+J"
    QTest.keyClick(row.chip, Qt.Key.Key_Space)
    QTest.keyClick(row.chip, Qt.Key.Key_Escape)
    assert manager.active["toggle_captions"] == "Ctrl+Alt+J"
    assert view.shortcuts.bindings()["toggle_captions"] == "Ctrl+Alt+J"


def test_escape_after_conflict_and_reset_conflict_leave_existing_bindings_alone(qt_app):
    editor = ShortcutEditor()
    editor.propose("toggle_captions", "Ctrl+Alt+J")
    editor.propose("toggle_overlay", "Ctrl+Alt+C")
    before = editor.bindings()
    editor.reset("toggle_captions")
    row = editor.row("toggle_captions")
    assert row.chip.recording and "Also used" in row.note.text()
    QTest.keyClick(row.chip, Qt.Key.Key_Escape)
    assert editor.bindings() == before
    assert not row.chip.recording


def test_hiding_or_leaving_capture_cancels_without_overwriting(view):
    view.show()
    view.show_section("shortcuts")
    before = view.shortcuts.bindings()
    view.shortcuts.start_recording("toggle_overlay")
    view.show_section("general")
    QApplication.processEvents()
    assert not view.shortcut_controller.suspended
    assert view.shortcuts.bindings() == before
    assert view.shortcut_controller.manager.active == before


def test_captured_window_shortcut_cannot_activate_its_normal_action(qt_app):
    shell = QWidget()
    editor = ShortcutEditor(parent=shell)
    close_shortcut = QShortcut("Ctrl+W", shell)
    calls = []
    close_shortcut.activated.connect(lambda: calls.append(True))
    shell.show()
    editor.start_recording("toggle_captions")
    QTest.keyClick(
        editor.row("toggle_captions").chip, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier
    )
    assert editor.bindings()["toggle_captions"] == "Ctrl+W"
    assert not calls


@pytest.mark.parametrize("sequence", ["Ctrl+F12", "Ctrl+Alt+F12", "AltGr+Q", "Numpad+1"])
def test_unsupported_combinations_are_explained_without_overwriting(qt_app, sequence):
    editor = ShortcutEditor()
    before = editor.bindings()
    assert not is_valid_global(sequence)[0]
    assert editor.propose("toggle_captions", sequence)
    assert editor.bindings() == before


def test_numpad_capture_does_not_silently_bind_the_number_row(qt_app):
    editor = ShortcutEditor()
    editor.start_recording("toggle_captions")
    chip = editor.row("toggle_captions").chip
    modifiers = CTRL_ALT | Qt.KeyboardModifier.KeypadModifier
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(chip, QKeyEvent(kind, Qt.Key.Key_1, modifiers))
    assert editor.bindings()["toggle_captions"] == "Ctrl+Alt+C"
    assert "recognise" in editor.row("toggle_captions").note.text()


def test_invalid_saved_binding_is_visible_with_a_reason_and_can_be_cleared(
    qt_app, tmp_path, monkeypatch
):
    store = SettingsStore(tmp_path / "settings.json")
    store.settings.shortcuts.globals["toggle_captions"] = "Ctrl+Alt"
    manager = HotkeyManager()
    monkeypatch.setattr(manager, "is_supported", True)
    monkeypatch.setattr(manager, "_start", lambda wanted: {})
    view = SettingsView(store, SettingsDeps(hotkeys=manager))
    view.shortcut_controller.reload()
    row = view.shortcuts.row("toggle_captions")
    assert row.chip.text() == "Ctrl+Alt"
    assert "modifiers alone" in row.note.text()
    view.shortcuts.clear("toggle_captions")
    assert row.chip.text() == "Not set"
    assert (
        SettingsStore(tmp_path / "settings.json").settings.shortcuts.globals["toggle_captions"]
        == ""
    )


def test_queued_global_activation_is_discarded_after_unregister(qt_app):
    manager = HotkeyManager()
    fired = []
    manager.activated.connect(fired.append)
    manager._fired.emit(manager._generation, "toggle_captions")
    manager.unregister_all()
    QApplication.processEvents()
    assert fired == []


def test_registration_timeout_cancels_late_start_and_can_recover(qt_app, monkeypatch):
    from subbyai.system import hotkeys

    manager = HotkeyManager()
    monkeypatch.setattr(manager, "is_supported", True)
    monkeypatch.setattr(hotkeys, "_START_TIMEOUT", 0)
    entered = []

    def delayed_start(state):
        entered.append(state)
        assert state.stopped.wait(2), "timed-out registration was not cancelled"
        state.ready.set()

    monkeypatch.setattr(manager, "_run", delayed_start)
    failures = manager.register_all({"captions": "Ctrl+Alt+J"})
    assert set(failures) == {"captions"}
    assert manager.registered_bindings == {}
    assert entered and entered[0].stopped.is_set()
    assert manager.unregister_all()
    assert manager.register_all({"captions": ""}) == {}


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_onboarding_points_to_reachable_shortcuts_and_explains_platform(
    qt_app, tmp_path, monkeypatch, platform
):
    from subbyai.ui import onboarding_steps

    monkeypatch.setattr(onboarding_steps, "sys", SimpleNamespace(platform=platform))
    store = SettingsStore(tmp_path / "settings.json")
    ready = ReadyStep(store.settings)
    text = " ".join(label.text() for label in ready.findChildren(QLabel))
    assert "Settings → Shortcuts" in text
    view = SettingsView(store)
    view.show_section("shortcuts")
    assert not view.nav.currentItem().isHidden()
    assert not view.advanced_toggle.isChecked()
    if platform == "darwin":
        assert "available on Windows" in text
        assert "Ctrl+Alt+C" not in text


def test_help_shortcuts_match_installed_accelerators(qt_app, store, tmp_path):
    shell = Shell(SettingsStore(tmp_path / "settings.json").settings)
    transcript = TranscriptView(store)
    assert {s.key().toString() for s in shell.findChildren(QShortcut)} == {
        action.default for action in SHELL_ACTIONS
    }
    assert {s.key().toString() for s in transcript.findChildren(QShortcut)} == {
        QKeySequence(action.default).toString() for action in TRANSCRIPT_ACTIONS
    }
    assert "Ctrl+Space" not in {a.default for a in (*SHELL_ACTIONS, *TRANSCRIPT_ACTIONS)}


@pytest.mark.parametrize("text_scale", [1.0, 1.25, 1.5])
def test_shortcut_controls_fit_narrow_settings_with_larger_text(view, qt_app, text_scale):
    theme.apply(qt_app, "dark", text_scale=text_scale, reduce_motion=True)
    view.resize(640, 460)
    view.show_section("shortcuts")
    view.show()
    QApplication.processEvents()
    area = view.stack.currentWidget()
    assert area.horizontalScrollBar().maximum() == 0
    for action, row in view.shortcuts._rows.items():
        assert row.chip.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert row.chip.accessibleName().endswith(row.title)
        assert not row.reset_button.isHidden()
        assert row.reset_button.accessibleName().endswith(row.title)
        assert row.reset_button.width() >= row.reset_button.minimumSizeHint().width(), action
        assert row.chip.width() >= row.chip.minimumSizeHint().width(), action
    theme.apply(qt_app, "dark", text_scale=1.0)
