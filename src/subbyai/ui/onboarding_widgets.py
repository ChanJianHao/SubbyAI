"""Painted building blocks and language data for the first-run wizard.

Kept apart from the wizard so that file can be about flow rather than pixels.

The style cards paint straight from ``tokens.OVERLAY_PRESETS``: a preview that
re-implements the overlay is a preview that will quietly stop matching the thing
it previews, and the first run is exactly where that lie costs the most trust.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from ..core.settings import OverlayPreset
from . import theme
from .tokens import OVERLAY_PRESETS, RADIUS, SPACE
from .widgets import SOUND_LEVEL, LevelMeter

__all__ = [
    "SOUND_LEVEL",
    "DualPreview",
    "KeyCapRow",
    "LevelMeter",
    "ProgressDots",
    "SelectCard",
    "StyleCard",
    "qcolor",
]


def qcolor(value: str) -> QColor:
    """A QColor from any token value, including the ``rgba()`` forms Qt cannot parse."""
    if value.startswith("rgba"):
        parts = value[value.index("(") + 1 : value.rindex(")")].split(",")
        red, green, blue = (int(float(part)) for part in parts[:3])
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        return QColor(red, green, blue, round(alpha * 255))
    return QColor(value)


class ProgressDots(QWidget):
    """Where you are in the wizard, without ever numbering the steps at you."""

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._total = max(1, total)
        self._index = 0
        self.setFixedSize(self._total * 12 + 8, 12)

    def set_index(self, index: int) -> None:
        self._index = index
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current()
        painter.setPen(Qt.PenStyle.NoPen)
        x = 0.0
        top = (self.height() - 6) / 2
        for step in range(self._total):
            active = step == self._index
            painter.setBrush(qcolor(palette.accent if active else palette.stroke))
            width = 14.0 if active else 6.0
            painter.drawRoundedRect(QRectF(x, top, width, 6.0), 3.0, 3.0)
            x += width + 6.0
        painter.end()


class SelectCard(QAbstractButton):
    """A checkable card: a title, one line of plain explanation, optional badge and size.

    Unavailable cards stay visible and say why, because a choice that silently
    disappears reads as a missing feature rather than a machine limit.
    """

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        meta: str = "",
        badge: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(title)
        self._subtitle = subtitle
        self._meta = meta
        self._badge = badge
        self._reason = ""
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        return QSize(240, self.minimumHeight())

    def set_badge(self, badge: str) -> None:
        self._badge = badge
        self.update()

    def set_unavailable(self, reason: str) -> None:
        """Refuse the card and show the reason in place of its description."""
        self._reason = reason
        self.setEnabled(not reason)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = theme.current()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        checked = self.isChecked()
        enabled = self.isEnabled()

        painter.setBrush(qcolor(colors.accent_subtle if checked else colors.surface))
        edge = colors.accent if checked else (colors.stroke if self.hasFocus() else colors.hairline)
        painter.setPen(QPen(qcolor(edge), 2 if checked else 1))
        painter.drawRoundedRect(rect, RADIUS["xl"], RADIUS["xl"])

        pad = float(SPACE["lg"])
        reserved = self._paint_side(painter, rect, pad)
        text_width = rect.width() - pad * 2 - reserved
        painter.setFont(theme.ui_font("body_strong"))
        painter.setPen(qcolor(colors.text if enabled else colors.text_disabled))
        painter.drawText(
            QRectF(rect.left() + pad, rect.top() + pad - 4, text_width, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.text(),
        )
        detail = self._reason or self._subtitle
        if detail:
            painter.setFont(theme.ui_font("caption"))
            painter.setPen(qcolor(colors.text_secondary if enabled else colors.text_disabled))
            painter.drawText(
                QRectF(rect.left() + pad, rect.top() + pad + 16, text_width, rect.height() - pad),
                int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
                detail,
            )
        painter.end()

    def _paint_side(self, painter: QPainter, rect: QRectF, pad: float) -> float:
        """Draw the badge and size column; return how much width it claimed."""
        colors = theme.current()
        claimed = 0.0
        if self._badge:
            painter.setFont(theme.ui_font("micro"))
            width = painter.fontMetrics().horizontalAdvance(self._badge) + 18
            pill = QRectF(rect.right() - pad - width, rect.top() + pad - 2, width, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(colors.accent_subtle))
            painter.drawRoundedRect(pill, 9, 9)
            painter.setPen(qcolor(colors.accent))
            painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), self._badge)
            claimed = width + SPACE["md"]
        if self._meta:
            painter.setFont(theme.ui_font("caption"))
            width = painter.fontMetrics().horizontalAdvance(self._meta) + 4
            top = rect.top() + pad + (18 if self._badge else 0)
            painter.setPen(qcolor(colors.text_tertiary))
            painter.drawText(
                QRectF(rect.right() - pad - width, top - 2, width, 18),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                self._meta,
            )
            claimed = max(claimed, width + SPACE["md"])
        return claimed


def _mock_frame(painter: QPainter, frame: QRectF) -> None:
    """A stand-in for whatever the user will actually be watching.

    Derived from the palette rather than a bundled screenshot, so it stays in the
    family in both themes and costs nothing to ship.
    """
    colors = theme.current()
    gradient = QLinearGradient(frame.topLeft(), frame.bottomRight())
    gradient.setColorAt(0.0, qcolor(colors.float_).darker(190))
    gradient.setColorAt(1.0, qcolor(colors.canvas).darker(150))
    painter.fillRect(frame, gradient)
    glow = qcolor(colors.accent)
    glow.setAlpha(34)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(
        QRectF(
            frame.left() - frame.width() * 0.15,
            frame.top() - frame.height() * 0.25,
            frame.width() * 0.75,
            frame.height() * 0.95,
        )
    )


def _draw_caption_text(
    painter: QPainter, rect: QRectF, text: str, color: tuple[int, int, int], outline: bool
) -> None:
    flags = int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap)
    if outline:
        painter.setPen(QColor(0, 0, 0, 217))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(rect.translated(dx, dy), flags, text)
    painter.setPen(QColor(*color))
    painter.drawText(rect, flags, text)


class StyleCard(QAbstractButton):
    """One overlay preset, drawn at miniature scale over a mock frame."""

    SAMPLE = "This is how your captions will look."

    def __init__(self, preset: OverlayPreset, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preset = preset
        self.setText(label)
        self.setMinimumSize(230, 122)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(230, 122)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = theme.current()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        footer_height = 28.0
        frame = QRectF(rect.left(), rect.top(), rect.width(), rect.height() - footer_height)

        clip = QPainterPath()
        clip.addRoundedRect(rect, RADIUS["lg"], RADIUS["lg"])
        painter.setClipPath(clip)
        _mock_frame(painter, frame)
        self._paint_panel(painter, frame)

        checked = self.isChecked()
        footer = QRectF(rect.left(), rect.bottom() - footer_height, rect.width(), footer_height)
        painter.fillRect(footer, qcolor(colors.accent_subtle if checked else colors.surface))
        painter.setFont(theme.ui_font("body_strong"))
        painter.setPen(qcolor(colors.text if checked else colors.text_secondary))
        painter.drawText(footer, int(Qt.AlignmentFlag.AlignCenter), self.text())

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        edge = colors.accent if checked else colors.hairline
        painter.setPen(QPen(qcolor(edge), 2 if checked else 1))
        painter.drawRoundedRect(rect, RADIUS["lg"], RADIUS["lg"])
        painter.end()

    def _paint_panel(self, painter: QPainter, frame: QRectF) -> None:
        style = OVERLAY_PRESETS[self.preset]
        mini_size = max(9, round(style.font_size * 0.34))
        painter.setFont(theme.caption_font(mini_size, style.font_weight))
        available = QRect(0, 0, int(frame.width() * 0.80), 1000)
        text_rect = painter.fontMetrics().boundingRect(
            available, int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap), self.SAMPLE
        )
        panel = QRectF(0, 0, text_rect.width() + 22, text_rect.height() + 12)
        panel.moveCenter(frame.center())
        panel.moveBottom(frame.bottom() - 9)

        background = QColor(*style.background)
        if background.alpha():
            painter.setBrush(background)
            painter.setPen(
                QPen(QColor(*style.border), 1) if style.border else Qt.PenStyle.NoPen
            )
            painter.drawRoundedRect(panel, style.radius * 0.6, style.radius * 0.6)
        _draw_caption_text(
            painter,
            panel.adjusted(11, 6, -11, -6),
            self.SAMPLE,
            style.translation_color,
            style.outline,
        )


class DualPreview(QWidget):
    """Two lines stacked the way the overlay stacks them: heard above, understood below."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preset = OverlayPreset.GLASS
        self._original = ""
        self._translation = ""
        self._show_original = True
        self.setFixedHeight(82)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_lines(self, original: str, translation: str) -> None:
        self._original = original
        self._translation = translation
        self.update()

    def set_show_original(self, show: bool) -> None:
        self._show_original = show
        self.update()

    def set_preset(self, preset: OverlayPreset) -> None:
        self._preset = preset if preset in OVERLAY_PRESETS else OverlayPreset.GLASS
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        backdrop = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(backdrop, RADIUS["lg"], RADIUS["lg"])
        painter.setClipPath(clip)
        _mock_frame(painter, backdrop)

        style = OVERLAY_PRESETS[self._preset]
        translation_font = theme.caption_font(15, style.font_weight)
        original_font = theme.caption_font(
            max(10, round(15 * style.original_ratio)), style.font_weight
        )
        original_color = QColor(*style.text_color)
        original_color.setAlphaF(style.dim_original)

        lines = []
        if self._show_original and self._original:
            lines.append((original_font, original_color.getRgb()[:3], self._original))
        lines.append((translation_font, style.translation_color, self._translation))

        widths, heights = [], []
        for font, _color, text in lines:
            painter.setFont(font)
            widths.append(painter.fontMetrics().horizontalAdvance(text))
            heights.append(painter.fontMetrics().height())
        panel = QRectF(0, 0, max(widths) + 36, sum(heights) + 4 * (len(lines) - 1) + 18)
        panel.moveCenter(backdrop.center())

        background = QColor(*style.background)
        if background.alpha():
            painter.setBrush(background)
            painter.setPen(QPen(QColor(*style.border), 1) if style.border else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(panel, style.radius, style.radius)

        top = panel.top() + 9
        for index, (font, color, text) in enumerate(lines):
            painter.setFont(font)
            line = QRectF(panel.left(), top, panel.width(), heights[index])
            if index == 0 and len(lines) > 1:
                painter.setPen(original_color)
                painter.drawText(line, int(Qt.AlignmentFlag.AlignCenter), text)
            else:
                _draw_caption_text(painter, line, text, color, style.outline)
            top += heights[index] + 4
        painter.end()


class KeyCapRow(QWidget):
    """A global shortcut, shown the way it looks on the keyboard."""

    def __init__(self, sequence: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        colors = theme.current()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["md"])
        cap = QLabel(sequence)
        cap.setFont(theme.mono_font(12))
        cap.setStyleSheet(
            f"background:{colors.input_bg};border:1px solid {colors.stroke};"
            f"border-radius:{RADIUS['sm']}px;padding:3px 8px;color:{colors.text};"
        )
        text = QLabel(description)
        text.setObjectName("secondary")
        row.addWidget(cap)
        row.addWidget(text, 1)
