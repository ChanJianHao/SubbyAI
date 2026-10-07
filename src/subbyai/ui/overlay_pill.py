"""The overlay's hover controls.

A plain child widget rather than a second window on purpose: another top-level
window would join the z-order fight the overlay already has to win against
borderless-fullscreen games, and could take the focus the overlay is sworn never
to take. Every button is focus-less for the same reason.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QPushButton, QWidget

from ..core.settings import OverlayPreset
from .motion import Tween
from .tokens import DARK, OVERLAY_PRESETS, SPACE

_GLASS = OVERLAY_PRESETS[OverlayPreset.GLASS]
# Denser than the panel: the pill sits over captions, not over video.
_PILL_BG = (*_GLASS.background[:3], 217)
_HIT = 32


class ControlPill(QWidget):
    """Pin / scrub / live / style / hide, revealed on hover dwell."""

    pin_toggled = Signal(bool)
    scrub_back_requested = Signal()
    scrub_forward_requested = Signal()
    live_requested = Signal()
    style_requested = Signal()
    hide_requested = Signal()

    HEIGHT = _HIT + SPACE["sm"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(_stylesheet())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["xs"], SPACE["xs"], SPACE["xs"], SPACE["xs"])
        layout.setSpacing(0)

        self._pin = self._button("⊙", "Keep captions on screen", checkable=True)
        self._pin.toggled.connect(self.pin_toggled)
        layout.addWidget(self._pin)

        back = self._button("‹", "Earlier captions")  # noqa: RUF001 - a chevron, not "<"
        back.clicked.connect(self.scrub_back_requested)
        layout.addWidget(back)

        forward = self._button("›", "Later captions")  # noqa: RUF001 - a chevron, not ">"
        forward.clicked.connect(self.scrub_forward_requested)
        layout.addWidget(forward)

        self._live = self._button("Live", "Back to what's being said now")
        self._live.setObjectName("live")
        self._live.setCheckable(True)
        self._live.setMinimumWidth(0)
        self._live.clicked.connect(self.live_requested)
        layout.addWidget(self._live)

        style = self._button("Aa", "Caption style")
        style.clicked.connect(self.style_requested)
        layout.addWidget(style)

        close = self._button("✕", "Hide captions — Ctrl+Alt+O brings them back")
        close.clicked.connect(self.hide_requested)
        layout.addWidget(close)

        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)
        self._motion = Tween(self, self._fade.setOpacity)
        self._motion.finished.connect(self._settled)
        self.adjustSize()
        self.hide()

    # ---------- state ----------

    def set_pinned(self, pinned: bool) -> None:
        self._pin.setChecked(pinned)

    def set_scrubbing(self, scrubbing: bool) -> None:
        """While scrubbing, ``Live`` is the way out, so it stops being quiet."""
        self._live.setChecked(scrubbing)

    def reveal(self, animated: bool = True) -> None:
        self.show()
        self.raise_()
        self._fade_to(1.0, animated)

    def conceal(self, animated: bool = True) -> None:
        self._fade_to(0.0, animated)
        if not animated:
            self.hide()

    # ---------- painting ----------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        rect = QRectF(self.rect())
        path.addRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.fillPath(path, QColor(*_PILL_BG))
        painter.end()

    # ---------- internals ----------

    def _button(self, glyph: str, tooltip: str, checkable: bool = False) -> QPushButton:
        button = QPushButton(glyph, self)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(_HIT)
        button.setMinimumWidth(_HIT)
        return button

    def _fade_to(self, target: float, animated: bool) -> None:
        self._motion.to(target, animate=animated)

    def _settled(self) -> None:
        if self._motion.target == 0:
            self.hide()


def _stylesheet() -> str:
    text = "rgb({}, {}, {})".format(*_GLASS.text_color)
    hover = "rgba({}, {}, {}, 0.14)".format(*_GLASS.text_color)
    return f"""
    QPushButton {{
        background: transparent;
        border: none;
        border-radius: {_HIT // 2}px;
        color: {text};
        font-size: 15px;
        font-weight: 600;
        padding: 0;
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:checked {{ color: {DARK.accent}; }}
    QPushButton#live {{ padding: 0 12px; font-size: 12px; letter-spacing: 0.4px; }}
    QPushButton#live:checked {{ background: {DARK.accent_subtle}; color: {DARK.accent}; }}
    """
