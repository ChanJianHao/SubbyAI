"""Bounded finalized-text conditioning and language state for one ASR session."""

from __future__ import annotations

import numpy as np


class RecognitionContext:
    def __init__(self, source_language: str = ""):
        self.explicit_language = source_language or None
        self.reset()

    def reset(self):
        self.text = ""
        self.language = self.explicit_language
        self.language_confidence = 0.0
        self._audio = np.empty(0, np.float32)
        self._audio_end = 0.0
        self._next_probe = 0.0
        self._candidate = None
        self._votes = 0
        self.epoch = None
        self.end = -100.0

    def prepare(self, engine, work):
        # Retained chunks omit long pauses. Concatenating old-language audio
        # across an uncovered gap biased language detection toward the previous
        # speaker in the code-switch fixture. Start fresh evidence after a gap;
        # an explicit user language remains authoritative.
        if self.epoch != work.epoch or work.start - self.end > 1.2:
            self.reset()
            self.epoch = work.epoch
        offset = max(0, round((self._audio_end - work.start) * 16000))
        self._audio = np.concatenate([self._audio, work.audio[offset:]])[-96000:]
        self._audio_end = max(self._audio_end, work.start + work.duration)
        if (
            not self.explicit_language
            and len(self._audio) >= 48000
            and self._audio_end >= self._next_probe
            and hasattr(engine, "detect_language")
        ):
            detected, confidence = engine.detect_language(self._audio)
            self._next_probe = self._audio_end + 20
            if confidence >= 0.8:
                if self.language is None or self.language == detected:
                    self.language, self.language_confidence = detected, confidence
                    self._candidate, self._votes = None, 0
                else:
                    self._votes = self._votes + 1 if self._candidate == detected else 1
                    self._candidate = detected
                    # Require repeated evidence, and recheck promptly on a switch.
                    if self._votes >= 2:
                        self.language, self.language_confidence = detected, confidence
                        self.text = ""
                        self._audio = work.audio[-96000:]
                        self._candidate, self._votes = None, 0
                    else:
                        self._next_probe = self._audio_end + 3
        self.end = max(self.end, work.start + work.duration)
        # A long window contains enough evidence to detect an actual switch.
        # Lock only short fragments; forcing a session language forever would
        # turn code switching into repeated recognition errors.
        return self.explicit_language or (self.language if work.duration < 2.5 else None)

    def remember(self, text, result):
        if (
            not self.explicit_language
            and result.language
            and result.duration >= 2.5
            and result.language_confidence >= 0.8
        ):
            if self.language and self.language != result.language:
                self.text = ""
            self.language = result.language
            self.language_confidence = result.language_confidence
        if result.speech_probability >= 0.6 and (
            not result.confidence_known or result.confidence >= 0.5
        ):
            # Whisper's small conditioning window is not a conversation store.
            # Keep the latest finalized utterance; replaying older boilerplate
            # caused it to reappear in noisy pauses in a controlled ablation.
            # Translation retains a separate, larger history of sentence pairs.
            self.text = text.strip()[-600:]
