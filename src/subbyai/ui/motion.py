"""Interruptible Qt motion, with immediate semantic state and bounded painting.

Animations never own an action or its completion. Hidden controls settle, page
snapshots are released after a short transition, and decorative layers cannot
intercept input. No worker, graphics dependency or idle animation is needed.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .tokens import DURATION, MOTION_DISTANCE, RADIUS

log = logging.getLogger(__name__)
EASING = QEasingCurve.Type.OutCubic


def system_reduces_motion() -> bool:
    """Windows' Accessibility > Visual effects > Animation effects preference."""
    if sys.platform == "win32":
        try:
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return not bool(enabled.value)
        except (AttributeError, OSError):
            log.debug("System animation preference unavailable")
    elif sys.platform == "darwin":
        # NSWorkspace is optional; never add a native runtime just for motion.
        try:
            from Foundation import NSUserDefaults

            return bool(
                NSUserDefaults.standardUserDefaults()
                .persistentDomainForName_("com.apple.universalaccess")
                .get("reduceMotion", False)
            )
        except (ImportError, AttributeError):
            pass
    return False


class MotionPolicy(QObject):
    changed = Signal()

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._requested = False
        self._system = system_reduces_motion()
        app.applicationStateChanged.connect(self.refresh_system)

    @property
    def reduced(self) -> bool:
        return self._requested or self._system

    @property
    def requested(self) -> bool:
        return self._requested

    def configure(self, reduced: bool) -> None:
        before = self.reduced
        self._requested = bool(reduced)
        if before != self.reduced:
            self.changed.emit()

    def refresh_system(self, *_args) -> None:
        before = self.reduced
        self._system = system_reduces_motion()
        if before != self.reduced:
            self.changed.emit()


def policy() -> MotionPolicy:
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("Motion requires a QApplication")
    if not hasattr(app, "_motion_policy"):
        app._motion_policy = MotionPolicy(app)
    return app._motion_policy


def allowed(widget: QWidget | None = None) -> bool:
    return not policy().reduced and (widget is None or widget.isVisible())


class Tween(QObject):
    """A reusable scalar: reverse from the current value, never queue frames."""

    finished = Signal()

    def __init__(self, owner: QWidget, write: Callable[[float], None], value: float = 0.0):
        super().__init__(owner)
        self.owner = owner
        self.value = self.target = float(value)
        self._write = write
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(EASING)
        self.animation.valueChanged.connect(self._frame)
        self.animation.finished.connect(self.finished)
        owner.installEventFilter(self)
        policy().changed.connect(self._policy_changed)

    def _frame(self, value) -> None:
        self.value = float(value)
        self._write(self.value)

    def to(self, target: float, duration: str = "base", *, animate: bool = True) -> None:
        self.animation.stop()
        self.target = float(target)
        if not animate or not allowed(self.owner) or abs(self.value - self.target) < 0.001:
            self._frame(self.target)
            self.finished.emit()
            return
        self.animation.setDuration(DURATION[duration])
        self.animation.setStartValue(self.value)
        self.animation.setEndValue(self.target)
        self.animation.start()

    def settle(self) -> None:
        self.animation.stop()
        self._frame(self.target)
        self.finished.emit()

    def _policy_changed(self) -> None:
        if policy().reduced:
            self.settle()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Hide:
            self.settle()
        return False


