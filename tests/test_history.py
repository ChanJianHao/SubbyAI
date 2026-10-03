"""History list, search, transcript viewer, exports, and the Ask AI consent gate."""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from PySide6.QtCore import QThreadPool

from subbyai.core.events import CaptionSegment, PrivacyTier
from subbyai.storage.session_store import SearchHit
from subbyai.ui import history_view as hv
from subbyai.ui import transcript_view as tv
from subbyai.ui.exports import export_markdown, export_srt, export_text, export_vtt
from subbyai.ui.history_widgets import EmptyState
from subbyai.ui.transcript_ask import ask_button_label

DAY = 86400


def _segment(text: str, translation: str | None = None, start: float = 0.0) -> CaptionSegment:
    segment = CaptionSegment(
        text=text, language="ja", audio_start=start, audio_duration=2.5
    )
    if translation:
        segment = segment.with_translation(translation, "en", "builtin", PrivacyTier.ON_DEVICE)
    return segment


def _make_session(store, title: str, lines, started_at: float | None = None) -> int:
    session_id = store.create_session(title, "ja", "en")
    for index, (text, translation) in enumerate(lines):
        store.add_segment(session_id, _segment(text, translation, start=index * 3.0))
    store.flush()
    if started_at is not None:
        with store._connect() as conn:
            conn.execute(
                "UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = ?",
                (started_at, started_at + 1080, session_id),
            )
    return session_id


@pytest.fixture
def populated(store):
    """Three sessions across three days, all dual-language.

    Anchored to local midnight rather than to "an hour ago": day labels are
    calendar comparisons, so a fixture offset in hours lands on the wrong day
    whenever the suite runs shortly after midnight — which failed this test for
    one hour out of every twenty-four.
    """
    now = time.time()
    midnight = datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    ids = {
        "today": _make_session(
            store,
            "YouTube — 14:02",
            [("こんにちは世界", "Hello world"), ("ドラゴンが山にいる", "A dragon in the mountain")],
            started_at=now,
        ),
        "yesterday": _make_session(
            store,
            "Lecture — 09:30",
            [("おはよう", "Good morning")],
            started_at=midnight - 12 * 3600,
        ),
        "older": _make_session(
            store, "Call — 16:45", [("さようなら", "Goodbye")], started_at=midnight - 5 * DAY
        ),
    }
    return ids


# ---------- grouping ----------


def test_sessions_are_grouped_by_day(qt_app, store, populated):
    view = hv.HistoryView(store)
    groups = hv.group_sessions(view.sessions())
    labels = [label for label, _ in groups]

    assert labels[0] == "Today"
    assert labels[1] == "Yesterday"
    assert len(labels) == 3
    assert labels[2] not in ("Today", "Yesterday")
    assert [s.title for s in groups[0][1]] == ["YouTube — 14:02"]


def test_day_label_wording(qt_app):
    now = time.time()
    assert hv.day_label(now, now) == "Today"
    assert hv.day_label(now - DAY, now) == "Yesterday"
    older = hv.day_label(now - 4 * DAY, now)
    assert "," in older and older not in ("Today", "Yesterday")


def test_footer_states_where_transcripts_live(qt_app, store, populated):
    view = hv.HistoryView(store)
    text = view._storage_label.text()
    assert text.startswith("Transcripts stay on this device · ")
    assert text.endswith(" used")


# ---------- search ----------


def test_search_is_debounced_and_returns_hits(qt_app, store, populated):
    view = hv.HistoryView(store)
    calls: list[str] = []
    real_search = store.search

    def counting_search(query, *args, **kwargs):
        calls.append(query)
        return real_search(query, *args, **kwargs)

    store.search = counting_search

    for text in ("d", "dr", "dra", "drag", "dragon"):
        view.search_field.setText(text)
    assert calls == []  # nothing fires while the user is still typing

    _wait_for(lambda: bool(calls), timeout=2.0)
    assert calls == ["dragon"]
    assert view._hits and view._hits[0].text == "ドラゴンが山にいる"


