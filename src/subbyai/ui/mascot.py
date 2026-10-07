"""Mochi reacts to meaningful state changes, then rests. No idle repaint timer."""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme
from .motion import Tween, allowed


class Mochi(QWidget):
    def __init__(self, parent=None, size=96):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAccessibleName("Mochi, SubbyAI's smiling subtitle companion")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._mood = "ready"
        self._reaction = Tween(self, lambda _value: self.update(), 1.0)

    def set_mood(self, mood: str) -> None:
        if mood == self._mood:
            return
        self._mood = mood
        self._reaction.value = 0.0
        self._reaction.to(1.0, "feedback")

    def enterEvent(self, event):
        if allowed(self):
            self._reaction.value = 0.0
            self._reaction.to(1.0, "feedback")
        super().enterEvent(event)

    def paintEvent(self, event):
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 100, self.height() / 100)
        lift = math.sin(self._reaction.value * math.pi) * 4
        painter.translate(50, 50 - lift)
        painter.rotate(math.sin(self._reaction.value * math.tau) * 3)
        painter.translate(-50, -50)
        draw_mochi(painter, palette, self._mood)
        painter.end()


def draw_mochi(painter, palette, mood="ready"):
    """Draw our original mark in a 100 x 100 coordinate system."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(palette.accent_subtle))
    painter.drawEllipse(QRectF(6, 6, 88, 88))
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(16, 21, 68, 54), 23, 23)
    bubble.moveTo(32, 66)
    bubble.lineTo(29, 85)
    bubble.lineTo(49, 73)
    painter.setBrush(QColor(palette.accent))
    painter.drawPath(bubble)
    painter.setBrush(QColor(palette.accent_on))
    for x in (36, 59):
        height = 3 if mood == "success" else 8
        gaze = 2 if mood == "listening" else 0
        painter.drawRoundedRect(QRectF(x + gaze, 42, 5, height), 2.5, 2.5)
    painter.setPen(
        QPen(QColor(palette.accent_on), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    )
    smile = QPainterPath()
    if mood == "busy":
        smile.addEllipse(QRectF(47, 55, 6, 6))
    elif mood == "error":
        smile.moveTo(45, 58)
        smile.quadTo(50, 54, 55, 58)
    else:
        smile.moveTo(45, 55)
        smile.quadTo(50, 61, 55, 55)
    painter.drawPath(smile)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#E783AC"))
    painter.drawEllipse(QRectF(26, 53, 10, 4))
    painter.drawEllipse(QRectF(64, 53, 10, 4))
