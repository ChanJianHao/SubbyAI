"""History: find something you heard.

Two decisions carry this screen. Search is debounced rather than live because a
full-text query per keystroke turns a fast index into a stuttering one on a
laptop. And every empty state names itself and offers the single action that
fixes it — "no results" and "history is switched off" are different problems and
must never look like the same blank box.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.events import SessionInfo
from ..storage.session_store import SearchHit, SessionStore
from . import theme
from .exports import day_label, human_bytes, save_export
from .history_rows import HitRow, SessionRow, clear_layout, day_header
from .history_widgets import EmptyState, Hairline, LinkButton, blend, confirm_delete
from .tokens import SPACE, Palette

SEARCH_DEBOUNCE_MS = 300
HITS_PER_SESSION = 3
_PREVIEW_BUDGET = 20

SEARCH_PLACEHOLDER = "Search everything you've captioned"
STORAGE_PREFIX = "Transcripts stay on this device"

EMPTY_NOTHING = (
    "Your sessions will appear here",
    "Every captioning session is saved as a searchable transcript — on this device only.",
)
EMPTY_DISABLED = (
    "Session history is off",
    "Turn it on to keep searchable transcripts of what you caption. "
    "Everything stays on this device.",
)
EMPTY_NO_RESULTS_BODY = "Try fewer words, or search in the original language."


def group_sessions(
    sessions: list[SessionInfo], now: float | None = None
) -> list[tuple[str, list[SessionInfo]]]:
    """Split a newest-first session list into day-labelled runs."""
    groups: list[tuple[str, list[SessionInfo]]] = []
    for session in sessions:
        label = day_label(session.started_at, now)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(session)
        else:
            groups.append((label, [session]))
    return groups


def group_hits(
    hits: list[SearchHit], per_session: int = HITS_PER_SESSION
) -> list[tuple[int, str, list[SearchHit]]]:
    """Collect search hits under their session, capped so one session can't fill the list."""
    groups: list[tuple[int, str, list[SearchHit]]] = []
    index: dict[int, list[SearchHit]] = {}
    for hit in hits:
        bucket = index.get(hit.session_id)
        if bucket is None:
            bucket = []
            index[hit.session_id] = bucket
            groups.append((hit.session_id, hit.session_title, bucket))
        if len(bucket) < per_session:
            bucket.append(hit)
    return groups


