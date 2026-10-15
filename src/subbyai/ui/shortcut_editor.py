"""Rebinding shortcuts without ever opening a dialog.

Every refusal — an unusable combination, a clash with another action, a
combination another app already owns — is written next to the row that caused
it. A modal would take focus, and this app's first law is that nothing steals
focus from whatever the user is watching.

Validation is delegated to the pure helpers in ``system.hotkeys`` so the editor
and the registrar can never disagree about what a valid shortcut is.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.shortcuts import GLOBAL_ACTIONS, SHELL_ACTIONS, TRANSCRIPT_ACTIONS, ShortcutAction
from ..system.hotkeys import find_conflicts, is_valid_global, normalize_sequence
from . import theme
from .settings_widgets import Group, Toast, hint_label
from .settings_widgets.common import SettingRow
from .settings_widgets.key_cap import KeyCap
from .tokens import SPACE

__all__ = ["APP_SHORTCUTS", "GLOBAL_ACTIONS", "ShortcutAction", "ShortcutEditor"]


APP_SHORTCUTS = tuple((a.label, a.default) for a in (*SHELL_ACTIONS, *TRANSCRIPT_ACTIONS))

_UNSET = "Not set"
_RECORDING_HINT = "Esc cancels · Backspace clears"
_TAKE_OVER = "Press the same keys again to take it over."


class _ShortcutRow(QWidget):
    """One action: name, key-cap, per-row reset, and its own note line."""

    record_requested = Signal(str)
    reset_requested = Signal(str)
    retry_requested = Signal(str)

    def __init__(self, action: ShortcutAction, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action = action
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE["xs"])

        controls = QWidget(self)
        line = QHBoxLayout(controls)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(SPACE["sm"])
        self.chip = KeyCap(self)
        self.chip.setAccessibleName(f"Change shortcut: {action.label}")
        self.chip.setToolTip("Select to change. Esc cancels; Backspace clears.")
        self.chip.setAccessibleDescription(self.chip.toolTip())
        self.chip.clicked.connect(lambda: self.record_requested.emit(action.id))
        self.reset_button = QPushButton("↺", self)
        self.reset_button.setObjectName("quiet")
        self.reset_button.setToolTip(f"Reset to {action.default}")
        self.reset_button.setAccessibleName(f"Reset shortcut: {action.label}")
        self.reset_button.setAccessibleDescription(self.reset_button.toolTip())
        self.reset_button.setMinimumWidth(30)
        self.reset_button.clicked.connect(lambda: self.reset_requested.emit(action.id))
        self.retry_button = QPushButton("Try again", self)
        self.retry_button.setObjectName("quiet")
        self.retry_button.setAccessibleName(f"Retry shortcut: {action.label}")
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(lambda: self.retry_requested.emit(action.id))
        line.addWidget(self.chip, 0)
        line.addWidget(self.reset_button, 0)
        column.addWidget(SettingRow(action.label, controls))

        self.note = hint_label("")
        self.note.setVisible(False)
        column.addWidget(self.note)
        column.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)

    @property
    def title(self) -> str:
        return self.action.label

    def set_sequence(self, sequence: str, orphaned: bool = False) -> None:
        self.chip.setText(sequence or _UNSET)
        self.chip.setAccessibleDescription(
            f"{sequence or _UNSET}. Select to change. Esc cancels; Backspace clears."
        )
        self.chip.set_recording(False)
        self.chip.set_tone("warn" if orphaned else "normal")
        if orphaned:
            self.show_note("This one lost its keys. Pick new ones.", tone="warn")

    def show_note(self, text: str, tone: str = "warn") -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))
        self.chip.setAccessibleDescription(f"{self.chip.text()}. {text}")
        self.chip.set_tone(tone)

    def clear_note(self) -> None:
        self.note.setText("")
        self.note.setVisible(False)
        self.retry_button.setVisible(False)
        if not self.chip.recording:
            self.chip.set_tone("normal")

    def show_failure(self, message: str) -> None:
        self.show_note(message, tone="bad")
        self.retry_button.setVisible(True)


class ShortcutEditor(QWidget):
    """Editable global shortcuts and fixed window/transcript accelerators."""

    bindings_changed = Signal(dict)
    capture_changed = Signal(bool)
    retry_requested = Signal()

    def __init__(
        self, bindings: dict[str, str] | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._bindings: dict[str, str] = {a.id: a.default for a in GLOBAL_ACTIONS}
        self._rows: dict[str, _ShortcutRow] = {}
        self._recording: str = ""
        self._pending: tuple[str, str] | None = None
        self._undo: dict[str, str] | None = None
        self._failures: dict[str, str] = {}

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        globals_group = Group(
            "Global shortcuts",
            "Work even while another app is active. Select a key combination to change it; "
            "press Esc to cancel or Backspace to clear.",
        )
        self.unavailable_note = hint_label("")
        self.unavailable_note.hide()
        globals_group.add(self.unavailable_note)
        for action in GLOBAL_ACTIONS:
            row = _ShortcutRow(action, self)
            row.record_requested.connect(self.start_recording)
            row.reset_requested.connect(self.reset)
            row.retry_requested.connect(lambda _id: self.retry_requested.emit())
            row.chip.captured.connect(lambda chord, i=action.id: self._on_captured(i, chord))
            row.chip.cleared.connect(lambda i=action.id: self._on_cleared(i))
            row.chip.cancelled.connect(lambda i=action.id: self._on_cancelled(i))
            self._rows[action.id] = row
            globals_group.add(row)

        self.toast = Toast(self)
        self.toast.action_clicked.connect(self.undo_reset)
        globals_group.add(self.toast)
        self.reset_all_button = QPushButton("Reset all shortcuts", self)
        self.reset_all_button.setObjectName("quiet")
        self.reset_all_button.clicked.connect(self.reset_all)
        globals_group.add(self.reset_all_button)
        column.addWidget(globals_group)

        for title, actions in (
            ("While SubbyAI is focused", SHELL_ACTIONS),
            ("While a saved transcript is focused", TRANSCRIPT_ACTIONS),
        ):
            in_app = Group(title, "Built-in shortcuts · not editable.")
            for action in actions:
                label = QLabel(action.default)
                label.setAccessibleName(f"{action.label}: {action.default}")
                label.setFont(self._rows["toggle_captions"].chip.font())
                in_app.add_row(action.label, label)
            column.addWidget(in_app)

        self.set_bindings(bindings or {})
        self.refresh_theme()

    # ---------- state ----------

    def bindings(self) -> dict[str, str]:
        return dict(self._bindings)

    def set_bindings(self, bindings: dict[str, str]) -> None:
        """Load stored bindings without announcing them back to the caller."""
        self.cancel_recording()
        for action in GLOBAL_ACTIONS:
            stored = bindings.get(action.id, action.default)
            self._bindings[action.id] = (normalize_sequence(stored) or stored) if stored else ""
        clashes = find_conflicts(self._bindings)
        owners = {a for actions in clashes.values() for a in actions}
        for action_id, row in self._rows.items():
            row.set_sequence(self._bindings[action_id])
            if action_id in owners:
                row.show_note("Two actions share these keys. Give one of them new keys.")
            else:
                row.clear_note()

    def row(self, action_id: str) -> _ShortcutRow | None:
        return self._rows.get(action_id)

    def set_failures(self, failures: dict[str, str]) -> None:
        """Show what the system refused to register, per row."""
        self._failures = dict(failures)
        if failures:
            self.toast.hide()
        for action_id, row in self._rows.items():
            message = failures.get(action_id)
            if message:
                row.show_failure(message)
            elif not row.chip.recording:
                row.clear_note()

    def set_unavailable(self, reason: str) -> None:
        self.unavailable_note.setText(reason)
        self.unavailable_note.show()
        for row in self._rows.values():
            row.chip.setEnabled(False)
            row.reset_button.setEnabled(False)
        self.reset_all_button.setEnabled(False)

    # ---------- editing ----------

    def start_recording(self, action_id: str) -> None:
        row = self._rows.get(action_id)
        if row is None:
            return
        self.cancel_recording()
        self._recording = action_id
        self.capture_changed.emit(True)
        row.clear_note()
        row.chip.set_recording(True)
        row.show_note(_RECORDING_HINT, tone="recording")

    def cancel_recording(self) -> None:
        action_id, self._recording = self._recording, ""
        row = self._rows.get(action_id)
        if row is None:
            return
        self._pending = None
        row.set_sequence(self._bindings.get(action_id, ""))
        row.clear_note()
        if action_id in self._failures:
            row.show_failure(self._failures[action_id])
        self.capture_changed.emit(False)

    def propose(self, action_id: str, sequence: str) -> str:
        """Try to bind a chord. Returns "" when accepted, else why it was not.

        A clash is refused once and explained; the identical chord offered again
        is read as "yes, take it", which is the whole point of the inline flow.
        """
        row = self._rows.get(action_id)
        if row is None:
            return ""
        canonical = normalize_sequence(sequence)
        ok, reason = is_valid_global(canonical or sequence)
        if not ok:
            self._pending = None
            row.show_note(reason)
            return reason

        holder = self._holder_of(canonical, exclude=action_id)
        if holder is not None and self._pending != (action_id, canonical):
            self._pending = (action_id, canonical)
            clash = f'Also used by "{self._rows[holder].title}". {_TAKE_OVER}'
            row.show_note(clash)
            return clash

        if holder is not None:
            self._bindings[holder] = ""
            self._rows[holder].set_sequence("", orphaned=True)
        self._pending = None
        self._recording = ""
        self._bindings[action_id] = canonical
        row.set_sequence(canonical)
        row.clear_note()
        self._announce()
        self.capture_changed.emit(False)
        return ""

    def clear(self, action_id: str) -> None:
        if action_id not in self._bindings:
            return
        self._pending = None
        self._recording = ""
        self._bindings[action_id] = ""
        self._rows[action_id].set_sequence("")
        self._rows[action_id].clear_note()
        self._announce()
        self.capture_changed.emit(False)

    def reset(self, action_id: str) -> None:
        self.cancel_recording()
        action = next((a for a in GLOBAL_ACTIONS if a.id == action_id), None)
        if action is None:
            return
        holder = self._holder_of(action.default, exclude=action_id)
        if holder is not None:
            self.start_recording(action_id)
        self.propose(action_id, action.default)

    def reset_all(self) -> None:
        self._undo = dict(self._bindings)
        self.set_bindings({a.id: a.default for a in GLOBAL_ACTIONS})
        self.toast.show_message("Shortcuts reset", "Undo", seconds=8)
        self._announce()

    def undo_reset(self) -> None:
        if self._undo is None:
            return
        restored, self._undo = self._undo, None
        self.set_bindings(restored)
        self._announce()

    def refresh_theme(self) -> None:
        self.setStyleSheet(
            "QPushButton#quiet { border: 2px solid transparent; padding: 4px 6px; }"
            f"QPushButton#quiet:focus {{ border-color: {theme.current().focus}; }}"
        )

    # ---------- internals ----------

    def _on_captured(self, action_id: str, chord: str) -> None:
        if self._recording == action_id:
            self.propose(action_id, chord)

    def _on_cleared(self, action_id: str) -> None:
        if self._recording == action_id:
            self.clear(action_id)

    def _on_cancelled(self, action_id: str) -> None:
        if self._recording == action_id:
            self.cancel_recording()

    def _holder_of(self, sequence: str, exclude: str) -> str | None:
        canonical = normalize_sequence(sequence)
        if not canonical:
            return None
        for action_id, bound in self._bindings.items():
            if action_id != exclude and normalize_sequence(bound) == canonical:
                return action_id
        return None

    def _announce(self) -> None:
        self.bindings_changed.emit(dict(self._bindings))

    def hideEvent(self, event) -> None:
        self.cancel_recording()
        super().hideEvent(event)
