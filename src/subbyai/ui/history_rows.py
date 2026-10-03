"""The two row shapes of the History list: a session, and a matched line.

Kept out of ``history_view`` so that file stays about which state is showing and
this one stays about what a row looks like.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.events import SessionInfo
from ..storage.session_store import SearchHit
from . import theme
from .exports import EXPORT_KINDS, clock_timestamp, language_pair, session_summary
from .history_widgets import ElidedLabel, highlight_html
from .tokens import RADIUS, SPACE, Palette


class SessionRow(QFrame):
    """One session. Actions stay hidden until the pointer or focus arrives."""

    open_requested = Signal(int)
    export_requested = Signal(int, str)
    delete_requested = Signal(int)
    hovered = Signal(int)

    def __init__(self, session: SessionInfo, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setFixedHeight(64)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        palette = theme.current()
        self.setStyleSheet(
            f"QFrame {{ border-radius: {RADIUS['md']}px; }}"
            f"QFrame:hover {{ background: {palette.raised}; }}"
            f"QFrame:focus {{ background: {palette.raised}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(session.title)
        title.setFont(theme.ui_font("body_strong"))
        text.addWidget(title)

        meta = QHBoxLayout()
        meta.setSpacing(SPACE["sm"])
        summary = QLabel(session_summary(session))
        summary.setObjectName("secondary")
        meta.addWidget(summary)
        languages = language_pair(session)
        if languages:
            meta.addWidget(chip(languages, palette))
        self._preview = ElidedLabel("")
        self._preview.setObjectName("tertiary")
        self._preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        meta.addWidget(self._preview, 1)
        text.addLayout(meta)
        layout.addLayout(text, 1)

        self._open = QPushButton("Open")
        self._open.setObjectName("quiet")
        self._open.clicked.connect(lambda: self.open_requested.emit(session.id))
        layout.addWidget(self._open)

        self._more = QPushButton("···")
        self._more.setObjectName("quiet")
        self._more.setFixedWidth(32)
        self._more.clicked.connect(self._show_menu)
        layout.addWidget(self._more)
        self._set_actions_visible(False)

    def set_preview(self, text: str) -> None:
        self._preview.setText(text)

    def _set_actions_visible(self, visible: bool) -> None:
        self._open.setVisible(visible)
        self._more.setVisible(visible)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        export = menu.addMenu("Export")
        for kind, label, _suffix in EXPORT_KINDS:
            export.addAction(label, lambda k=kind: self.export_requested.emit(self.session.id, k))
        menu.addAction("Delete…", lambda: self.delete_requested.emit(self.session.id))
        menu.exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))

    def enterEvent(self, event) -> None:
        self._set_actions_visible(True)
        self.hovered.emit(self.session.id)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.hasFocus():
            self._set_actions_visible(False)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        self._set_actions_visible(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._set_actions_visible(False)
        super().focusOutEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.open_requested.emit(self.session.id)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_requested.emit(self.session.id)
        elif key == Qt.Key.Key_Delete:
            self.delete_requested.emit(self.session.id)
        else:
            super().keyPressEvent(event)


class HitRow(QFrame):
    """One matched line inside a search result group."""

    clicked = Signal(int, int)

    def __init__(self, hit: SearchHit, query: str, tint: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.hit = hit
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        palette = theme.current()
        self.setStyleSheet(
            f"QFrame {{ border-radius: {RADIUS['md']}px; }}"
            f"QFrame:hover {{ background: {palette.raised}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])

        stamp = QLabel(clock_timestamp(hit.audio_start))
        stamp.setFont(theme.mono_font(12))
        stamp.setObjectName("tertiary")
        stamp.setFixedWidth(64)
        stamp.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(stamp)

        body = QLabel()
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        lines = [highlight_html(hit.text, query, tint)]
        if hit.translation:
            lines.append(
                f'<span style="color:{palette.text_secondary};">'
                f"{highlight_html(hit.translation, query, tint)}</span>"
            )
        body.setText("<br>".join(lines))
        layout.addWidget(body, 1)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.hit.session_id, self.hit.segment_id)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit(self.hit.session_id, self.hit.segment_id)
        else:
            super().keyPressEvent(event)


def day_header(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("micro")
    label.setContentsMargins(SPACE["md"], SPACE["md"], 0, SPACE["xs"])
    return label


def chip(text: str, palette: Palette) -> QLabel:
    chip = QLabel(text)
    chip.setObjectName("secondary")
    chip.setStyleSheet(
        f"QLabel {{ background: {palette.raised}; border-radius: {RADIUS['full']}px;"
        f" padding: 1px 8px; color: {palette.text_secondary}; }}"
    )
    return chip


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
