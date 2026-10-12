"""Bounded semantic accumulation for finalized captions, independent of providers."""

from __future__ import annotations

import re

_END = re.compile(r"[.!?。！？][\"'”’」』)]*$")  # noqa: RUF001 - native punctuation


def sentence_finished(text: str, language: str | None = None) -> bool:
    if not _END.search(text.strip()):
        return False
    if language == "en":
        # ASR inserts punctuation at acoustic pauses, including incomplete
        # clauses ("ask not.", "because."). Keep these open for a continuation;
        # do not treat punctuation alone as semantic completion.
        words = re.findall(r"[a-z]+", text.casefold())
        if words and words[-1] in {
            "a",
            "an",
            "the",
            "to",
            "of",
            "for",
            "with",
            "and",
            "or",
            "if",
            "when",
            "because",
            "not",
        }:
            return False
    return True


def join_sources(segments, vocabulary: str = "") -> str:
    text = ""
    protected = {term.strip().casefold() for term in vocabulary.replace("\n", ",").split(",")}
    grammar = {
        "ask",
        "do",
        "does",
        "did",
        "not",
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "with",
        "and",
        "or",
        "if",
        "when",
        "because",
    }
    previous = None
    for segment in segments:
        current = segment.text.strip()
        if segment.language == "en" and not sentence_finished(current, "en"):
            # Short Whisper fragments sometimes become emphatic ALL CAPS.
            # OPUS tokenizes those differently. Normalize only known grammar
            # words in an unfinished clause, leaving names/acronyms and explicit
            # vocabulary untouched. Original captions always retain raw casing.
            current = re.sub(
                r"\b[A-Z]{2,}\b",
                lambda m: (
                    m[0].lower()
                    if (m[0].casefold() in grammar and m[0].casefold() not in protected)
                    else m[0]
                ),
                current,
            )
            if current:
                current = current[0].upper() + current[1:]
        if previous and previous.language == "en" and not sentence_finished(previous.text, "en"):
            # Punctuation at an incomplete acoustic boundary is not a full stop
            # in the sentence actually handed to the translator.
            text = re.sub(r"[.!?]+$", "", text)
            first = current.split()[0] if current.split() else ""
            if (
                first in {"What", "Which", "That", "If", "Because"}
                and first.casefold() not in protected
            ):
                current = current[0].lower() + current[1:]
        # CJK should retain its native punctuation and word boundaries.
        cjk = bool(
            segment.language != "ko"
            and text
            and current
            and _is_cjk(text[-1])
            and _is_cjk(current[0])
        )
        text += ("" if not text or cjk else " ") + current
        previous = segment
    return text


def _is_cjk(character):
    return "\u3000" <= character <= "\u9fff"


def can_join(previous, current) -> bool:
    return (
        previous.language == current.language
        and previous.context_epoch == current.context_epoch
        and current.audio_start - (previous.audio_start + previous.audio_duration) <= 1.2
        and current.audio_start >= previous.audio_start
    )
