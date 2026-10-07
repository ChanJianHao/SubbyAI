"""The model and painter behind the transcript body.

Sessions run to thousands of captions, so rows are data plus a delegate rather
than a widget each: only what is on screen is ever laid out or painted. The
model owns the flash highlight because a search hit has to survive the scroll
that brings it into view.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QWidget

from ..core.events import CaptionSegment, SessionInfo
from . import theme
from .exports import clock_timestamp, wall_timestamp
from .tokens import Palette

SEGMENT_ROLE = Qt.ItemDataRole.UserRole + 1
GUTTER_WIDTH = 72
ROW_PADDING_V = 6
ROW_PADDING_H = 12
PAIR_GAP = 2
FLASH_MS = 600

#: `14:03:12` needs more room than the gutter's own padding leaves, and the text
#: block only starts at GUTTER_WIDTH + ROW_PADDING_H, so it can borrow the gap.
_STAMP_WIDTH = GUTTER_WIDTH - ROW_PADDING_H + 8

_WRAP = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
_GUTTER_ALIGN = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)


def row_text(segment: CaptionSegment) -> str:
    """What one row means as plain text — original, then translation."""
    translation = segment.display_translation
    return f"{segment.text}\n{translation}" if translation else segment.text


class TranscriptModel(QAbstractListModel):
    """Rows of ``CaptionSegment``. Holds the session only for wall-clock stamps."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._segments: list[CaptionSegment] = []
        self._session: SessionInfo | None = None
        self._flash_id: int | None = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._finish_flash)

    def set_segments(
        self, segments: list[CaptionSegment], session: SessionInfo | None = None
    ) -> None:
        self.beginResetModel()
        self._flash_timer.stop()
        self._segments = list(segments)
        self._session = session
        self._flash_id = None
        self.endResetModel()

    def segments(self) -> list[CaptionSegment]:
        return list(self._segments)

    def session(self) -> SessionInfo | None:
        return self._session

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._segments)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid() or not 0 <= index.row() < len(self._segments):
            return None
        segment = self._segments[index.row()]
        if role == SEGMENT_ROLE:
            return segment
        if role == Qt.ItemDataRole.DisplayRole:
            return row_text(segment)
        return None

    def timestamp(self, segment: CaptionSegment) -> str:
        """Time of day when the session is known, offset when it is not."""
        if self._session is not None:
            return wall_timestamp(self._session.started_at, segment.audio_start)
        return clock_timestamp(segment.audio_start)

    def is_flashing(self, segment: CaptionSegment) -> bool:
        return self._flash_id is not None and segment.id == self._flash_id

    def row_for_segment(self, segment_id: int) -> int:
        for row, segment in enumerate(self._segments):
            if segment.id == segment_id:
                return row
        return -1

    def flash(self, segment_id: int) -> None:
        """Briefly mark the row a search hit pointed at, then let it settle."""
        row = self.row_for_segment(segment_id)
        if row < 0:
            return
        previous = self.row_for_segment(self._flash_id) if self._flash_id is not None else -1
        self._flash_id = segment_id
        if previous >= 0:
            self._emit_row(previous)
        self._emit_row(row)
        self._flash_timer.start(FLASH_MS)

    def find_rows(self, query: str) -> list[int]:
        needle = query.strip().casefold()
        if not needle:
            return []
        return [
            row
            for row, segment in enumerate(self._segments)
            if needle in row_text(segment).casefold()
        ]

    def _clear_flash(self, row: int) -> None:
        self._flash_id = None
        self._emit_row(row)

    def _finish_flash(self) -> None:
        row = self.row_for_segment(self._flash_id) if self._flash_id is not None else -1
        self._clear_flash(row)

    def _emit_row(self, row: int) -> None:
        index = self.index(row, 0)
        if index.isValid():
            self.dataChanged.emit(index, index)


class SegmentDelegate(QStyledItemDelegate):
    """Paints the mono timestamp gutter and the original/translation pair."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._width = 640
        self._mono = theme.mono_font(12)
        self._original = theme.ui_font("body")
        self._translation = theme.ui_font("body")
        self._translation.setWeight(QFont.Weight(500))

    def set_available_width(self, width: int) -> None:
        self._width = max(240, width)

    def sizeHint(self, option, index: QModelIndex | QPersistentModelIndex) -> QSize:
        segment = index.data(SEGMENT_ROLE)
        if segment is None:
            return QSize(self._width, 24)
        height = self._height_of(segment.text, self._original)
        translation = segment.display_translation
        if translation:
            height += PAIR_GAP + self._height_of(translation, self._translation)
        return QSize(self._width, height + ROW_PADDING_V * 2)

    def paint(self, painter, option, index: QModelIndex | QPersistentModelIndex) -> None:
        segment = index.data(SEGMENT_ROLE)
        if segment is None:
            return
        model = index.model()
        palette = theme.current()
        painter.save()
        self._paint_background(painter, option, palette, model, segment)
        self._paint_gutter(painter, option, palette, model, segment)

        left = option.rect.left() + GUTTER_WIDTH + ROW_PADDING_H
        top = option.rect.top() + ROW_PADDING_V
        width = self._text_width()
        translation = segment.display_translation
        original_colour = palette.text_secondary if translation else palette.text
        if segment.is_uncertain:
            # Dimmed rather than hidden: doubtful speech is still evidence.
            original_colour = palette.text_tertiary
        top = self._draw_block(
            painter, segment.text, self._original, original_colour, left, top, width
        )
        if translation:
            self._draw_block(
                painter,
                translation,
                self._translation,
                palette.text,
                left,
                top + PAIR_GAP,
                width,
            )
        painter.restore()

    # ---------- painting parts ----------

    def _text_width(self) -> int:
        return max(120, self._width - GUTTER_WIDTH - ROW_PADDING_H * 2)

    def _height_of(self, text: str, font: QFont) -> int:
        rect = QRect(0, 0, self._text_width(), 0)
        return QFontMetrics(font).boundingRect(rect, _WRAP, text).height()

    def _paint_background(self, painter, option, palette: Palette, model, segment) -> None:
        tint: QColor | None = None
        if hasattr(model, "is_flashing") and model.is_flashing(segment):
            tint = QColor(palette.accent)
            tint.setAlphaF(0.28)
        elif option.state & QStyle.StateFlag.State_Selected:
            tint = QColor(palette.accent)
            tint.setAlphaF(0.14)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            tint = QColor(palette.raised)
        if tint is not None:
            painter.fillRect(option.rect, tint)

    def _paint_gutter(self, painter, option, palette: Palette, model, segment) -> None:
        stamp = model.timestamp(segment) if hasattr(model, "timestamp") else ""
        painter.setFont(self._mono)
        painter.setPen(QColor(palette.text_tertiary))
        painter.drawText(
            QRect(
                option.rect.left() + ROW_PADDING_H,
                option.rect.top() + ROW_PADDING_V,
                _STAMP_WIDTH,
                QFontMetrics(self._mono).height(),
            ),
            _GUTTER_ALIGN,
            stamp,
        )

    def _draw_block(
        self, painter, text: str, font: QFont, colour: str, left: int, top: int, width: int
    ) -> int:
        painter.setFont(font)
        painter.setPen(QColor(colour))
        height = self._height_of(text, font)
        painter.drawText(QRect(left, top, width, height), _WRAP, text)
        return top + height