class _PageSnapshot(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pixmap = QPixmap()
        self.progress = 1.0
        self.direction = 1
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setOpacity(1.0 - self.progress)
        painter.drawPixmap(
            QPointF(-self.direction * MOTION_DISTANCE["page"] * self.progress, 0), self.pixmap
        )
        painter.end()


class MotionStack(QStackedWidget):
    """Change pages immediately; dissolve and slide the outgoing visual only."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._snapshot = _PageSnapshot(self)
        self._transition = Tween(self, self._frame, 1.0)
        self._transition.finished.connect(self._release)

    def setCurrentIndex(self, index: int) -> None:
        old = self.currentIndex()
        if index == old or not 0 <= index < self.count():
            return
        pixmap = QPixmap()
        # Limit memory on very large/multi-monitor high-DPI windows.
        pixels = self.width() * self.height() * self.devicePixelRatioF() ** 2
        if old >= 0 and allowed(self) and pixels <= 8_000_000:
            pixmap = self.grab()
        self._release()
        super().setCurrentIndex(index)
        if not pixmap.isNull():
            self._snapshot.pixmap = pixmap
            self._snapshot.direction = 1 if index > old else -1
            self._snapshot.setGeometry(self.rect())
            self._snapshot.show()
            self._snapshot.raise_()
            self._transition.value = 0.0
            self._transition.to(1.0, "page")

    def setCurrentWidget(self, widget: QWidget) -> None:
        self.setCurrentIndex(self.indexOf(widget))

    def _frame(self, value: float) -> None:
        self._snapshot.progress = value
        self._snapshot.update()

    def _release(self) -> None:
        self._transition.animation.stop()
        self._snapshot.hide()
        self._snapshot.pixmap = QPixmap()

    def resizeEvent(self, event) -> None:
        self._release()
        super().resizeEvent(event)


class Expandable(QWidget):
    """Clip a settings section while opening; hide it and its focus targets on close."""

    def __init__(
        self, content: QWidget, parent: QWidget | None = None, *, horizontal: bool = False
    ):
        super().__init__(parent)
        self.content = content
        self._horizontal = horizontal
        self.expanded = True
        self._content_enabled_before_exit: bool | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content)
        self._height = Tween(self, self._frame)
        self._height.finished.connect(self._finish)

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self.expanded:
            return
        self.expanded = expanded
        was_visible = self.isVisible()
        extent = self.width() if self._horizontal else self.height()
        self._height.value = float(extent if was_visible else 0)
        if expanded:
            if self._content_enabled_before_exit is not None:
                self.content.setEnabled(self._content_enabled_before_exit)
                self._content_enabled_before_exit = None
            self.show()
            self.content.show()
        elif was_visible:
            self._content_enabled_before_exit = self.content.isEnabled()
            self.content.setEnabled(False)
        if self._horizontal:
            target = max(self.content.minimumWidth(), self.content.sizeHint().width())
        else:
            target = self.content.heightForWidth(self.width())
            if target < 0:
                target = self.content.sizeHint().height()
        parent = self.parentWidget()
        self._height.to(
            max(0, target) if expanded else 0,
            "gentle",
            animate=was_visible or bool(parent and parent.isVisible()),
        )

    def _frame(self, height: float) -> None:
        if self._horizontal:
            self.setFixedWidth(round(height))
        else:
            self.setMaximumHeight(round(height))

    def _finish(self) -> None:
        if self.expanded:
            if not self._horizontal:
                self.setMaximumHeight(16777215)
        else:
            self.hide()
            if self._content_enabled_before_exit is not None:
                self.content.setEnabled(self._content_enabled_before_exit)
                self._content_enabled_before_exit = None


class VisibilityMotion(QObject):
    """Small surfaces arrive and leave without hiding before their exit completes."""

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self.widget = widget
        self.visible = not widget.isHidden()
        self._enabled_before_exit: bool | None = None
        self.effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(self.effect)
        self.tween = Tween(widget, self.effect.setOpacity, 1.0)
        self.tween.finished.connect(self._finish)

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)
        if visible:
            if self._enabled_before_exit is not None:
                self.widget.setEnabled(self._enabled_before_exit)
                self._enabled_before_exit = None
            if self.widget.isHidden():
                self.tween.value = 0.0
                self.effect.setOpacity(0.0)
                self.widget.show()
            self.tween.to(1.0)
        else:
            if self._enabled_before_exit is None:
                self._enabled_before_exit = self.widget.isEnabled()
            self.widget.setEnabled(False)
            self.tween.to(0.0, "fast")

    def _finish(self) -> None:
        if not self.visible:
            self.widget.hide()


def reveal(widget: QWidget, visible: bool) -> None:
    controller = getattr(widget, "_visibility_motion", None)
    if controller is None:
        controller = widget._visibility_motion = VisibilityMotion(widget)
    controller.set_visible(visible)


class InteractionFeedback(QWidget):
    """A local wash, focus ring and press ripple; geometry and hit areas stay put."""

    def __init__(self, control: QWidget):
        super().__init__(control)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._control = control
        self._origin = QPointF()
        self._hover = Tween(self, lambda _value: self.update())
        self._press = Tween(self, lambda _value: self.update(), 1.0)
        control.installEventFilter(self)
        if isinstance(control, QAbstractButton):
            control.pressed.connect(self.pulse)
            control.toggled.connect(lambda _checked: self.pulse())
        self.setGeometry(control.rect())
        self.show()

    def pulse(self) -> None:
        if not allowed(self._control) or not self._control.isEnabled():
            return
        if self._origin.isNull():
            self._origin = QPointF(self.rect().center())
        self._press.value = 0.0
        self._press.to(1.0, "feedback")

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        elif kind in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            self._hover.to(1.0, "fast")
        elif kind in (QEvent.Type.Leave, QEvent.Type.FocusOut, QEvent.Type.EnabledChange):
            active = watched.isEnabled() and (watched.underMouse() or watched.hasFocus())
            self._hover.to(float(active))
        elif kind == QEvent.Type.MouseButtonPress:
            self._origin = event.position()
            if not isinstance(watched, QAbstractButton):
                self.pulse()
        elif kind == QEvent.Type.KeyPress:
            self._origin = QPointF(self.rect().center())
        elif kind == QEvent.Type.Hide:
            self._hover.to(0.0, animate=False)
            self._press.to(1.0, animate=False)
        return False

    def paintEvent(self, event) -> None:
        if not self._control.isEnabled():
            return
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = RADIUS["lg"] if self.height() > 70 else RADIUS["sm"]
        path = QPainterPath()
        path.addRoundedRect(bounds, radius, radius)
        painter.setClipPath(path)
        colour = QColor(palette.accent)
        colour.setAlphaF(0.07 * self._hover.value)
        painter.fillPath(path, colour)
        if self._press.value < 1.0:
            colour.setAlphaF(0.18 * (1.0 - self._press.value))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            radius = max(self.width(), self.height()) * self._press.value
            painter.drawEllipse(self._origin, radius, radius)
        if self._control.hasFocus():
            painter.setPen(QPen(QColor(palette.focus), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.end()


def interactive(control: QWidget) -> InteractionFeedback:
    if not hasattr(control, "_interaction_feedback"):
        control._interaction_feedback = InteractionFeedback(control)
    return control._interaction_feedback


class _Confirmation(QObject):
    def __init__(self, button: QAbstractButton):
        super().__init__(button)
        self.button = button
        self.original = button.text()
        self.label = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.restore)

    def show(self, label: str) -> None:
        if not self.timer.isActive():
            self.original = self.button.text()
        self.label = label
        self.button.setText(label)
        interactive(self.button).pulse()
        self.timer.start(DURATION["confirmation"])

    def restore(self) -> None:
        if self.button.text() == self.label:
            self.button.setText(self.original)


def confirm_action(button: QAbstractButton, label: str = "Saved ✓") -> None:
    """Confirm only after success; repeated actions extend the same bounded timer."""
    if not hasattr(button, "_confirmation"):
        button._confirmation = _Confirmation(button)
    button._confirmation.show(label)


class _WindowEntrance(QObject):
    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self.widget = widget
        self.tween = Tween(widget, widget.setWindowOpacity, 1.0)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show and allowed(watched):
            # Always readable and interactive from the first frame.
            self.tween.value = 0.88
            self.widget.setWindowOpacity(0.88)
            self.tween.to(1.0, "fast")
        return False


class _FeedbackInstaller(QObject):
    def __init__(self, parent: QObject):
        super().__init__(parent)
        # Native popup wrappers can be temporary. Retain their Python feedback
        # until Qt destroys the widget, and mark construction before callbacks
        # can deliver another Show event.
        self._controls: dict[int, InteractionFeedback | None] = {}
        self._windows: dict[int, _WindowEntrance | None] = {}

    @staticmethod
    def _retain(widget, controllers, factory):
        key = id(widget)
        if key in controllers:
            return controllers[key]
        controllers[key] = None
        try:
            controller = factory(widget)
            controllers[key] = controller
            widget.destroyed.connect(
                lambda _object=None, key=key: controllers.pop(key, None)
            )
            return controller
        except Exception:
            controllers.pop(key, None)
            raise

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show:
            try:
                if isinstance(watched, QAbstractButton):
                    self._retain(watched, self._controls, interactive)
                elif (
                    isinstance(watched, (QMenu, QDialog))
                    or (
                        isinstance(watched, QWidget)
                        and watched.windowType() in (Qt.WindowType.Popup, Qt.WindowType.ToolTip)
                    )
                ) and id(watched) not in self._windows:
                    entrance = self._retain(watched, self._windows, _WindowEntrance)
                    if entrance is not None:
                        watched._window_entrance = entrance
                        entrance.eventFilter(watched, event)
            except (RuntimeError, AttributeError):
                log.debug("Decorative feedback unavailable", exc_info=True)
        return False


def install(app: QApplication, reduced: bool = False) -> None:
    policy().configure(reduced)
    if not hasattr(app, "_feedback_installer"):
        app._feedback_installer = _FeedbackInstaller(app)
        app.installEventFilter(app._feedback_installer)