def test_clicking_a_search_hit_emits_segment_opened(qt_app, store, populated):
    view = hv.HistoryView(store)
    view.search_field.setText("dragon")
    view._run_search()

    rows = view.findChildren(hv.HitRow)
    assert rows, "a matched line should be shown"
    received: list[tuple[int, int]] = []
    view.segment_opened.connect(lambda s, g: received.append((s, g)))
    rows[0].clicked.emit(rows[0].hit.session_id, rows[0].hit.segment_id)

    assert received == [(populated["today"], rows[0].hit.segment_id)]


def test_hits_are_grouped_and_capped_per_session():
    hits = [
        SearchHit(1, "A", segment_id=n, audio_start=float(n), text="x", translation=None)
        for n in range(5)
    ]
    hits.append(SearchHit(2, "B", segment_id=99, audio_start=1.0, text="x", translation=None))
    grouped = hv.group_hits(hits)

    assert [session_id for session_id, _, _ in grouped] == [1, 2]
    assert len(grouped[0][2]) == hv.HITS_PER_SESSION
    assert len(grouped[1][2]) == 1


def test_search_highlight_marks_the_match(qt_app, store, populated):
    from subbyai.ui.history_widgets import highlight_html

    marked = highlight_html("A dragon in the mountain", "dragon", "#FFEEAA")
    assert "<span" in marked and ">dragon<" in marked
    assert "A " in marked


# ---------- empty states ----------


def test_empty_state_when_nothing_captured(qt_app, store):
    view = hv.HistoryView(store)
    empty = view.findChild(EmptyState)

    assert empty is not None
    assert empty.title == "Your sessions will appear here"
    assert empty.body.startswith("Every captioning session is saved as a searchable transcript")


def test_empty_state_when_history_is_off_offers_the_fix(qt_app, store, populated):
    view = hv.HistoryView(store)
    view.set_history_enabled(False)
    empty = view.findChild(EmptyState)

    assert empty is not None
    assert empty.title == "Session history is off"
    assert empty.action_button is not None
    assert empty.action_button.text() == "Turn on history"

    asked: list[bool] = []
    view.enable_history_requested.connect(lambda: asked.append(True))
    empty.action_button.click()
    assert asked == [True]


def test_empty_state_when_search_finds_nothing(qt_app, store, populated):
    view = hv.HistoryView(store)
    view.search_field.setText("zzzznothing")
    view._run_search()
    empty = view.findChild(EmptyState)

    assert empty is not None
    assert empty.title == 'Nothing found for "zzzznothing"'
    assert empty.body == "Try fewer words, or search in the original language."


# ---------- destructive actions ----------


def test_delete_asks_first_and_removes_the_session(qt_app, store, populated, monkeypatch):
    view = hv.HistoryView(store)
    asked: list[str] = []

    monkeypatch.setattr(hv, "confirm_delete", lambda parent, text: asked.append(text) or False)
    view._delete_session(populated["older"])
    assert asked, "deleting must ask first"
    assert store.get_session(populated["older"]) is not None

    monkeypatch.setattr(hv, "confirm_delete", lambda parent, text: True)
    view._delete_session(populated["older"])
    assert store.get_session(populated["older"]) is None
    assert populated["older"] not in [s.id for s in view.sessions()]


# ---------- transcript viewer ----------


def test_transcript_model_rows_and_data(qt_app, store, populated):
    view = tv.TranscriptView(store)
    view.load_session(populated["today"])

    assert view.model.rowCount() == 2
    first = view.model.index(0, 0)
    segment = first.data(tv.SEGMENT_ROLE)
    assert segment.text == "こんにちは世界"
    assert segment.display_translation == "Hello world"
    assert first.data() == "こんにちは世界\nHello world"
    assert view.model.row_for_segment(segment.id) == 0
    assert view.model.row_for_segment(-1) == -1


