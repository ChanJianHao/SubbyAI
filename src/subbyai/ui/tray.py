"""Tray icon: the app's real home.

Most sessions are "start captions, watch something, stop" — the window is
optional. The tray therefore carries the full quick-control set, and its state
must always tell the truth about whether captions are running.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ..branding import APP_FULL_NAME, APP_NAME
from ..core.health import HealthState
from ..core.settings import OverlayPreset
from . import theme
from .widgets import PRESET_LABELS


class Tray(QObject):
    toggle_captions = Signal()
    toggle_overlay = Signal()
    toggle_click_through = Signal()
    preset_selected = Signal(object)
    open_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon = QSystemTrayIcon(theme.app_icon(), self)
        self._icon.setToolTip(APP_FULL_NAME)
        self._menu = QMenu()
        self._build_menu()
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        self._header = QAction("Off", self._menu)
        self._header.setEnabled(False)
        self._menu.addAction(self._header)
        self._menu.addSeparator()

        self._toggle_action = QAction("Start captions", self._menu)
        self._toggle_action.triggered.connect(self.toggle_captions)
        self._menu.addAction(self._toggle_action)

        self._overlay_action = QAction("Show captions", self._menu)
        self._overlay_action.setCheckable(True)
        self._overlay_action.setChecked(True)
        self._overlay_action.triggered.connect(self.toggle_overlay)
        self._menu.addAction(self._overlay_action)

        self._click_action = QAction("Click-through", self._menu)
        self._click_action.setCheckable(True)
        self._click_action.triggered.connect(self.toggle_click_through)
        self._menu.addAction(self._click_action)

        style_menu = self._menu.addMenu("Caption style")
        self._preset_group = QActionGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_actions: dict[OverlayPreset, QAction] = {}
        for preset, label in PRESET_LABELS.items():
            action = QAction(label, style_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, p=preset: self.preset_selected.emit(p))
            self._preset_group.addAction(action)
            style_menu.addAction(action)
            self._preset_actions[preset] = action

        self._menu.addSeparator()
        open_action = QAction(f"Open {APP_NAME}", self._menu)
        open_action.triggered.connect(self.open_requested)
        self._menu.addAction(open_action)

        quit_action = QAction(f"Quit {APP_NAME}", self._menu)
        quit_action.triggered.connect(self.quit_requested)
        self._menu.addAction(quit_action)

    # ---------- state ----------

    def show(self) -> None:
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def set_icon(self, icon: QIcon) -> None:
        self._icon.setIcon(icon)

    def set_health(self, state: HealthState, quality_label: str = "") -> None:
        running = state.is_running and state is not HealthState.OFF
        summary = {
            HealthState.OFF: "Off",
            HealthState.STARTING: "Starting…",
            HealthState.DOWNLOADING: "Downloading…",
            HealthState.LISTENING: "Listening",
            HealthState.SILENT: "No sound",
            HealthState.SOUND_NO_SPEECH: "Listening",
            HealthState.DELAYED: "Running behind",
            HealthState.ERROR: "Needs attention",
        }.get(state, "")
        detail = f"{summary} — {quality_label}" if quality_label and running else summary
        self._header.setText(detail)
        self._toggle_action.setText("Stop captions" if running else "Start captions")
        self._icon.setToolTip(f"{APP_NAME} — {summary}")

    def set_overlay_visible(self, visible: bool) -> None:
        self._overlay_action.setChecked(visible)

    def set_click_through(self, enabled: bool) -> None:
        self._click_action.setChecked(enabled)

    def set_preset(self, preset: OverlayPreset) -> None:
        action = self._preset_actions.get(preset)
        if action is not None:
            action.setChecked(True)

    def notify(self, title: str, message: str) -> None:
        """A tray balloon — used for things that must not interrupt fullscreen."""
        self._icon.showMessage(title, message, theme.app_icon(), 5000)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_requested.emit()