class HistoryView(QWidget):
    """The session list, its search mode, and the storage footer."""

    session_opened = Signal(int)
    segment_opened = Signal(int, int)
    enable_history_requested = Signal()
    manage_storage_requested = Signal()
    session_exported = Signal(int, str)

    def __init__(self, store: SessionStore, parent: QWidget | None = None):
        super().__init__(parent)
        self._store = store
        self._sessions: list[SessionInfo] = []
        self._hits: list[SearchHit] = []
        self._query = ""
        self._history_enabled = True
        self._previews: dict[int, str] = {}
        self._rows: list[SessionRow] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._fill_previews)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._run_search)

        self._build()
        theme.subscribe(self._restyle)
        self.refresh()

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], 0)
        root.setSpacing(SPACE["lg"])

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(40)
        self.search_field.textChanged.connect(self._on_search_text)
        self.search_field.returnPressed.connect(self._run_search)
        root.addWidget(self.search_field)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._list = QVBoxLayout(self._host)
        self._list.setContentsMargins(0, 0, SPACE["sm"], SPACE["lg"])
        self._list.setSpacing(SPACE["xs"])
        self._list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, 1)

        root.addWidget(Hairline())
        footer = QHBoxLayout()
        footer.setContentsMargins(0, SPACE["sm"], 0, SPACE["sm"])
        self._storage_label = QLabel()
        self._storage_label.setObjectName("secondary")
        # Carries a byte count, so its length varies; let it wrap rather
        # than widen the window.
        self._storage_label.setWordWrap(True)
        footer.addWidget(self._storage_label)
        footer.addStretch(1)
        self._manage = LinkButton("Manage storage")
        self._manage.clicked.connect(self.manage_storage_requested)
        footer.addWidget(self._manage)
        root.addLayout(footer)

    # ---------- public API ----------

    def refresh(self) -> None:
        """Reload from the store and repaint whichever mode is showing."""
        self._previews.clear()
        self._sessions = self._store.list_sessions() if self._history_enabled else []
        if self._query:
            self._run_search()
        else:
            self._render()

    def set_history_enabled(self, enabled: bool) -> None:
        """Told by the shell, because the view never reads settings itself."""
        if enabled == self._history_enabled:
            return
        self._history_enabled = enabled
        self.refresh()

    def sessions(self) -> list[SessionInfo]:
        return list(self._sessions)

    # ---------- search ----------

    def _on_search_text(self, text: str) -> None:
        self._query = text.strip()
        if not self._query:
            self._debounce.stop()
            self._hits = []
            self._render()
            return
        self._debounce.start()

    def _run_search(self) -> None:
        self._debounce.stop()
        if not self._query or not self._history_enabled:
            self._hits = []
        else:
            self._hits = self._store.search(self._query)
        self._render()

    # ---------- rendering ----------

    def _render(self) -> None:
        clear_layout(self._list)
        self._rows = []
        if not self._history_enabled:
            self._show_empty(*EMPTY_DISABLED, action="Turn on history")
        elif self._query:
            self._render_hits()
        elif not self._sessions:
            self._show_empty(*EMPTY_NOTHING)
        else:
            self._render_sessions()
        self._storage_label.setText(
            f"{STORAGE_PREFIX} · {human_bytes(self._store.storage_bytes())} used"
        )
        self._preview_timer.start(0)

    def _render_sessions(self) -> None:
        for label, sessions in group_sessions(self._sessions):
            self._list.addWidget(day_header(label))
            for session in sessions:
                row = SessionRow(session)
                row.open_requested.connect(self.session_opened)
                row.export_requested.connect(self._export_session)
                row.delete_requested.connect(self._delete_session)
                row.hovered.connect(self._ensure_preview)
                self._list.addWidget(row)
                self._rows.append(row)

    def _render_hits(self) -> None:
        if not self._hits:
            self._show_empty(f'Nothing found for "{self._query}"', EMPTY_NO_RESULTS_BODY)
            return
        palette = theme.current()
        tint = blend(palette.accent, palette.surface, 0.18)
        for _session_id, title, hits in group_hits(self._hits):
            self._list.addWidget(day_header(title))
            for hit in hits:
                row = HitRow(hit, self._query, tint)
                row.clicked.connect(self.segment_opened)
                self._list.addWidget(row)

    def _show_empty(self, title: str, body: str, action: str = "") -> None:
        empty = EmptyState(title, body, action)
        if action:
            empty.action_clicked.connect(self.enable_history_requested)
        self._list.addWidget(empty)

    # ---------- previews ----------

    def _fill_previews(self) -> None:
        """First transcript lines cost a query each, so only the visible run gets one."""
        for row in self._rows[:_PREVIEW_BUDGET]:
            self._ensure_preview(row.session.id)

    def _ensure_preview(self, session_id: int) -> None:
        if session_id not in self._previews:
            segments = self._store.segments(session_id)
            self._previews[session_id] = segments[0].text if segments else ""
        text = self._previews[session_id]
        for row in self._rows:
            if row.session.id == session_id:
                row.set_preview(text)

    # ---------- actions ----------

    def _export_session(self, session_id: int, kind: str) -> None:
        path = save_export(self, self._store, session_id, kind)
        if path is not None:
            self.session_exported.emit(session_id, str(path))

    def _delete_session(self, session_id: int) -> None:
        session = next((s for s in self._sessions if s.id == session_id), None)
        title = session.title if session else "this transcript"
        if not confirm_delete(self, f"Delete “{title}”?"):
            return
        self._store.delete_session(session_id)
        self.refresh()

    def _restyle(self, palette: Palette) -> None:
        self._manage.restyle(palette)
        self._render()
