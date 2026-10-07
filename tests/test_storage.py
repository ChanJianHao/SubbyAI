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
