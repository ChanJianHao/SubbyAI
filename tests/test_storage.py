import time

import pytest

from subbyai.core.events import CaptionSegment, PrivacyTier, TranslationState
from subbyai.storage.session_store import SearchHit


def _seg(text: str, translation: str | None = None, start: float = 0.0) -> CaptionSegment:
    seg = CaptionSegment(text=text, language="ja", audio_start=start, audio_duration=2.0)
    if translation:
        seg = seg.with_translation(translation, "en", "builtin", PrivacyTier.ON_DEVICE)
    return seg


def test_session_lifecycle_and_segments(store):
    store.start_writer()
    session_id = store.create_session("Test session", "ja", "en")
    store.add_segment(session_id, _seg("こんにちは", "Hello", 1.0))
    store.add_segment(session_id, _seg("さようなら", "Goodbye", 5.0))
    store.flush()

    segments = store.segments(session_id)
    assert [s.text for s in segments] == ["こんにちは", "さようなら"]
    assert segments[0].translation == "Hello"
    assert segments[0].translation_state is TranslationState.DONE

    info = store.get_session(session_id)
    assert info is not None and info.segment_count == 2


def test_empty_sessions_are_discarded(store):
    store.start_writer()
    session_id = store.create_session("Nothing happened")
    store.end_session(session_id)
    assert store.get_session(session_id) is None


def test_search_matches_original_and_translation(store):
    store.start_writer()
    session_id = store.create_session("Anime")
    store.add_segment(session_id, _seg("dragon in the mountain", "translated dragon line"))
    store.add_segment(session_id, _seg("something else entirely", "nothing relevant"))
    store.flush()

    hits = store.search("dragon")
    assert len(hits) == 1
    assert isinstance(hits[0], SearchHit)
    assert hits[0].session_title == "Anime"

    assert len(store.search("translated")) == 1  # translation column is indexed
    assert store.search("zzzz") == []


def test_search_prefix_matching(store):
    store.start_writer()
    session_id = store.create_session("S")
    store.add_segment(session_id, _seg("magnificent castle"))
    store.flush()
    assert len(store.search("magnif")) == 1


def test_search_tolerates_punctuation_query(store):
    """FTS5 chokes on bare operators; the store must not raise."""
    store.start_writer()
    session_id = store.create_session("S")
    store.add_segment(session_id, _seg("hello world"))
    store.flush()
    assert store.search('"') == []
    assert len(store.search("hello")) == 1


def test_delete_session_removes_segments_and_index(store):
    store.start_writer()
    session_id = store.create_session("Doomed")
    store.add_segment(session_id, _seg("ephemeral text"))
    store.flush()
    assert len(store.search("ephemeral")) == 1

    store.delete_session(session_id)
    assert store.get_session(session_id) is None
    assert store.search("ephemeral") == []


def test_retention_deletes_old_sessions(store):
    store.start_writer()
    old = store.create_session("Old")
    store.add_segment(old, _seg("old text"))
    store.flush()
    # Backdate the session by 100 days.
    with store._connect() as conn:  # white-box test of retention
        conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (time.time() - 100 * 86400, old),
        )
    recent = store.create_session("Recent")
    store.add_segment(recent, _seg("recent text"))
    store.flush()

    removed = store.apply_retention(90)
    assert removed == 1
    assert store.get_session(old) is None
    assert store.get_session(recent) is not None


def test_retention_forever_keeps_everything(store):
    store.start_writer()
    session_id = store.create_session("Keep")
    store.add_segment(session_id, _seg("text"))
    store.flush()
    assert store.apply_retention(0) == 0
    assert store.get_session(session_id) is not None


def test_writes_are_batched_not_per_caption(store):
    """The writer batches captions without reopening the database per event."""
    store.start_writer()
    session_id = store.create_session("Batch")
    for i in range(20):
        store.add_segment(session_id, _seg(f"line {i}", start=float(i)))
    store.flush()
    assert len(store.segments(session_id)) == 20


