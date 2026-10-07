"""Painted motion controls; logical values and accessibility remain native Qt."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QProgressBar, QWidget

from . import theme
from .motion import Tween, allowed, policy
from .tokens import DURATION


class MotionToggle(QCheckBox):
    """A sliding switch with the checkbox's signals, focus and accessible state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._position = Tween(self, lambda _value: self.update(), float(self.isChecked()))
        self.toggled.connect(lambda checked: self._position.to(float(checked), "fast"))

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        width = 38 + (10 + metrics.horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(width, max(24, metrics.height() + 4))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, point) -> bool:
        return self.rect().contains(point)

    def checkStateSet(self) -> None:
        super().checkStateSet()
        if hasattr(self, "_position") and self.signalsBlocked():
            self._position.to(float(self.isChecked()), animate=False)

    def paintEvent(self, event) -> None:
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        position = self._position.value
        right_to_left = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        x = self.width() - 38 if right_to_left else 0
        top = (self.height() - 22) / 2
        track = QRectF(x + 1, top + 1, 36, 20)
        off, on = QColor(palette.stroke), QColor(palette.accent)
        colour = QColor.fromRgbF(
            off.redF() + (on.redF() - off.redF()) * position,
            off.greenF() + (on.greenF() - off.greenF()) * position,
            off.blueF() + (on.blueF() - off.blueF()) * position,
        )
        if not self.isEnabled():
            painter.setOpacity(0.55)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(track, 10, 10)
        thumb = position if not right_to_left else 1 - position
        painter.setBrush(QColor(palette.accent_on if self.isChecked() else palette.surface))
        painter.drawEllipse(QRectF(x + 4 + 16 * thumb, top + 4, 14, 14))
        if self.hasFocus():
            painter.setPen(QPen(QColor(palette.focus), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-1, -1, 1, 1), 11, 11)
        if self.text():
            painter.setPen(QColor(palette.text if self.isEnabled() else palette.text_disabled))
            rect = self.rect().adjusted(48, 0, 0, 0)
            if right_to_left:
                rect = self.rect().adjusted(0, 0, -48, 0)
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextShowMnemonic, self.text()
            )
        painter.end()


class ActivityIndicator(QWidget):
    """A bounded spinner while work is pending; a static ring with reduced motion."""

    def __init__(self, parent: QWidget | None = None, size: int = 20):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._busy = False
        self._phase = 0.0
        self._cycle = QVariantAnimation(self)
        self._cycle.setDuration(DURATION["activity"])
        self._cycle.setStartValue(0.0)
        self._cycle.setEndValue(1.0)
        self._cycle.setLoopCount(-1)
        self._cycle.valueChanged.connect(self._frame)
        policy().changed.connect(self._sync)
        self.hide()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.setVisible(busy)
        self._sync()

    def _frame(self, phase) -> None:
        self._phase = float(phase)
        self.update()

    def _sync(self) -> None:
        if self._busy and allowed(self):
            self._cycle.start()
        else:
            self._cycle.stop()
            self._phase = 0.0
            self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event) -> None:
        self._cycle.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current()
        bounds = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        painter.setPen(QPen(QColor(palette.stroke), 2))
        painter.drawEllipse(bounds)
        painter.setPen(
            QPen(QColor(palette.accent), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        painter.drawArc(bounds, round((90 - self._phase * 360) * 16), -110 * 16)
        painter.end()


class SmoothProgressBar(QProgressBar):
    """Actual progress is immediate; only the painted fill follows with easing."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(8)
        self._fill = Tween(self, lambda _value: self.update())
        self._phase = 0.0
        self._cycle = QVariantAnimation(self)
        self._cycle.setDuration(DURATION["progress"])
        self._cycle.setStartValue(0.0)
        self._cycle.setEndValue(1.0)
        self._cycle.setLoopCount(-1)
        self._cycle.valueChanged.connect(self._frame)
        self.valueChanged.connect(self._value_changed)
        policy().changed.connect(self._sync)

    def _value_changed(self, value: int) -> None:
        span = self.maximum() - self.minimum()
        fraction = (value - self.minimum()) / span if span else 0.0
        self._fill.to(max(0.0, min(1.0, fraction)), animate=value != self.minimum())

    def setRange(self, minimum: int, maximum: int) -> None:
        super().setRange(minimum, maximum)
        self._value_changed(self.value())
        self._sync()

    def reset(self) -> None:
        super().reset()
        self._fill.to(0.0, animate=False)

    def _sync(self) -> None:
        if self.minimum() == self.maximum() == 0 and allowed(self):
            self._cycle.start()
        else:
            self._cycle.stop()
            self.update()

    def _frame(self, value) -> None:
        self._phase = float(value)
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event) -> None:
        self._cycle.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current()
        track = QRectF(0, (self.height() - 8) / 2, self.width(), 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.raised))
        painter.drawRoundedRect(track, 4, 4)
        painter.setBrush(QColor(palette.accent))
        if self.minimum() == self.maximum() == 0:
            fraction = 0.5 - 0.5 * math.cos(self._phase * math.tau) if allowed(self) else 0.5
            fill = QRectF(
                track.left() + fraction * track.width() * 0.65, track.top(), track.width() * 0.35, 8
            )
        else:
            fill = QRectF(track.left(), track.top(), track.width() * self._fill.value, 8)
        if self.invertedAppearance() != (self.layoutDirection() == Qt.LayoutDirection.RightToLeft):
            fill.moveRight(track.right() - (fill.left() - track.left()))
        painter.drawRoundedRect(fill, 4, 4)
        if self.isTextVisible():
            painter.setPen(QColor(palette.text))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class SelectionMarker(QWidget):
    """A small accent travelling between tabs without moving their hit targets."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._from = self._to = QRectF()
        self._selected: QWidget | None = None
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._reposition)
        self._position = Tween(self, lambda _value: self.update(), 1.0)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def select(self, widget: QWidget) -> None:
        self._selected = widget
        progress = self._position.value
        self._from = self._from.translated((self._to.x() - self._from.x()) * progress, 0)
        self._from.setWidth(self._from.width() + (self._to.width() - self._from.width()) * progress)
        self._to = QRectF(widget.geometry()).adjusted(10, 0, -10, 0)
        self._to.setTop(self.height() - 3)
        self._to.setHeight(3)
        if self._from.isEmpty():
            self._from = QRectF(self._to)
        self.show()
        self.raise_()
        self._position.value = 0.0
        self._position.to(1.0)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
            self.hide()
            self._layout_timer.start(0)
        return False

    def _reposition(self) -> None:
        if self._selected is not None:
            self.select(self._selected)
            self._position.settle()

    def paintEvent(self, event) -> None:
        progress = self._position.value
        rect = QRectF(self._to)
        rect.moveLeft(self._from.x() + (self._to.x() - self._from.x()) * progress)
        rect.setWidth(self._from.width() + (self._to.width() - self._from.width()) * progress)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.current().accent))
        painter.drawRoundedRect(rect, 1.5, 1.5)
        painter.end()
