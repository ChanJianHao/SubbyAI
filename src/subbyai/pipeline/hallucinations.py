"""Suppress likely recognition filler without changing genuine speech.

Whole-caption matching combines the recognizer's no-speech score with known
filler phrases. Literal phrase patterns never remove substrings from sentences."""

from __future__ import annotations

import logging
import re
import unicodedata
from importlib import resources

from .. import paths

log = logging.getLogger(__name__)

_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
#: Below this speech probability a caption is treated as noise regardless of text.
_NO_SPEECH_LIMIT = 0.6
#: Fuzzy match ceiling — captions this close to a known filler are dropped.
_LENGTH_SLACK = 12


class HallucinationFilter:
    def __init__(self, phrases: list[str] | None = None):
        if phrases is None:
            phrases = load_default_phrases() + load_user_phrases()
        self._phrases = {_normalize(p) for p in phrases if p.strip()}

    def should_drop(
        self, text: str, speech_probability: float = 1.0, confidence: float = 1.0
    ) -> bool:
        """True when this caption is noise rather than speech."""
        stripped = text.strip()
        if not stripped or _PUNCT_ONLY.match(stripped):
            return True

        # Combine both recognition signals before rejecting probable noise.
        if speech_probability < _NO_SPEECH_LIMIT and confidence < 0.5:
            return True

        normalized = _normalize(stripped)
        if not normalized:
            return True
        if normalized in self._phrases:
            return True

        # A known filler plus a little punctuation/politeness is still filler,
        # but only when the whole caption is short — never mid-sentence.
        if len(normalized) <= _LENGTH_SLACK * 3:
            for phrase in self._phrases:
                if not phrase:
                    continue
                if normalized.startswith(phrase) and len(normalized) - len(phrase) <= _LENGTH_SLACK:
                    return True
        return False

    def clean(self, text: str) -> str:
        """Tidy whitespace. Text is never surgically edited beyond this."""
        return re.sub(r"\s+", " ", text).strip()


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"[\s\W_]+", " ", text).strip()


def load_default_phrases() -> list[str]:
    try:
        data = (
            resources.files("subbyai.resources")
            .joinpath("hallucinations.txt")
            .read_text(encoding="utf-8")
        )
    except (OSError, ModuleNotFoundError):
        log.warning("Bundled phrase list missing")
        return []
    return _parse(data)


def load_user_phrases() -> list[str]:
    path = paths.config_dir() / "ignored-phrases.txt"
    if not path.exists():
        return []
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return []


def ensure_user_file():
    path = paths.config_dir() / "ignored-phrases.txt"
    if not path.exists():
        path.write_text(
            "# One phrase per line. A caption that is only one of these is hidden.\n"
            "# Phrases are never removed from the middle of real speech.\n",
            encoding="utf-8",
        )
    return path


def _parse(data: str) -> list[str]:
    return [
        line.strip()
        for line in data.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
