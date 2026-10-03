"""Which output gets captioned, with a meter that proves it.

The device list is the honest picker from the spec: the level meter sits beside
the chosen row, so "is it hearing anything" is answered on the same screen where
the choice is made instead of in a support thread.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import SettingsStore
from .settings_languages import RECONNECT_NOTE
from .settings_widgets import Group, LevelMeter, SettingsSection, hint_label
from .tokens import SPACE
from .widgets import compact_combo

__all__ = ["AudioSection"]

SENSITIVITIES: tuple[tuple[str, str], ...] = (
    ("Low", "low"),
    ("Standard", "standard"),
    ("High", "high"),
)


class _DeviceRow(QWidget):
    """One output, with room for the meter when it is the chosen one."""

    def __init__(self, device: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device_id = str(getattr(device, "id", ""))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["sm"])
        name = str(getattr(device, "name", "")) or "Unnamed output"
        if getattr(device, "is_default", False):
            name = f"{name} (your usual output)"
        self.button = QRadioButton(name, self)
        row.addWidget(self.button, 1)
        self._row = row

    def attach(self, meter: QWidget) -> None:
        self._row.addWidget(meter, 0)
        meter.setVisible(True)


class AudioSection(SettingsSection):
    """The output being listened to, and how picky speech detection is."""

    restart_capture_requested = Signal()

    def __init__(self, store: SettingsStore, deps: Any = None, parent: QWidget | None = None):
        super().__init__(store, parent)
        self._deps = deps
        self._rows: list[_DeviceRow] = []
        self.meter = LevelMeter(self)
        self._group = QButtonGroup(self)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        devices = Group(
            "Listen to",
            f"Whatever this output is playing is what gets captioned. {RECONNECT_NOTE}",
        )
        self.device_box = QWidget(self)
        self.device_layout = QVBoxLayout(self.device_box)
        self.device_layout.setContentsMargins(0, 0, 0, 0)
        self.device_layout.setSpacing(SPACE["sm"])
        devices.add(self.device_box)
        self.empty_note = hint_label("")
        self.empty_note.setVisible(False)
        devices.add(self.empty_note)
        self.rescan_button = QPushButton("Look again", self)
        self.rescan_button.clicked.connect(self.refresh)
        devices.add(self.rescan_button)
        column.addWidget(devices)

        speech = Group("Speech")
        self.sensitivity = compact_combo(QComboBox(self))
        for label, value in SENSITIVITIES:
            self.sensitivity.addItem(label, value)
        self.sensitivity.currentIndexChanged.connect(self._on_sensitivity)
        speech.add_row(
            "Ignore short noises",
            self.sensitivity,
            "Higher skips clicks, keystrokes and other brief sounds, and may skip "
            "very short words with them.",
        )
        column.addWidget(speech)
        self.refresh()

    def set_level(self, level: float) -> None:
        self.meter.set_level(level)

    def refresh(self) -> None:
        with self.quiet():
            index = self.sensitivity.findData(self.settings.captions.speech_sensitivity)
            self.sensitivity.setCurrentIndex(max(0, index))
        self._rebuild_devices()

    def _rebuild_devices(self) -> None:
        for row in self._rows:
            self._group.removeButton(row.button)
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        self.meter.setParent(self)
        self.meter.setVisible(False)

        devices, problem = self._list_devices()
        self.empty_note.setText(problem)
        self.empty_note.setVisible(bool(problem))
        for device in devices:
            row = _DeviceRow(device, self.device_box)
            self._group.addButton(row.button)
            row.button.clicked.connect(
                lambda _checked=False, device_id=row.device_id: self._on_device(device_id)
            )
            self.device_layout.addWidget(row)
            self._rows.append(row)

        default_id = next(
            (str(getattr(d, "id", "")) for d in devices if getattr(d, "is_default", False)), ""
        )
        selected = (
            _find(self._rows, self.settings.audio.device_id)
            or _find(self._rows, default_id)
            or (self._rows[0] if self._rows else None)
        )
        if selected is not None:
            selected.button.setChecked(True)
            selected.attach(self.meter)

    def _list_devices(self) -> tuple[list, str]:
        """Devices, plus the one sentence to show when there are none."""
        lister = getattr(self._deps, "audio_devices", None)
        if lister is None:
            return [], "We can't see your audio devices from here yet."
        try:
            devices = list(lister())
        except Exception as exc:
            return [], getattr(exc, "message", "") or "We couldn't read your audio devices."
        if not devices:
            return [], "No audio outputs turned up. Start something playing and look again."
        return devices, ""

    def _on_device(self, device_id: str) -> None:
        row = _find(self._rows, device_id)
        if row is None:
            return
        self.settings.audio.device_id = device_id or None
        self.settings.audio.device_name = row.button.text()
        row.attach(self.meter)
        self.apply("audio")
        if not self._muted:
            self.restart_capture_requested.emit()

    def _on_sensitivity(self, index: int) -> None:
        self.settings.captions.speech_sensitivity = str(
            self.sensitivity.itemData(index) or "standard"
        )
        self.apply("captions")


def _find(rows: list[_DeviceRow], device_id: str | None) -> _DeviceRow | None:
    if not device_id:
        return None
    return next((row for row in rows if row.device_id == device_id), None)
