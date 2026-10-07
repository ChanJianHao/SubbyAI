"""Opt-in local SQLite/FTS caption history with a bounded background writer."""

from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..core.events import CaptionSegment, SessionInfo, TranslationState

log = logging.getLogger(__name__)

_FLUSH_INTERVAL = 3.0
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    source_language TEXT,
    target_language TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    audio_start REAL NOT NULL,
    audio_duration REAL NOT NULL,
    created_at REAL NOT NULL,
    text TEXT NOT NULL,
    translation TEXT,
    language TEXT,
    confidence REAL
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, translation, content='segments', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text, translation)
    VALUES (new.id, new.text, coalesce(new.translation, ''));
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, translation)
    VALUES ('delete', old.id, old.text, coalesce(old.translation, ''));
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, translation)
    VALUES ('delete', old.id, old.text, coalesce(old.translation, ''));
    INSERT INTO segments_fts(rowid, text, translation)
    VALUES (new.id, new.text, coalesce(new.translation, ''));
END;
"""


@dataclass(frozen=True, slots=True)
class SearchHit:
    session_id: int
    session_title: str
    segment_id: int
    audio_start: float
    text: str
    translation: str | None


class SessionStore:
    """Thread-safe store with a background writer.

    Reads happen on the calling thread using short-lived connections; writes go
    through a queue so neither the caption pipeline nor the UI ever blocks on
    disk.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        flush_interval: float = _FLUSH_INTERVAL,
        *,
        lazy: bool = False,
        on_write_failure: Callable[[], None] | None = None,
    ):
        self._path = db_path or paths.sessions_db()
        self._flush_interval = flush_interval
        self._queue: queue.Queue[object] = queue.Queue(maxsize=512)
        self.dropped_writes = 0
        self._failure_pending = False
        self._failure_lock = threading.Lock()
        self._on_write_failure = on_write_failure
        self._writer: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.recovered_backup: Path | None = None
        self._initialized = False
        if not lazy or self._path.exists():
            self._initialize()

    def _initialize(self) -> None:
        if self._initialized:
            return
        try:
            self._init_schema()
        except sqlite3.DatabaseError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if code not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                raise
            # Preserve damaged data and journals; never overwrite the only copy.
            backup = self._path.with_name(f"{self._path.name}.damaged-{time.time_ns()}")
            backup.mkdir(mode=0o700)
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(self._path) + suffix)
                if source.is_file():
                    source.rename(backup / source.name)
            self.recovered_backup = backup
            self._init_schema()
            log.warning("Damaged transcript storage preserved in a local recovery folder")
        self._initialized = True

    # ---------- lifecycle ----------

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that is committed *and closed* on exit.

        sqlite3's own context manager only ends the transaction — it leaves the
        connection open. Left as-is, every call leaked a connection and kept a
        Windows file lock on the database.
        """
        conn = sqlite3.connect(self._path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA secure_delete=ON")
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        if self._path.is_symlink() or self._path.is_junction():
            raise OSError("The transcript database must be a regular file, not a filesystem link.")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(segments)")}
            if "event_id" not in columns:
                conn.execute("ALTER TABLE segments ADD COLUMN event_id INTEGER")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_event "
                "ON segments(session_id, event_id)"
            )

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def start_writer(self) -> None:
        if self._writer and self._writer.is_alive():
            return
        self._stop.clear()
        self._writer = threading.Thread(target=self._writer_loop, name="subbyai-store", daemon=True)
        self._writer.start()

    def stop_writer(self, timeout: float = 5.0) -> None:
        if not self._writer:
            return
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        self._writer.join(timeout=timeout)
        if not self._writer.is_alive():
            self._writer = None

    def close(self) -> None:
        self.stop_writer()

    # ---------- sessions ----------

    def create_session(
        self, title: str, source_language: str = "", target_language: str = ""
    ) -> int:
        self._initialize()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (title, started_at, source_language, target_language)"
                " VALUES (?, ?, ?, ?)",
                (title, time.time(), source_language or None, target_language or None),
            )
            return int(cursor.lastrowid)

    def end_session(self, session_id: int, *, keep_empty: bool = False) -> None:
        failure = None
        try:
            self.flush()
        except HistoryWriteError as exc:
            failure = exc
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (time.time(), session_id))
            # Drop sessions that captured nothing rather than littering History.
            if not keep_empty:
                conn.execute(
                    "DELETE FROM sessions WHERE id = ?"
                    " AND NOT EXISTS (SELECT 1 FROM segments WHERE session_id = ?)",
                    (session_id, session_id),
                )
        if failure is not None:
            raise failure

    def rename_session(self, session_id: int, title: str) -> None:
        if not self._initialized:
            return
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))

    def delete_session(self, session_id: int) -> None:
        if not self._initialized:
            return
        self.flush()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def delete_all(self) -> None:
        if not self._initialized:
            return
        self.flush()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM segments")
            conn.execute("DELETE FROM sessions")
            conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")

    def list_sessions(self, limit: int = 200) -> list[SessionInfo]:
        if not self._initialized:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, COUNT(g.id) AS segment_count,
                       COALESCE(SUM(CASE WHEN LENGTH(TRIM(g.text)) > 0
                           THEN LENGTH(TRIM(g.text)) - LENGTH(REPLACE(TRIM(g.text), ' ', '')) + 1
                           WHEN LENGTH(TRIM(g.translation)) > 0 THEN LENGTH(TRIM(g.translation))
                               - LENGTH(REPLACE(TRIM(g.translation), ' ', '')) + 1
                           ELSE 0 END), 0)
                           AS word_count
                FROM sessions s LEFT JOIN segments g ON g.session_id = s.id
                GROUP BY s.id ORDER BY s.started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def get_session(self, session_id: int) -> SessionInfo | None:
        if not self._initialized:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, COUNT(g.id) AS segment_count,
                       COALESCE(SUM(CASE WHEN LENGTH(TRIM(g.text)) > 0
                           THEN LENGTH(TRIM(g.text)) - LENGTH(REPLACE(TRIM(g.text), ' ', '')) + 1
                           WHEN LENGTH(TRIM(g.translation)) > 0 THEN LENGTH(TRIM(g.translation))
                               - LENGTH(REPLACE(TRIM(g.translation), ' ', '')) + 1
                           ELSE 0 END), 0)
                           AS word_count
                FROM sessions s LEFT JOIN segments g ON g.session_id = s.id
                WHERE s.id = ? GROUP BY s.id
                """,
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row else None

    # ---------- segments ----------

    def add_segment(self, session_id: int, segment: CaptionSegment) -> None:
        """Queue a segment for the writer thread. Never blocks."""
        try:
            self._queue.put_nowait((session_id, segment))
        except queue.Full:
            self._report_loss(1)

    def update_segment(self, session_id: int, segment: CaptionSegment) -> None:
        """Upsert by session and event id, including updates ahead of a batch flush."""
        self.add_segment(session_id, segment)

    def segments(self, session_id: int) -> list[CaptionSegment]:
        if not self._initialized:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM segments WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        return [_segment_from_row(row) for row in rows]

    def search(self, query: str, limit: int = 200) -> list[SearchHit]:
        if not self._initialized:
            return []
        query = query.strip()
        if not query:
            return []
        match = " ".join(f'"{token}"*' for token in query.split())
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT g.id AS segment_id, g.session_id, g.audio_start, g.text,
                           g.translation, s.title AS session_title
                    FROM segments_fts f
                    JOIN segments g ON g.id = f.rowid
                    JOIN sessions s ON s.id = g.session_id
                    WHERE segments_fts MATCH ?
                    ORDER BY g.session_id DESC, g.id LIMIT ?
                    """,
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                log.debug("Search query rejected; falling back to LIKE")
                like = f"%{query}%"
                rows = conn.execute(
                    """
                    SELECT g.id AS segment_id, g.session_id, g.audio_start, g.text,
                           g.translation, s.title AS session_title
                    FROM segments g JOIN sessions s ON s.id = g.session_id
                    WHERE g.text LIKE ? OR g.translation LIKE ?
                    ORDER BY g.session_id DESC, g.id LIMIT ?
                    """,
                    (like, like, limit),
                ).fetchall()
        return [
            SearchHit(
                session_id=row["session_id"],
                session_title=row["session_title"],
                segment_id=row["segment_id"],
                audio_start=row["audio_start"],
                text=row["text"],
                translation=row["translation"],
            )
            for row in rows
        ]

    # ---------- maintenance ----------

    def storage_bytes(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def apply_retention(self, retention_days: int, *, active_session: int | None = None) -> int:
        """Delete sessions older than the policy. Returns how many went."""
        if not self._initialized or retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        with self._lock, self._connect() as conn:
            old = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM sessions WHERE started_at < ? AND id != ?",
                    (cutoff, active_session or -1),
                ).fetchall()
            ]
            if old:
                conn.executemany("DELETE FROM segments WHERE session_id = ?", [(i,) for i in old])
                conn.executemany("DELETE FROM sessions WHERE id = ?", [(i,) for i in old])
        return len(old)

    def flush(self, timeout: float = 5.0) -> None:
        """Block until everything queued so far has actually reached the database."""
        if self._writer and self._writer.is_alive():
            barrier = _FlushBarrier()
            deadline = time.monotonic() + timeout
            try:
                self._queue.put(barrier, timeout=max(0, timeout))
            except queue.Full as exc:
                raise TimeoutError("History storage is busy. Try again shortly.") from exc
            if not barrier.done.wait(max(0, deadline - time.monotonic())):
                raise TimeoutError("History storage hasn't finished writing. Try again shortly.")
            if barrier.failed:
                raise HistoryWriteError()
        else:
            self._drain_once()
            if self._take_failure():
                raise HistoryWriteError()

    # ---------- writer ----------

    def _writer_loop(self) -> None:
        pending: list[tuple[int, CaptionSegment]] = []
        last_commit = time.monotonic()
        while True:
            timeout = max(0.05, self._flush_interval - (time.monotonic() - last_commit))
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None if self._stop.is_set() else _TICK

            if item is None:  # shutdown
                self._commit(pending)
                return
            if isinstance(item, _FlushBarrier):
                self._commit(pending)
                pending = []
                last_commit = time.monotonic()
                item.failed = self._take_failure()
                item.done.set()
                continue
            if item is not _TICK:
                pending.append(item)  # type: ignore[arg-type]

            due = time.monotonic() - last_commit >= self._flush_interval
            if pending and (due or len(pending) >= 32):
                self._commit(pending)
                pending = []
                last_commit = time.monotonic()

    def _drain_once(self) -> None:
        pending = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, _FlushBarrier):
                item.done.set()
            elif item is not None and item is not _TICK:
                pending.append(item)
        self._commit(pending)

    def _commit(self, pending: list[tuple[int, CaptionSegment]]) -> None:
        if not pending:
            return
        rows = [
            (
                session_id,
                seg.id,
                seg.audio_start,
                seg.audio_duration,
                seg.created_at,
                seg.text,
                seg.translation if seg.translation_state is TranslationState.DONE else None,
                seg.language,
                seg.confidence,
            )
            for session_id, seg in pending
        ]
        try:
            with self._lock, self._connect() as conn:
                conn.executemany(
                    "INSERT INTO segments (session_id, event_id, audio_start, audio_duration,"
                    " created_at,"
                    " text, translation, language, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(session_id, event_id) DO UPDATE SET "
                    "translation=excluded.translation",
                    rows,
                )
        except (sqlite3.Error, OSError):
            self._report_loss(len(rows))

    def _report_loss(self, count: int) -> None:
        with self._failure_lock:
            self.dropped_writes += count
            notify = not self._failure_pending
            self._failure_pending = True
        log.warning("History storage could not save %d write(s)", count)
        if notify and self._on_write_failure is not None:
            try:
                self._on_write_failure()
            except Exception:
                log.warning("Could not deliver the history storage warning")

    def _take_failure(self) -> bool:
        with self._failure_lock:
            failed = self._failure_pending
            self._failure_pending = False
            return failed


class HistoryWriteError(OSError):
    def __init__(self) -> None:
        super().__init__(
            "Some captions could not be saved. Check disk space and folder permissions."
        )


class _FlushBarrier:
    """Marker that makes the writer commit everything queued before it."""

    __slots__ = ("done", "failed")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.failed = False


_TICK: object = object()  # "no item arrived", distinct from shutdown


def _session_from_row(row: sqlite3.Row) -> SessionInfo:
    return SessionInfo(
        id=row["id"],
        title=row["title"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        source_language=row["source_language"],
        target_language=row["target_language"],
        # .keys() is load-bearing: sqlite3.Row.__contains__ tests *values*, not
        # column names, so dropping it would silently zero both counts.
        segment_count=row["segment_count"] if "segment_count" in row.keys() else 0,  # noqa: SIM118
        word_count=int(row["word_count"]) if "word_count" in row.keys() else 0,  # noqa: SIM118
    )


def _segment_from_row(row: sqlite3.Row) -> CaptionSegment:
    return CaptionSegment(
        id=row["id"],
        text=row["text"],
        translation=row["translation"],
        translation_state=(TranslationState.DONE if row["translation"] else TranslationState.NONE),
        language=row["language"],
        confidence=row["confidence"] if row["confidence"] is not None else 1.0,
        audio_start=row["audio_start"],
        audio_duration=row["audio_duration"],
        created_at=row["created_at"],
    )


def iter_export(segments: list[CaptionSegment]) -> Iterator[str]:
    """Plain-text export lines."""
    for seg in segments:
        stamp = _timestamp(seg.audio_start)
        yield f"[{stamp}] {seg.text}"
        if seg.translation:
            yield f"[{stamp}] {seg.translation}"


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
