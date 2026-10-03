"""The mono chip that shows a shortcut, and captures a new one.

While recording, the chip claims ``ShortcutOverride`` so the chord being typed
belongs to it rather than to any in-window accelerator — otherwise pressing
Ctrl+W to bind it would close the window instead.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QPushButton, QWidget

from .. import theme
from ..tokens import RADIUS

__all__ = ["RECORDING_LABEL", "KeyCap", "chord_from_event"]

RECORDING_LABEL = "Press keys…"

_MODIFIER_KEYS = frozenset(
    {
        int(Qt.Key.Key_Control),
        int(Qt.Key.Key_Alt),
        int(Qt.Key.Key_AltGr),
        int(Qt.Key.Key_Shift),
        int(Qt.Key.Key_Meta),
        int(Qt.Key.Key_CapsLock),
        int(Qt.Key.Key_NumLock),
    }
)


def chord_from_event(event) -> str:
    """The chord a key-down describes, or "" when it is only modifiers."""
    key = int(event.key())
    if key in _MODIFIER_KEYS:
        return ""
    modifiers = event.modifiers()
    parts = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        parts.append("Ctrl")
    if modifiers & Qt.KeyboardModifier.AltModifier:
        parts.append("Alt")
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        parts.append("Shift")
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        parts.append("Win")
    name = QKeySequence(key).toString()
    if not name:
        return ""
    parts.append(name)
    return "+".join(parts)


class KeyCap(QPushButton):
    """Shows one binding; captures a replacement while ``recording``."""

    captured = Signal(str)
    cleared = Signal()
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        # Set before anything that can dispatch an event: ``event`` runs during
        # construction and reads both of these.
        self._recording = False
        self._tone = "normal"
        super().__init__(parent)
        self.setFont(theme.mono_font(12))
        self.setMinimumWidth(132)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_theme()

    @property
    def recording(self) -> bool:
        return self._recording

    def set_recording(self, on: bool) -> None:
        self._recording = on
        if on:
            self.setText(RECORDING_LABEL)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.set_tone("recording" if on else "normal")

    def set_tone(self, tone: str) -> None:
        """``normal`` / ``recording`` / ``warn`` / ``bad``."""
        self._tone = tone
        self.refresh_theme()

    def refresh_theme(self) -> None:
        palette = theme.current()
        border, colour = {
            "recording": (palette.accent, palette.text),
            "warn": (palette.warning, palette.warning),
            "bad": (palette.error, palette.error),
        }.get(self._tone, (palette.hairline, palette.text))
        self.setStyleSheet(
            f"QPushButton {{ background: {palette.input_bg}; color: {colour};"
            f" border: 1px solid {border}; border-radius: {RADIUS['sm']}px;"
            " padding: 6px 12px; }"
            f"QPushButton:hover {{ border-color: {palette.accent}; }}"
        )

    def event(self, event) -> bool:
        if self._recording and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = int(event.key())
        if key == int(Qt.Key.Key_Escape):
            self.cancelled.emit()
            return
        if key == int(Qt.Key.Key_Backspace) and not event.modifiers():
            self.cleared.emit()
            return
        chord = chord_from_event(event)
        if chord:
            self.captured.emit(chord)

    def focusOutEvent(self, event) -> None:
        if self._recording:
            self.cancelled.emit()
        super().focusOutEvent(event)
