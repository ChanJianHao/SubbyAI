"""Where the caption panel sits, and how the mouse moves it.

Placement is saved per display signature to accommodate different desktop
layouts. When a saved display is unavailable, the panel is clamped to an
available screen so it remains visible and reachable.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

#: Fraction of the screen the panel spans, and how far its bottom sits above
#: the screen edge — subtitle convention, out of the way of most video UI.
DEFAULT_WIDTH_RATIO = 0.6
BOTTOM_INSET_RATIO = 0.06

LEFT_EDGE = -1
RIGHT_EDGE = 1


def screen_signature(widget: QWidget) -> str:
    """Key for this display's saved placement: name plus its pixel size."""
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return "display"
    size = screen.geometry()
    return f"{screen.name() or 'display'}@{size.width()}x{size.height()}"


def rect_from(saved: list[int] | None, min_width: int) -> QRect | None:
    """Read a stored ``[x, y, w, h]``, rejecting anything unusable."""
    if not saved or len(saved) != 4:
        return None
    try:
        x, y, width, height = (int(value) for value in saved)
    except (TypeError, ValueError):
        return None
    if width < min_width or height <= 0:
        return None
    return QRect(x, y, width, height)


def is_on_a_screen(rect: QRect) -> bool:
    """Whether the panel's centre lands on a display the user can actually see."""
    return any(
        screen.availableGeometry().contains(rect.center())
        for screen in QGuiApplication.screens()
    )


def default_rect(widget: QWidget, min_width: int, height: int) -> QRect:
    """Centred near the bottom of the widget's screen."""
    screen = widget.screen() or QGuiApplication.primaryScreen()
    area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
    width = max(min_width, min(area.width(), int(area.width() * DEFAULT_WIDTH_RATIO)))
    return QRect(
        area.x() + (area.width() - width) // 2,
        area.y() + area.height() - int(area.height() * BOTTOM_INSET_RATIO) - height,
        width,
        height,
    )


def edge_at(pos: QPoint, width: int, top_zone: int, edge_zone: int) -> int:
    """Which resize edge the cursor is in. Height follows the text, so only
    the left and right edges are grabbable."""
    if pos.y() < top_zone:
        return 0
    if pos.x() <= edge_zone:
        return LEFT_EDGE
    if pos.x() >= width - edge_zone:
        return RIGHT_EDGE
    return 0


def resized(start: QRect, edge: int, dx: int, min_width: int) -> QRect:
    """Apply a horizontal drag to one edge, keeping the other one still."""
    rect = QRect(start)
    if edge == LEFT_EDGE:
        rect.setLeft(min(rect.right() - min_width, rect.left() + dx))
    else:
        rect.setRight(max(rect.left() + min_width, rect.right() + dx))
    return rect
