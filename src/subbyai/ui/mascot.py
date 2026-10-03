"""Mochi: an original, quiet speech-bubble companion, drawn with Qt paths."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme


class Mochi(QWidget):
    def __init__(self, parent=None, size=96):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAccessibleName("Mochi, SubbyAI's smiling subtitle companion")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 100, self.height() / 100)
        draw_mochi(painter, palette)
        painter.end()


def draw_mochi(painter, palette):
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
        painter.drawRoundedRect(QRectF(x, 42, 5, 8), 2.5, 2.5)
    painter.setPen(
        QPen(QColor(palette.accent_on), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    )
    smile = QPainterPath()
    smile.moveTo(45, 55)
    smile.quadTo(50, 61, 55, 55)
    painter.drawPath(smile)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#E783AC"))
    painter.drawEllipse(QRectF(26, 53, 10, 4))
    painter.drawEllipse(QRectF(64, 53, 10, 4))