def test_add_segment_does_not_block_without_writer(store):
    session_id = store.create_session("No writer")
    store.add_segment(session_id, _seg("queued"))
    store.flush()  # drains synchronously when the writer isn't running
    assert len(store.segments(session_id)) == 1


@pytest.mark.parametrize("background", [False, True])
def test_event_retraction_keeps_queue_order_and_other_session_data(store, background):
    if background:
        store.start_writer()
    session = store.create_session("Correction")
    other = store.create_session("Unrelated")
    original = _seg("superseded words", "superseded translation")
    store.add_segment(session, original)
    store.add_segment(other, original)
    store.remove_segment(session, original.id)
    store.flush()
    assert not store.segments(session)
    assert len(store.segments(other)) == 1
    assert [hit.session_id for hit in store.search("superseded")] == [other]
    store.add_segment(session, original)
    store.flush()
    assert len(store.segments(session)) == 1


def test_corrected_source_and_timing_replace_event_and_search_index(store):
    from dataclasses import replace

    session = store.create_session("Correction")
    original = _seg("mistaken name")
    store.add_segment(session, original)
    store.update_segment(session, replace(original, text="correct name", audio_duration=3))
    store.flush()
    assert len(store.segments(session)) == 1
    assert store.segments(session)[0].text == "correct name"
    assert store.segments(session)[0].audio_duration == 3
    assert not store.search("mistaken")
    assert len(store.search("correct")) == 1


@pytest.mark.parametrize("background", [False, True])
def test_failed_write_is_reported_and_next_batch_can_recover(store, monkeypatch, background):
    import contextlib
    import sqlite3

    from subbyai.storage.session_store import HistoryWriteError

    session = store.create_session("Write failure")
    original_connect = store._connect
    notices = []
    store._on_write_failure = lambda: notices.append(True)

    @contextlib.contextmanager
    def unavailable():
        raise sqlite3.OperationalError("Disk is full")
        yield  # pragma: no cover

    monkeypatch.setattr(store, "_connect", unavailable)
    if background:
        store.start_writer()
    store.add_segment(session, _seg("private content that must not be logged"))
    with pytest.raises(HistoryWriteError):
        store.flush()
    assert store.dropped_writes == 1
    assert notices == [True]
    monkeypatch.setattr(store, "_connect", original_connect)
    store.add_segment(session, _seg("Recovered caption"))
    store.flush()
    assert [s.text for s in store.segments(session)] == ["Recovered caption"]


def test_queue_overflow_is_visible_and_bounded(store):
    from subbyai.storage.session_store import HistoryWriteError

    session = store.create_session("Bounded")
    notices = []
    store._on_write_failure = lambda: notices.append(True)
    for _ in range(513):
        store.add_segment(session, _seg("caption"))
    assert store.pending_count == 512
    assert store.dropped_writes == 1
    assert notices == [True]
    with pytest.raises(HistoryWriteError):
        store.flush()
    assert len(store.segments(session)) == 512


def test_translation_only_sessions_have_meaningful_word_counts(store):
    session = store.create_session("Translated only")
    store.add_segment(session, _seg("", "three translated words"))
    store.flush()
    assert store.get_session(session).word_count == 3
    assert store.list_sessions()[0].word_count == 3


def test_empty_metadata_session_has_zero_word_count(store):
    session = store.create_session("Metadata only")
    assert store.get_session(session).word_count == 0


def test_retention_never_deletes_the_current_session(store):
    active = store.create_session("Still running")
    with store._connect() as conn:
        conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (0, active))
    assert store.apply_retention(1, active_session=active) == 0
    assert store.get_session(active) is not None
    assert store.apply_retention(1) == 1


