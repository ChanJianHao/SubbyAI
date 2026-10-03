"""The caption event model.

Everything downstream — overlay, live view, history, transcripts, AI — consumes
these. Two rules shaped the design:

1. A caption's *original* text and its *translation* are separate fields that
   arrive at separate times. The original is shown the instant ASR finishes;
   the translation is attached later via ``with_translation``. Captions must
   never wait for a translator.
2. Nothing the engines know is thrown away. Language, confidence, and timing
   are what let the UI mark uncertain text, detect the wrong language, and
   scrub back through history.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum

_ids = itertools.count(1)


class TranslationState(StrEnum):
    NONE = "none"  # translation not requested
    PENDING = "pending"  # requested, in flight
    DONE = "done"
    FAILED = "failed"  # provider chain exhausted; original still valid


class PrivacyTier(StrEnum):
    """Where a provider ran. Computed from the endpoint, never self-declared."""

    ON_DEVICE = "on_device"
    LOCAL_NETWORK = "local_network"
    CLOUD = "cloud"

    @property
    def label(self) -> str:
        return {
            PrivacyTier.ON_DEVICE: "On this device",
            PrivacyTier.LOCAL_NETWORK: "Local network",
            PrivacyTier.CLOUD: "Cloud",
        }[self]


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    """One finalized utterance."""

    text: str
    """Original transcription, in the language that was spoken."""

    language: str | None = None
    """ISO 639-1 code detected (or forced) for the original."""

    language_confidence: float = 0.0

    translation: str | None = None
    target_language: str | None = None
    translation_state: TranslationState = TranslationState.NONE
    translation_provider: str | None = None
    translation_tier: PrivacyTier | None = None

    confidence: float = 1.0
    """0-1, derived from the recognizer's average log-probability."""

    speech_probability: float = 1.0
    confidence_known: bool = True
    """False when the engine that produced this reports no confidence."""
    """1 - no_speech_prob. Low values are the principled hallucination signal."""

    audio_start: float = 0.0
    """Seconds from the start of the session."""

    audio_duration: float = 0.0
    created_at: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def is_uncertain(self) -> bool:
        """Whether to mark this caption as a guess.

        An engine that reports no confidence gets the benefit of the doubt: an
        absent signal is not a negative one, and marking everything uncertain
        would make the marking meaningless.
        """
        if not self.confidence_known:
            return False
        return self.confidence < 0.55 or self.speech_probability < 0.5

    @property
    def display_translation(self) -> str | None:
        """Translation to show, if any is worth showing."""
        if self.translation_state is TranslationState.DONE:
            return self.translation
        return None

    def with_translation(
        self,
        text: str,
        target_language: str,
        provider: str,
        tier: PrivacyTier,
    ) -> CaptionSegment:
        return replace(
            self,
            translation=text,
            target_language=target_language,
            translation_state=TranslationState.DONE,
            translation_provider=provider,
            translation_tier=tier,
        )

    def with_translation_state(self, state: TranslationState) -> CaptionSegment:
        return replace(self, translation_state=state)


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """A captioning session: one continuous run of the pipeline."""

    id: int
    title: str
    started_at: float
    ended_at: float | None = None
    source_language: str | None = None
    target_language: str | None = None
    segment_count: int = 0
    word_count: int = 0

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at or time.time()) - self.started_at
