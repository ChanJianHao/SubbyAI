"""The transcript viewer: one session, read back line by line.

Header, find bar, exports, and the Ask AI drawer. The body itself — the
virtualized list model and its painter — lives in ``transcript_model.py``, and
the file formats live in ``exports.py`` as pure functions so they can be tested
without a window.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.events import SessionInfo
from ..storage.session_store import SessionStore
from . import theme
from .exports import (
    EXPORT_KINDS,
    export_markdown,
    export_srt,
    export_text,
    save_export,
    session_meta_line,
)
from .history_widgets import Hairline, confirm_delete
from .tokens import SPACE, Palette
from .transcript_ask import AskAiDrawer, Assistant, ask_button_label
from .transcript_model import SEGMENT_ROLE, SegmentDelegate, TranscriptModel, row_text

__all__ = [
    "SEGMENT_ROLE",
    "SegmentDelegate",
    "TranscriptModel",
    "TranscriptView",
    "export_markdown",
    "export_srt",
    "export_text",
    "row_text",
]

DELETE_QUESTION = "Delete this transcript?"


class TranscriptView(QWidget):
    """Header, find bar, the virtualized body, and the Ask AI drawer."""

    back_requested = Signal()
    session_renamed = Signal(int, str)
    session_deleted = Signal(int)
    cloud_consent_granted = Signal()

    def __init__(
        self,
        store: SessionStore,
        parent: QWidget | None = None,
        assistant: Assistant | None = None,
    ):
        super().__init__(parent)
        self._store = store
        self._session: SessionInfo | None = None
        self._session_id: int | None = None
        self._matches: list[int] = []
        self._match_index = -1

        self.model = TranscriptModel(self)
        self._delegate = SegmentDelegate(self)
        self._build(assistant)
        self._install_shortcuts()
        theme.subscribe(self._restyle)

    # ---------- construction ----------

    def _build(self, assistant: Assistant | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["lg"])
        root.setSpacing(SPACE["sm"])

        header = QHBoxLayout()
        header.setSpacing(SPACE["sm"])
        back = QPushButton("←")
        back.setObjectName("quiet")
        back.setFixedWidth(32)
        back.setToolTip("Back to history")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)

        self.title_edit = QLineEdit()
        self.title_edit.setReadOnly(True)
        self.title_edit.setCursorPosition(0)
        self.title_edit.returnPressed.connect(self.commit_rename)
        self.title_edit.editingFinished.connect(self.commit_rename)
        self.title_edit.installEventFilter(self)
        header.addWidget(self.title_edit, 1)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_selection)
        header.addWidget(self.copy_button)

        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self.open_export_menu)
        header.addWidget(self.export_button)

        self.find_button = QPushButton("Find")
        self.find_button.clicked.connect(self.open_find)
        header.addWidget(self.find_button)

        self.ask_button = QPushButton(ask_button_label(assistant))
        self.ask_button.clicked.connect(self.toggle_drawer)
        self.ask_button.setVisible(assistant is not None)
        header.addWidget(self.ask_button)

        self.more_button = QPushButton("···")
        self.more_button.setObjectName("quiet")
        self.more_button.setFixedWidth(32)
        self.more_button.clicked.connect(self._open_more_menu)
        header.addWidget(self.more_button)
        root.addLayout(header)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("secondary")
        self.meta_label.setContentsMargins(40, 0, 0, 0)
        root.addWidget(self.meta_label)

        self.find_bar = self._build_find_bar()
        self.find_bar.hide()
        root.addWidget(self.find_bar)
        root.addWidget(Hairline())

        body = QHBoxLayout()
        body.setSpacing(0)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(self._delegate)
        self.list.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(False)
        self.list.setMouseTracking(True)
        self.list.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.selectionModel().selectionChanged.connect(self._on_selection_changed)
        body.addWidget(self.list, 1)

        self.drawer = AskAiDrawer(self, assistant)
        self.drawer.set_sources(self.transcript_text, self.selection_text)
        self.drawer.close_requested.connect(self.drawer.hide)
        self.drawer.consent_granted.connect(self.cloud_consent_granted)
        self.drawer.hide()
        body.addWidget(self.drawer)
        root.addLayout(body, 1)
        self._restyle(theme.current())

    def _build_find_bar(self) -> QFrame:
        bar = QFrame()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(40, 0, 0, 0)
        layout.setSpacing(SPACE["sm"])
        self.find_field = QLineEdit()
        self.find_field.setPlaceholderText("Find in this transcript")
        self.find_field.textChanged.connect(self._update_matches)
        self.find_field.returnPressed.connect(self.find_next)
        layout.addWidget(self.find_field, 1)
        self.match_label = QLabel()
        self.match_label.setObjectName("secondary")
        layout.addWidget(self.match_label)
        previous = QPushButton("↑")
        previous.setObjectName("quiet")
        previous.clicked.connect(self.find_previous)
        layout.addWidget(previous)
        following = QPushButton("↓")
        following.setObjectName("quiet")
        following.clicked.connect(self.find_next)
        layout.addWidget(following)
        close = QPushButton("✕")
        close.setObjectName("quiet")
        close.clicked.connect(self.close_find)
        layout.addWidget(close)
        return bar

    def _install_shortcuts(self) -> None:
        scope = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for sequence, slot in (
            ("Ctrl+F", self.open_find),
            ("Ctrl+E", self.open_export_menu),
            ("Ctrl+C", self.copy_selection),
            ("F2", self.begin_rename),
            ("Alt+Left", self.back_requested.emit),
            ("Shift+Return", self.find_previous),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(scope)
            shortcut.activated.connect(slot)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(scope)
        escape.activated.connect(self._on_escape)

    # ---------- public API ----------

    def load_session(self, session_id: int, focus_segment_id: int | None = None) -> None:
        """Show a session, optionally landing on one line and flashing it."""
        self._session_id = session_id
        self._session = self._store.get_session(session_id)
        self.model.set_segments(self._store.segments(session_id), self._session)
        self.title_edit.setText(self._session.title if self._session else "Session")
        self.title_edit.setCursorPosition(0)
        self.meta_label.setText(session_meta_line(self._session))
        self.close_find()
        self._delegate.set_available_width(self.list.viewport().width())
        if focus_segment_id is not None:
            self.focus_segment(focus_segment_id)

    def focus_segment(self, segment_id: int) -> None:
        row = self.model.row_for_segment(segment_id)
        if row < 0:
            return
        index = self.model.index(row, 0)
        self.list.setCurrentIndex(index)
        self.list.scrollTo(index, QListView.ScrollHint.PositionAtCenter)
        self.model.flash(segment_id)

    def set_assistant(self, assistant: Assistant | None) -> None:
        self.drawer.set_assistant(assistant)
        self.ask_button.setText(ask_button_label(assistant))
        self.ask_button.setVisible(assistant is not None)
        if assistant is None:
            self.drawer.hide()

    def set_cloud_consent(self, given: bool) -> None:
        self.drawer.set_consent(given)

    def transcript_text(self) -> str:
        return export_text(self.model.segments())

    def selection_text(self) -> str:
        rows = sorted(index.row() for index in self.list.selectionModel().selectedIndexes())
        segments = self.model.segments()
        return "\n".join(row_text(segments[row]) for row in rows if row < len(segments))

    def copy_selection(self) -> None:
        """Copy the selected lines, or the whole transcript when nothing is selected."""
        text = self.selection_text() or self.transcript_text()
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def toggle_drawer(self) -> None:
        self.drawer.setVisible(not self.drawer.isVisible())

    # ---------- rename ----------

    def begin_rename(self) -> None:
        self.title_edit.setReadOnly(False)
        self.title_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.title_edit.selectAll()
        self._restyle(theme.current())

    def commit_rename(self) -> None:
        if self.title_edit.isReadOnly() or self._session_id is None:
            return
        title = self.title_edit.text().strip()
        self.title_edit.setReadOnly(True)
        self._restyle(theme.current())
        if not title or (self._session and title == self._session.title):
            self.title_edit.setText(self._session.title if self._session else "")
            return
        self._store.rename_session(self._session_id, title)
        self._session = self._store.get_session(self._session_id)
        self.session_renamed.emit(self._session_id, title)

    # ---------- find ----------

    def open_find(self) -> None:
        self.find_bar.show()
        self.find_field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.find_field.selectAll()

    def close_find(self) -> None:
        self.find_bar.hide()
        self.find_field.clear()
        self._matches = []
        self._match_index = -1
        self.match_label.clear()

    def find_next(self) -> None:
        self._step_match(1)

    def find_previous(self) -> None:
        self._step_match(-1)

    def match_count(self) -> int:
        return len(self._matches)

    def _update_matches(self, text: str) -> None:
        self._matches = self.model.find_rows(text)
        self._match_index = 0 if self._matches else -1
        self._show_match_label()
        if self._matches:
            self._scroll_to_match()

    def _step_match(self, direction: int) -> None:
        if not self._matches:
            return
        self._match_index = (self._match_index + direction) % len(self._matches)
        self._show_match_label()
        self._scroll_to_match()

    def _scroll_to_match(self) -> None:
        index = self.model.index(self._matches[self._match_index], 0)
        self.list.setCurrentIndex(index)
        self.list.scrollTo(index, QListView.ScrollHint.PositionAtCenter)

    def _show_match_label(self) -> None:
        if not self.find_field.text().strip():
            self.match_label.clear()
        elif not self._matches:
            self.match_label.setText("No matches")
        else:
            self.match_label.setText(f"{self._match_index + 1} of {len(self._matches)}")

    # ---------- menus ----------

    def open_export_menu(self) -> None:
        if self._session_id is None:
            return
        menu = QMenu(self)
        for kind, label, _suffix in EXPORT_KINDS:
            menu.addAction(label, lambda k=kind: self._export(k))
        menu.exec(self.export_button.mapToGlobal(self.export_button.rect().bottomLeft()))

    def _export(self, kind: str) -> None:
        if self._session_id is not None:
            save_export(self, self._store, self._session_id, kind)

    def _open_more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Rename session", self.begin_rename)
        menu.addAction("Delete session…", self.delete_session)
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def delete_session(self) -> None:
        if self._session_id is None or not confirm_delete(self, DELETE_QUESTION):
            return
        session_id = self._session_id
        self._store.delete_session(session_id)
        self._session_id = None
        self._session = None
        self.model.set_segments([])
        self.session_deleted.emit(session_id)
        self.back_requested.emit()

    # ---------- events ----------

    def _on_selection_changed(self, *_args) -> None:
        self.drawer.set_has_selection(bool(self.list.selectionModel().selectedIndexes()))

    def eventFilter(self, watched, event) -> bool:
        """Click-to-rename, without subclassing QLineEdit for one gesture."""
        if watched is self.title_edit and event.type() == QEvent.Type.MouseButtonDblClick:
            self.begin_rename()
            return True
        return super().eventFilter(watched, event)

    def _on_escape(self) -> None:
        if self.find_bar.isVisible():
            self.close_find()
        elif self.drawer.isVisible():
            self.drawer.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._delegate.set_available_width(self.list.viewport().width())
        self.list.doItemsLayout()

    def _restyle(self, palette: Palette) -> None:
        editing = not self.title_edit.isReadOnly()
        border = palette.accent if editing else "transparent"
        background = palette.input_bg if editing else "transparent"
        self.title_edit.setStyleSheet(
            f"QLineEdit {{ background: {background}; border: 1px solid {border};"
            f" font-size: 20px; font-weight: 600; color: {palette.text}; padding: 2px 6px; }}"
        )
        self.list.viewport().update()