@pytest.mark.parametrize("background", [False, True])
def test_async_lifecycle_orders_creation_corrections_retraction_and_end(store, background):
    from dataclasses import replace

    if background:
        store.start_writer()
    first = store.create_session_async("First", "ja", "en")
    second = store.create_session_async("Second", "ja", "en")
    original = _seg("mistaken name", "old translation")
    store.add_segment(first, original)
    store.update_segment(first, replace(original, text="correct name"))
    store.add_segment(second, original)
    store.remove_segment(second, original.id)
    completed = []
    store.end_session_async(first, finished=lambda: completed.append("first"))
    store.end_session_async(second, finished=lambda: completed.append("second"))
    store.flush()
    assert first.row_id is not None and not first.failed
    assert [seg.text for seg in store.segments(first)] == ["correct name"]
    assert store.get_session(first).ended_at is not None
    assert store.get_session(second) is None
    assert completed == ["first", "second"]
    assert not store.search("mistaken")


@pytest.mark.parametrize("background", [False, True])
def test_failed_async_creation_never_assigns_captions_to_another_session(
    store, monkeypatch, background,
):
    import contextlib
    import sqlite3

    from subbyai.storage.session_store import HistoryWriteError

    existing = store.create_session("Unrelated")
    connect = store._connect

    @contextlib.contextmanager
    def unavailable():
        raise sqlite3.OperationalError("Storage unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(store, "_connect", unavailable)
    if background:
        store.start_writer()
    failed = store.create_session_async("Failure")
    store.add_segment(failed, _seg("never in unrelated history"))
    store.end_session_async(failed)
    with pytest.raises(HistoryWriteError):
        store.flush()
    assert failed.failed and failed.row_id is None
    monkeypatch.setattr(store, "_connect", connect)
    recovered = store.create_session_async("Recovered")
    store.add_segment(recovered, _seg("saved after recovery"))
    store.end_session_async(recovered)
    store.flush()
    assert not store.segments(existing)
    assert [s.text for s in store.segments(recovered)] == ["saved after recovery"]
    assert not store.search("unrelated history")


def test_deferred_existing_database_initializes_only_on_worker(tmp_path, monkeypatch):
    import threading

    from subbyai.storage import SessionStore

    path = tmp_path / "existing.db"
    with_existing = SessionStore(path)
    old = with_existing.create_session("Existing")
    with_existing.close()
    init_threads = []
    initialize = SessionStore._init_schema

    def observed_initialize(self):
        init_threads.append(threading.current_thread())
        initialize(self)

    monkeypatch.setattr(SessionStore, "_init_schema", observed_initialize)
    ready = []
    deferred = SessionStore(
        path, lazy=True, defer_initialize=True, on_ready=lambda: ready.append(True),
    )
    try:
        assert not init_threads and deferred.list_sessions() == []
        deferred.start_writer()
        deferred.flush()
        assert len(init_threads) == 1 and init_threads[0] is not threading.current_thread()
        assert ready == [True] and deferred.get_session(old).title == "Existing"
    finally:
        deferred.close()


def test_deferred_opt_out_does_not_create_database(tmp_path):
    from subbyai.storage import SessionStore

    path = tmp_path / "private.db"
    deferred = SessionStore(path, lazy=True, defer_initialize=True)
    try:
        deferred.start_writer()
        deferred.flush()
        assert not path.exists()
    finally:
        deferred.close()


def test_session_reference_is_never_resolved_to_a_rolled_back_row(store):
    from subbyai.storage.session_store import HistoryWriteError

    with store._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_caption BEFORE INSERT ON segments "
            "BEGIN SELECT RAISE(ABORT, 'Unavailable'); END",
        )
    failed = store.create_session_async("Rolled back")
    store.add_segment(failed, _seg("discarded caption"))
    with pytest.raises(HistoryWriteError):
        store.flush()
    assert failed.failed and failed.row_id is None
    assert store.list_sessions() == []
    with store._connect() as conn:
        conn.execute("DROP TRIGGER reject_caption")
    good = store.create_session_async("Committed")
    store.add_segment(good, _seg("committed caption"))
    store.flush()
    assert good.row_id is not None
    assert store.segments(failed) == []
    assert [s.text for s in store.segments(good)] == ["committed caption"]
