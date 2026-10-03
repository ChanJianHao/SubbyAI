"""Transcript exports and the timestamp/label formatting the history surface shares.

The format builders are pure functions over ``CaptionSegment`` lists so every
byte they emit can be asserted in a test without a running Qt application — an
export that silently drops the translated line is the kind of bug people only
find weeks later, in a file they already sent to someone else.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ..core.events import CaptionSegment, SessionInfo

#: Subtitles need a visible dwell time even when the recognizer reported none.
_MIN_CUE_SECONDS = 1.2

EXPORT_KINDS: tuple[tuple[str, str, str], ...] = (
    ("text", "Text (.txt)", ".txt"),
    ("subtitles", "Subtitles (.srt)", ".srt"),
    ("webvtt", "Web subtitles (.vtt)", ".vtt"),
    ("markdown", "Markdown (.md)", ".md"),
)


# ---------- timestamps ----------


def clock_timestamp(seconds: float) -> str:
    """Offset from the start of a session, as HH:MM:SS."""
    total = int(max(0.0, seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def wall_timestamp(started_at: float, offset: float) -> str:
    """Time of day a line was said, which is how people remember hearing it."""
    return datetime.fromtimestamp(started_at + max(0.0, offset)).strftime("%H:%M:%S")


def srt_timestamp(seconds: float) -> str:
    """HH:MM:SS,mmm — the only shape subtitle players accept."""
    milliseconds = round(max(0.0, seconds) * 1000)
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def vtt_timestamp(seconds: float) -> str:
    """HH:MM:SS.mmm - WebVTT is SRT with dots, and players reject commas."""
    return srt_timestamp(seconds).replace(",", ".")


# ---------- labels ----------


def language_label(code: str | None) -> str:
    """Full language name for a stored code, never the code itself.

    The import is deferred because the translation package pulls in the HTTP
    stack, and opening History should not pay for a translator it never uses.
    """
    if not code:
        return ""
    from ..translation.base import language_name

    return language_name(code)


def language_pair(session: SessionInfo | None) -> str:
    """`Japanese → English`, or just the spoken language when nothing is translated."""
    if session is None:
        return ""
    source = language_label(session.source_language)
    target = language_label(session.target_language)
    if source and target and source != target:
        return f"{source} → {target}"
    return source or target


def duration_label(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def session_summary(session: SessionInfo) -> str:
    """`18 min · 2,300 words` — the line under a session title."""
    return f"{duration_label(session.duration_seconds)} · {session.word_count:,} words"


def session_meta_line(session: SessionInfo | None) -> str:
    """`Tuesday 4 August · 14:02–14:20 · Japanese → English`."""  # noqa: RUF002 - en dash range
    if session is None:
        return ""
    start = datetime.fromtimestamp(session.started_at)
    parts = [f"{start:%A} {start.day} {start:%B}"]
    if session.ended_at:
        end = datetime.fromtimestamp(session.ended_at)
        parts.append(f"{start:%H:%M}–{end:%H:%M}")  # noqa: RUF001 - en dash reads as a range
    else:
        parts.append(f"{start:%H:%M}")
    languages = language_pair(session)
    if languages:
        parts.append(languages)
    return " · ".join(parts)


def day_label(when: float, now: float | None = None) -> str:
    """`Today`, `Yesterday`, or `Monday, 3 August` for a session-list header."""
    day = datetime.fromtimestamp(when)
    today = datetime.fromtimestamp(now if now is not None else time.time())
    delta = (today.date() - day.date()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    label = f"{day:%A}, {day.day} {day:%B}"
    return label if day.year == today.year else f"{label} {day.year}"


def human_bytes(count: int) -> str:
    """Storage size in the roundest honest unit: `41 MB`, `1.4 GB`."""
    if count < 1000:
        return f"{count} bytes"
    if count < 1_000_000:
        return f"{count / 1000:.0f} KB"
    if count < 1_000_000_000:
        return f"{count / 1_000_000:.0f} MB"
    return f"{count / 1_000_000_000:.1f} GB"


# ---------- formats ----------


def export_text(segments: list[CaptionSegment]) -> str:
    """Plain text: `[00:00:01] original` with the translation hanging under it."""
    lines: list[str] = []
    for segment in segments:
        stamp = f"[{clock_timestamp(segment.audio_start)}] "
        lines.append(f"{stamp}{segment.text}")
        translation = segment.display_translation
        if translation:
            lines.append(f"{' ' * len(stamp)}{translation}")
    return "\n".join(lines) + ("\n" if lines else "")


def export_srt(segments: list[CaptionSegment]) -> str:
    blocks: list[str] = []
    for number, segment in enumerate(segments, start=1):
        start = max(0.0, segment.audio_start)
        duration = segment.audio_duration if segment.audio_duration > 0 else _MIN_CUE_SECONDS
        cue = [
            str(number),
            f"{srt_timestamp(start)} --> {srt_timestamp(start + duration)}",
            segment.text,
        ]
        translation = segment.display_translation
        if translation:
            cue.append(translation)
        blocks.append("\n".join(cue))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_vtt(segments: list[CaptionSegment]) -> str:
    """WebVTT - what video players and web platforms actually accept.

    Same cue shape as SRT with two differences that break players if wrong:
    millisecond separators are dots, not commas, and the file needs a header.
    Cue numbers are optional in WebVTT and kept for parity with SRT exports.
    """
    if not segments:
        return "WEBVTT\n"
    blocks = ["WEBVTT", ""]
    for number, segment in enumerate(segments, start=1):
        start = max(0.0, segment.audio_start)
        duration = segment.audio_duration if segment.audio_duration > 0 else _MIN_CUE_SECONDS
        cue = [
            str(number),
            f"{vtt_timestamp(start)} --> {vtt_timestamp(start + duration)}",
            segment.text,
        ]
        translation = segment.display_translation
        if translation:
            cue.append(translation)
        blocks.append("\n".join(cue))
    return "\n\n".join(blocks) + "\n"


def export_markdown(segments: list[CaptionSegment], session: SessionInfo | None = None) -> str:
    title = session.title if session else "Transcript"
    lines = [f"# {title}", ""]
    meta = session_meta_line(session)
    if meta:
        lines += [meta, ""]
    for segment in segments:
        stamp = clock_timestamp(segment.audio_start)
        translation = segment.display_translation
        if translation:
            # Two trailing spaces: a Markdown hard break keeps the pair together.
            lines.append(f"**{stamp}** {segment.text}  ")
            lines.append(translation)
        else:
            lines.append(f"**{stamp}** {segment.text}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_export(
    kind: str, segments: list[CaptionSegment], session: SessionInfo | None = None
) -> str:
    if kind == "subtitles":
        return export_srt(segments)
    if kind == "webvtt":
        return export_vtt(segments)
    if kind == "markdown":
        return export_markdown(segments, session)
    return export_text(segments)


def suggested_filename(session: SessionInfo | None, suffix: str) -> str:
    stem = (session.title if session else "Transcript").strip() or "Transcript"
    for bad in '\\/:*?"<>|':
        stem = stem.replace(bad, "-")
    return f"{stem[:80]}{suffix}"


def save_export(parent, store, session_id: int, kind: str) -> Path | None:
    """Ask where to put an export and write it. Returns the path, or None if cancelled.

    Qt lives inside the function so the format builders above stay importable
    (and testable) without a display.
    """
    from PySide6.QtWidgets import QFileDialog

    from .. import paths

    label, suffix = next(
        ((name, ext) for key, name, ext in EXPORT_KINDS if key == kind), ("Text (.txt)", ".txt")
    )
    session = store.get_session(session_id)
    default = paths.exports_dir() / suggested_filename(session, suffix)
    chosen, _ = QFileDialog.getSaveFileName(parent, "Export transcript", str(default), f"{label}")
    if not chosen:
        return None
    path = Path(chosen)
    path.write_text(render_export(kind, store.segments(session_id), session), encoding="utf-8")
    return path
