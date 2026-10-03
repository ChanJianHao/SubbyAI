"""Controls that paint themselves, and therefore theme themselves.

Each exposes ``refresh_theme`` so ``restyle`` can walk a subtree after the
palette flips. None of them decide anything: a card is told whether it is
chosen, a picker is told its entries. The sections own the meaning.

The level meter, privacy badge, toast and language picker deliberately are
*not* here — ``ui.widgets`` already owns those and the Live view and onboarding
use them, so a second copy would be a second thing to keep in step.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..tokens import RADIUS, SPACE

__all__ = [
    "CardGrid",
    "ChoiceCard",
    "StatusDot",
]


class StatusDot(QWidget):
    """A small dot. Takes a role — ``ok``/``idle``/``busy``/``bad`` — not a colour."""

    def __init__(self, state: str = "idle", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setFixedSize(10, 10)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        palette = theme.current()
        colour = {
            "ok": palette.success,
            "bad": palette.error,
            "busy": palette.warning,
        }.get(self._state, palette.text_tertiary)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()


class ChoiceCard(QFrame):
    """A selectable card: title, blurb, and a quiet meta line.

    An unavailable card stays visible and carries the reason. Hiding an option
    the machine cannot run leaves the user wondering what they are missing.
    """

    clicked = Signal()

    def __init__(
        self,
        title: str,
        blurb: str = "",
        meta: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("choicecard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._checked = False
        self._reason = ""

        column = QVBoxLayout(self)
        column.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        column.setSpacing(SPACE["xs"])
        self._title = QLabel(title, self)
        self._title.setFont(theme.ui_font("body_strong"))
        self._badge = QLabel("", self)
        self._badge.setObjectName("accent")
        self._badge.setWordWrap(True)
        self._badge.setVisible(False)
        self._blurb = QLabel(blurb, self)
        self._blurb.setObjectName("secondary")
        self._blurb.setWordWrap(True)
        self._blurb.setVisible(bool(blurb))
        self._meta = QLabel(meta, self)
        self._meta.setObjectName("tertiary")
        self._meta.setWordWrap(True)
        self._meta.setVisible(bool(meta))
        column.addWidget(self._title)
        column.addWidget(self._badge)
        column.addWidget(self._blurb)
        column.addWidget(self._meta)
        column.addStretch(1)
        self.refresh_theme()

    @property
    def title(self) -> str:
        return self._title.text()

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool) -> None:
        self._checked = bool(checked)
        self.refresh_theme()

    def set_badge(self, text: str) -> None:
        """A short line above the blurb. Empty clears it."""
        self._badge.setText(text)
        self._badge.setVisible(bool(text))

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)
        self._meta.setVisible(bool(text))

    def set_unavailable(self, reason: str) -> None:
        """An empty reason means available again."""
        self._reason = reason
        self.setEnabled(not reason)
        if reason:
            self.set_meta(reason)
        self.refresh_theme()

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    def refresh_theme(self) -> None:
        palette = theme.current()
        border = palette.accent if self._checked else palette.hairline
        background = palette.accent_subtle if self._checked else palette.surface
        self.setStyleSheet(
            f"QFrame#choicecard {{ background: {background}; border: 1px solid {border};"
            f" border-radius: {RADIUS['lg']}px; }}"
            f"QFrame#choicecard:focus {{ border: 2px solid {palette.accent}; }}"
        )
        self._title.setStyleSheet(
            f"color: {palette.text_disabled if self._reason else palette.text};"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class CardGrid(QWidget):
    """An exclusive set of :class:`ChoiceCard` in a fixed column count."""

    chosen = Signal(str)

    def __init__(self, columns: int = 3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = max(1, columns)
        self._cards: dict[str, ChoiceCard] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(SPACE["sm"])
        self._value = ""

    def add_card(self, value: str, card: ChoiceCard) -> ChoiceCard:
        index = len(self._cards)
        self._grid.addWidget(card, index // self._columns, index % self._columns)
        self._cards[value] = card
        card.clicked.connect(lambda: self._pick(value))
        return card

    def cards(self) -> dict[str, ChoiceCard]:
        return dict(self._cards)

    def card(self, value: str) -> ChoiceCard | None:
        return self._cards.get(value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        """Select without announcing — for loading the stored setting."""
        self._value = value
        for key, card in self._cards.items():
            card.set_checked(key == value)

    def _pick(self, value: str) -> None:
        if value == self._value:
            return
        self.set_value(value)
        self.chosen.emit(value)
