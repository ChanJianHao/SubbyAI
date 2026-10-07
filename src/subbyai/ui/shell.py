"""The main window: a segmented shell around Live, History and Settings.

Three destinations, one of which is the point. A sidebar would announce
"dashboard"; this app is a captioner whose window you should be able to ignore
for a week at a time.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..branding import APP_FULL_NAME, APP_NAME
from ..core.health import HealthReport, HealthState
from ..core.settings import Settings
from . import theme
from .motion import MotionStack
from .motion_widgets import SelectionMarker
from .tokens import SPACE
from .widgets import fit_to_screen

log = logging.getLogger(__name__)

WINDOW_SIZE = (880, 620)
#: Honest because every pane inside scrolls and every control shrinks or
#: elides; tests/test_layout.py fails if that stops being true.
WINDOW_MINIMUM = (640, 460)

_SEGMENTS = ("Live", "History", "Settings")

_STATUS_DOTS = {
    HealthState.OFF: ("Off", "text_tertiary"),
    HealthState.STARTING: ("Starting", "warning"),
    HealthState.DOWNLOADING: ("Downloading", "warning"),
    HealthState.LISTENING: ("Listening", "success"),
    HealthState.SILENT: ("No sound", "warning"),
    HealthState.SOUND_NO_SPEECH: ("Listening", "success"),
    HealthState.DELAYED: ("Behind", "warning"),
    HealthState.ERROR: ("Stopped", "error"),
}


class Shell(QMainWindow):
    """Hosts the three surfaces. Owns no product logic."""

    segment_changed = Signal(int)
    close_to_tray_requested = Signal()
    quit_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._quitting = False
        self._health_report = HealthReport(HealthState.OFF)

        self.setWindowTitle(APP_FULL_NAME)
        self.setWindowIcon(theme.app_icon())
        self.setMinimumSize(*WINDOW_MINIMUM)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.stack = MotionStack()
        root.addWidget(self.stack, stretch=1)
        self.setCentralWidget(central)

        # Sized once the surfaces exist (add_surface) or from the saved
        # geometry; fit_to_screen keeps it inside the display either way.
        fit_to_screen(self, *WINDOW_SIZE)
        self._restore_geometry()
        self._install_shortcuts()
        theme.subscribe(self._theme_changed)

    def _theme_changed(self, _palette) -> None:
        self.set_health(self._health_report)

    # ---------- construction ----------

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(56)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["lg"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])
        from .mascot import Mochi

        layout.addWidget(Mochi(header, size=38))

        brand = QLabel(APP_NAME)
        brand.setObjectName("heading")
        layout.addWidget(brand)
        layout.addStretch(1)

        self._segment_group = QButtonGroup(self)
        self._segment_group.setExclusive(True)
        segments = QWidget()
        segment_layout = QHBoxLayout(segments)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(2)
        for index, name in enumerate(_SEGMENTS):
            button = QPushButton(name)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setChecked(index == 0)
            self._segment_group.addButton(button, index)
            segment_layout.addWidget(button)
        self._segment_group.idClicked.connect(self.show_segment)
        self._segment_marker = SelectionMarker(segments)
        self._segment_marker.select(self._segment_group.button(0))
        layout.addWidget(segments)
        layout.addStretch(1)

        self._status_dot = QLabel("●")
        self._status_text = QLabel("Off")
        self._status_text.setObjectName("secondary")
        layout.addWidget(self._status_dot)
        layout.addWidget(self._status_text)
        return header

    def add_surface(self, widget: QWidget) -> None:
        self.stack.addWidget(widget)
        # A surface can raise the window's content minimum, so re-check the
        # fit each time one arrives rather than trusting the constructor.
        if not self._settings.general.window_geometry:
            fit_to_screen(self, *WINDOW_SIZE)

    def _install_shortcuts(self) -> None:
        for index, key in enumerate(("Ctrl+1", "Ctrl+2", "Ctrl+3")):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda i=index: self.show_segment(i))
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.show_segment(2))
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.request_quit)

    # ---------- navigation ----------

    def show_segment(self, index: int) -> None:
        if index < 0 or index >= self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        button = self._segment_group.button(index)
        if button is not None:
            button.setChecked(True)
            self._segment_marker.select(button)
        self.segment_changed.emit(index)

    @property
    def current_segment(self) -> int:
        return self.stack.currentIndex()

    # ---------- status ----------

    def set_health(self, report: HealthReport) -> None:
        self._health_report = report
        text, colour_name = _STATUS_DOTS.get(report.state, ("", "text_tertiary"))
        palette = theme.current()
        colour = getattr(palette, colour_name, palette.text_tertiary)
        self._status_dot.setStyleSheet(f"color: {colour};")
        self._status_text.setText(text)

    # ---------- window lifecycle ----------

    def request_quit(self) -> None:
        self._quitting = True
        self.quit_requested.emit()

    def _restore_geometry(self) -> None:
        """Restore size and position, but only onto a screen that still exists."""
        saved = self._settings.general.window_geometry
        if len(saved) != 4:
            return
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication

        rect = QRect(*saved)
        matching = [s for s in QGuiApplication.screens() if s.availableGeometry().intersects(rect)]
        if matching:
            def intersection_area(screen):
                intersection = screen.availableGeometry().intersected(rect)
                return intersection.width() * intersection.height()

            screen = max(matching, key=intersection_area)
            fit_to_screen(self, rect.width(), rect.height(), screen=screen)
            available = screen.availableGeometry()
            self.move(
                max(available.left() + 8, min(rect.x(), available.right() - self.width() - 7)),
                max(available.top() + 8, min(rect.y(), available.bottom() - self.height() - 31)),
            )

    def closeEvent(self, event: QCloseEvent) -> None:  # Qt API casing
        self._settings.general.window_geometry = [self.x(), self.y(), self.width(), self.height()]
        if self._settings.general.close_to_tray and not self._quitting:
            event.ignore()
            self.hide()
            self.close_to_tray_requested.emit()
            return
        event.accept()
        self.quit_requested.emit()
