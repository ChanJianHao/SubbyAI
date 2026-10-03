"""The store must not leak SQLite connections.

sqlite3's context manager ends the transaction but leaves the connection open.
Relying on it held Windows file locks and made the database undeletable while
the app ran — this guards the fix.
"""

from __future__ import annotations

import gc
import sqlite3

from subbyai.core.events import CaptionSegment


def _open_connections() -> int:
    return sum(1 for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection))


def test_reads_and_writes_do_not_leak_connections(store):
    store.start_writer()
    session = store.create_session("Leak check")
    for index in range(10):
        store.add_segment(session, CaptionSegment(text=f"line {index}"))
    store.flush()

    gc.collect()
    before = _open_connections()
    for _ in range(20):
        store.list_sessions()
        store.segments(session)
        store.search("line")
    gc.collect()

    assert _open_connections() <= before


def test_database_file_is_released_after_close(tmp_path):
    """A closed store must let the file be deleted — Windows enforces this."""
    from subbyai.storage import SessionStore

    path = tmp_path / "release.db"
    store = SessionStore(path, flush_interval=0.05)
    store.start_writer()
    session = store.create_session("Session")
    store.add_segment(session, CaptionSegment(text="hello"))
    store.flush()
    store.close()
    gc.collect()

    path.unlink()  # raises PermissionError on Windows if a handle is still open
    assert not path.exists()