def test_transcript_focus_segment_selects_and_flashes(qt_app, store, populated):
    view = tv.TranscriptView(store)
    segments = store.segments(populated["today"])
    view.load_session(populated["today"], focus_segment_id=segments[1].id)

    assert view.list.currentIndex().row() == 1
    assert view.model.is_flashing(segments[1]) is True


def test_rename_writes_through_to_the_store(qt_app, store, populated):
    view = tv.TranscriptView(store)
    view.load_session(populated["today"])
    renamed: list[tuple[int, str]] = []
    view.session_renamed.connect(lambda i, t: renamed.append((i, t)))

    view.begin_rename()
    view.title_edit.setText("Anime night")
    view.commit_rename()

    assert store.get_session(populated["today"]).title == "Anime night"
    assert renamed == [(populated["today"], "Anime night")]


def test_transcript_delete_confirms_then_deletes(qt_app, store, populated, monkeypatch):
    view = tv.TranscriptView(store)
    view.load_session(populated["yesterday"])
    monkeypatch.setattr(tv, "confirm_delete", lambda parent, text: False)
    view.delete_session()
    assert store.get_session(populated["yesterday"]) is not None

    monkeypatch.setattr(tv, "confirm_delete", lambda parent, text: True)
    gone: list[int] = []
    view.session_deleted.connect(gone.append)
    view.delete_session()

    assert gone == [populated["yesterday"]]
    assert store.get_session(populated["yesterday"]) is None


def test_find_bar_counts_and_cycles_matches(qt_app, store, populated):
    view = tv.TranscriptView(store)
    view.load_session(populated["today"])
    view.open_find()
    view.find_field.setText("o")  # in "Hello world" and "A dragon in the mountain"

    assert view.match_count() == 2
    assert view.match_label.text() == "1 of 2"
    view.find_next()
    assert view.match_label.text() == "2 of 2"
    view.find_next()
    assert view.match_label.text() == "1 of 2"  # wraps
    view.find_previous()
    assert view.match_label.text() == "2 of 2"

    view.find_field.setText("zzzz")
    assert view.match_label.text() == "No matches"


# ---------- exports ----------


def test_export_text_keeps_both_languages(qt_app, store, populated):
    segments = store.segments(populated["today"])
    text = export_text(segments)

    assert "[00:00:00] こんにちは世界" in text
    assert "Hello world" in text
    assert "[00:00:03] ドラゴンが山にいる" in text
    assert text.endswith("\n")


def test_export_srt_timestamps_and_both_languages(qt_app, store, populated):
    segments = store.segments(populated["today"])
    srt = export_srt(segments)
    blocks = srt.strip().split("\n\n")

    assert blocks[0].splitlines() == [
        "1",
        "00:00:00,000 --> 00:00:02,500",
        "こんにちは世界",
        "Hello world",
    ]
    assert blocks[1].splitlines()[0] == "2"
    assert blocks[1].splitlines()[1] == "00:00:03,000 --> 00:00:05,500"
    assert "ドラゴンが山にいる" in blocks[1] and "A dragon in the mountain" in blocks[1]


def test_export_srt_gives_a_zero_length_cue_a_dwell_time(qt_app):
    srt = export_srt([CaptionSegment(text="quick", audio_start=1.0, audio_duration=0.0)])
    assert "00:00:01,000 --> 00:00:02,200" in srt


def test_export_vtt_is_headered_dot_timestamps_and_both_languages(qt_app, store, populated):
    vtt = export_vtt(store.segments(populated["today"]))

    assert vtt.startswith("WEBVTT\n")
    assert "," not in vtt.split("\n", 1)[1]  # commas break every WebVTT parser
    assert "00:00:00.000 --> 00:00:02.500" in vtt
    assert "こんにちは世界" in vtt and "Hello world" in vtt


def test_export_vtt_of_an_empty_session_is_just_the_header(qt_app):
    assert export_vtt([]) == "WEBVTT\n"


