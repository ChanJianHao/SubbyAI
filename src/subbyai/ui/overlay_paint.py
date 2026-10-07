"""The overlay's painters.

Every function here reads a resolved ``OverlayStyle`` and draws — no branching
on which preset is active, because a preset is a row of data. Adding a look
means adding a row to ``tokens.OVERLAY_PRESETS``, never a new code path here.

Nothing in this module measures or wraps text: ``overlay_layout`` already did
that, so a frame costs one blit per line plus, at most, a shadow and a stroke.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from . import theme
from .overlay_layout import (
    OUTLINE_ALPHA,
    PAIR_GAP,
    SHADOW_OFFSET,
    PairLayout,
)
from .tokens import SPACE, OverlayStyle

TICK_LENGTH = 14.0
TICK_WIDTH = 3


def paint_overlay(
    painter: QPainter,
    style: OverlayStyle,
    pairs: list[PairLayout],
    panel: QRectF,
    click_through: bool,
    scrubbing: bool,
) -> None:
    """Draw the whole panel: background, every pair top to bottom, then marks."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    _paint_panel(painter, style, panel)
    y = panel.top() + style.pad_v
    for pair in pairs:
        _paint_pair(painter, style, pair, panel.left() + style.pad_h, y)
        y += pair.height + PAIR_GAP
    _paint_marks(painter, style, panel, click_through, scrubbing)


def _paint_panel(painter: QPainter, style: OverlayStyle, panel: QRectF) -> None:
    """Fill and outline the panel. A zero-alpha background paints nothing."""
    radius = float(style.radius)
    if style.background[3] > 0:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*style.background))
        painter.drawRoundedRect(panel, radius, radius)
    if style.border:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(*style.border), style.border_width))
        inset = style.border_width / 2
        painter.drawRoundedRect(panel.adjusted(inset, inset, -inset, -inset), radius, radius)


def _paint_pair(
    painter: QPainter, style: OverlayStyle, pair: PairLayout, x: float, y: float
) -> None:
    """Draw one caption pair: shadow, outline, then the glyphs themselves."""
    for box in pair.lines:
        if box.static is None:
            continue
        alpha = max(0.0, min(1.0, box.opacity * pair.opacity))
        if alpha <= 0.0:
            continue
        painter.save()
        painter.translate(x, y + box.y + box.text_y)
        if box.font is not None:
            painter.setFont(box.font)
        if style.shadow:
            painter.setPen(QColor(0, 0, 0, int(style.shadow_alpha * alpha)))
            painter.drawStaticText(QPointF(box.x, SHADOW_OFFSET), box.static)
        if box.path is not None:
            pen = QPen(QColor(0, 0, 0, int(OUTLINE_ALPHA * alpha)))
            pen.setWidthF(style.outline_width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(box.path, pen)
        color = QColor(*box.color)
        color.setAlphaF(alpha)
        painter.setPen(color)
        painter.drawStaticText(QPointF(box.x, 0.0), box.static)
        painter.restore()


def _paint_marks(
    painter: QPainter,
    style: OverlayStyle,
    panel: QRectF,
    click_through: bool,
    scrubbing: bool,
) -> None:
    """Two small truths about the panel's own state, drawn in the corners."""
    if click_through:
        # Clicks pass straight through; without this the user has no way to
        # tell the overlay from a broken one.
        painter.setPen(QPen(QColor(*style.translation_color), TICK_WIDTH))
        corner = panel.topLeft() + QPointF(SPACE["sm"], SPACE["sm"])
        painter.drawLine(corner, corner + QPointF(TICK_LENGTH, 0.0))
    if scrubbing:
        label = QColor(*style.translation_color)
        label.setAlphaF(0.85)
        painter.setFont(theme.ui_font("micro"))
        painter.setPen(label)
        painter.drawText(
            panel.adjusted(style.pad_h, SPACE["xs"], -style.pad_h, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            "HISTORY",
        )
