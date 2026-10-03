"""Small widgets and helpers shared by the History list and the transcript viewer.

Kept apart from the two views so neither file grows into a place where layout,
copy, and painting all argue with each other.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .tokens import RADIUS, SPACE, Palette, hex_to_rgb


class Hairline(QFrame):
    """A one-pixel divider that follows the palette."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("hairline")
        self.setFixedHeight(1)


class ElidedLabel(QLabel):
    """A label that shortens its text with an ellipsis instead of stretching a row."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._full = text
        self.setMinimumWidth(40)

    def setText(self, text: str) -> None:
        self._full = text
        super().setText(text)
        self.update()

    def full_text(self) -> str:
        return self._full

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), int(self.alignment()), elided)
        painter.end()


class EmptyState(QWidget):
    """A named empty state: what this is, why it is empty, and the one way out.

    A bare empty box is the failure mode this class exists to prevent, so the
    action button is part of the widget rather than something a caller may
    forget to add.
    """

    action_clicked = Signal()

    def __init__(
        self,
        title: str,
        body: str,
        action: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.title = title
        self.body = body

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xxl"] * 2, SPACE["xl"], SPACE["xxl"])
        layout.setSpacing(SPACE["sm"])
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("heading")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # Wraps for the same reason the body does: a search query goes in this
        # title ('Nothing found for "..."'), so its width is user-controlled.
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        self._body_label = QLabel(body)
        self._body_label.setObjectName("secondary")
        self._body_label.setWordWrap(True)
        self._body_label.setMaximumWidth(420)
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._body_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.action_button: QPushButton | None = None
        if action:
            layout.addSpacing(SPACE["sm"])
            self.action_button = QPushButton(action)
            self.action_button.setObjectName("primary")
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.clicked.connect(self.action_clicked)
            layout.addWidget(self.action_button, alignment=Qt.AlignmentFlag.AlignHCenter)

    def set_copy(self, title: str, body: str) -> None:
        self.title = title
        self.body = body
        self._title_label.setText(title)
        self._body_label.setText(body)


class LinkButton(QPushButton):
    """A quiet text button that reads as a link rather than a control."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("quiet")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restyle(theme.current())

    def restyle(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"QPushButton#quiet {{ color: {palette.accent}; padding: 4px 2px; "
            f"border: none; background: transparent; }}"
        )


def confirm_delete(parent: QWidget | None, question: str) -> bool:
    """Ask before something irreversible. Returns True when the user said delete."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setWindowTitle("Delete transcript")
    box.setText(question)
    box.setInformativeText("This can't be undone.")
    delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is delete


def blend(foreground: str, background: str, alpha: float) -> str:
    """Flatten a tint to an opaque colour.

    Qt's rich text renderer ignores alpha in ``background-color``, so a 18%
    accent wash has to be mixed here or search highlights vanish in dark mode.
    """
    def mix(front: int, back: int) -> int:
        return max(0, min(255, round(back + (front - back) * alpha)))

    front_rgb = hex_to_rgb(foreground)
    back_rgb = hex_to_rgb(background)
    r, g, b = (mix(f, k) for f, k in zip(front_rgb, back_rgb, strict=True))
    return f"#{r:02X}{g:02X}{b:02X}"


def highlight_html(text: str, query: str, tint: str) -> str:
    """Escape ``text`` and wrap every word of ``query`` found in it with a tint."""
    words = [re.escape(word) for word in query.split() if word.strip()]
    if not words:
        return html.escape(text)
    pattern = re.compile("|".join(words), re.IGNORECASE)
    out: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        out.append(html.escape(text[cursor : match.start()]))
        out.append(
            f'<span style="background-color:{tint}; border-radius:{RADIUS["sm"]}px;">'
            f"{html.escape(match.group(0))}</span>"
        )
        cursor = match.end()
    out.append(html.escape(text[cursor:]))
    return "".join(out)