def test_export_markdown_has_title_metadata_and_both_languages(qt_app, store, populated):
    session = store.get_session(populated["today"])
    markdown = export_markdown(store.segments(populated["today"]), session)

    assert markdown.startswith("# YouTube — 14:02\n")
    assert "Japanese → English" in markdown  # names, never language codes
    assert "**00:00:00** こんにちは世界" in markdown
    assert "Hello world" in markdown


def test_transcript_text_round_trips_through_the_view(qt_app, store, populated):
    view = tv.TranscriptView(store)
    view.load_session(populated["today"])
    assert view.transcript_text() == export_text(store.segments(populated["today"]))


# ---------- ask ai ----------


class _FakeAssistant:
    def __init__(self, tier: PrivacyTier):
        self.label = "Test provider"
        self.tier = tier
        self.calls: list[tuple[str, str]] = []

    def ask(self, question: str, transcript: str) -> str:
        self.calls.append((question, transcript))
        return "A summary."


def test_ask_ai_is_hidden_without_a_provider(qt_app, store, populated):
    view = tv.TranscriptView(store)
    view.load_session(populated["today"])

    assert view.ask_button.isVisible() is False
    assert view.drawer.isVisible() is False


def test_ask_ai_button_carries_the_privacy_tier(qt_app, store, populated):
    assistant = _FakeAssistant(PrivacyTier.ON_DEVICE)
    view = tv.TranscriptView(store, assistant=assistant)
    view.load_session(populated["today"])

    assert view.ask_button.text() == "Ask AI · On this device"
    assert ask_button_label(_FakeAssistant(PrivacyTier.CLOUD)) == "Ask AI · Cloud"


def test_cloud_assistant_waits_for_consent_before_being_called(qt_app, store, populated):
    assistant = _FakeAssistant(PrivacyTier.CLOUD)
    view = tv.TranscriptView(store, assistant=assistant)
    view.load_session(populated["today"])
    granted: list[bool] = []
    view.cloud_consent_granted.connect(lambda: granted.append(True))

    view.drawer.summarize()
    _drain()
    assert assistant.calls == []  # nothing left the machine
    assert view.drawer.consent_panel.isVisibleTo(view.drawer)
    assert "Test provider" in view.drawer.consent_panel.message.text()
    assert "こんにちは世界" in view.drawer.consent_panel.preview.text()

    view.drawer.consent_panel.send_button.click()
    _drain()

    assert granted == [True]
    assert len(assistant.calls) == 1
    assert assistant.calls[0][0] == "Summarize this session"
    assert "Hello world" in assistant.calls[0][1]
    assert "Test provider · Cloud" in view.drawer.answers.toPlainText()


def test_cancelling_consent_sends_nothing(qt_app, store, populated):
    assistant = _FakeAssistant(PrivacyTier.CLOUD)
    view = tv.TranscriptView(store, assistant=assistant)
    view.load_session(populated["today"])

    view.drawer.summarize()
    view.drawer.consent_panel.rejected.emit()
    _drain()

    assert assistant.calls == []
    assert view.drawer.has_consent() is False


def test_on_device_assistant_needs_no_consent(qt_app, store, populated):
    assistant = _FakeAssistant(PrivacyTier.ON_DEVICE)
    view = tv.TranscriptView(store, assistant=assistant)
    view.load_session(populated["today"])

    view.drawer.summarize()
    _drain()

    assert len(assistant.calls) == 1
    assert "Test provider · On this device" in view.drawer.answers.toPlainText()


def _drain(timeout_ms: int = 3000) -> None:
    """Let the worker pool finish and its queued signals land on the UI thread."""
    from PySide6.QtWidgets import QApplication

    QThreadPool.globalInstance().waitForDone(timeout_ms)
    QApplication.processEvents()


def _wait_for(predicate, timeout: float) -> None:
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        QApplication.processEvents()
        time.sleep(0.01)
