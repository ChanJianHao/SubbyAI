"""Segment continuous audio into bounded speech phrases.

Convert native audio continuously, retain onset padding, and close at padded
speech boundaries or a bounded duration. Production injects stateful Silero;
energy detection is an explicit fallback. Forced splits receive acoustic overlap
and a second decode after lookahead, rather than declaring clipped words final.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .processing import StreamingResampler
from .processing import to_mono_16k as to_mono_16k

TARGET_RATE = 16000
_FRAME_SECONDS = 0.02  # 20 ms analysis frames
_FRAME_SAMPLES = int(TARGET_RATE * _FRAME_SECONDS)

# "Ignore short noises" in the UI. Higher threshold = fewer things count as speech.
SENSITIVITY_THRESHOLDS: dict[str, float] = {
    "low": 0.015,
    "standard": 0.008,
    "high": 0.004,
}
DEFAULT_SENSITIVITY = "standard"

# Why a segment ended, for the diagnostics panel and latency work.
CLOSE_SILENCE = "silence"
CLOSE_FAST_SILENCE = "fast_silence"
CLOSE_LONG_SPEECH = "long_speech"
CLOSE_MAX_LENGTH = "max_length"
CLOSE_FLUSH = "flush"
CLOSE_REVISION = "boundary_revision"
CLOSE_SPEECH_BOUNDARY = "retained_speech_boundary"

# Noise-floor adaptation per 20 ms non-speech frame. Asymmetric on purpose: fall
# quickly to a quieter room, climb slowly so one noisy pause cannot desensitise
# boundary detection for the next minute.
_FLOOR_FALL = 0.25
_FLOOR_RISE = 0.005

_MAX_FALL_FRAMES = 2  # <=40 ms from speech to the floor counts as an abrupt stop


@dataclass
class SegmenterConfig:
    sensitivity: str = DEFAULT_SENSITIVITY  # low | standard | high
    silence_threshold: float | None = None  # explicit override; None = from sensitivity
    min_speech_seconds: float = 0.08  # retain short answers and interjections
    close_after_silence: float = 0.6  # silence needed to end a segment
    fast_close_seconds: float = 0.35  # ...unless the boundary is unambiguous
    long_speech_seconds: float = 4.0  # after this much speech, a dip is enough
    weak_boundary_silence: float = 0.3  # breaths are not hard sentence boundaries
    retained_boundary_silence: float = 0.12  # candidate only; never closes a phrase by itself
    max_segment_seconds: float = 8.0  # force-split ceiling to keep latency bounded
    pre_roll_seconds: float = 0.25  # audio kept before speech onset
    overlap_seconds: float = 0.8  # enough acoustic context for boundary words
    adaptive_close: bool = False  # energy decay alone does not prove semantic completion
    revision_lookahead_seconds: float = 0.6

    @property
    def threshold(self) -> float:
        """RMS below this is silence."""
        if self.silence_threshold is not None:
            return self.silence_threshold
        return SENSITIVITY_THRESHOLDS.get(
            self.sensitivity, SENSITIVITY_THRESHOLDS[DEFAULT_SENSITIVITY]
        )


class SpeechSegmenter:
    """Stateful stream segmenter. Not thread-safe; feed it from one thread."""

    def __init__(self, config: SegmenterConfig | None = None, detector=None):
        self.config = config or SegmenterConfig()
        self.detector = detector
        self._resampler: StreamingResampler | None = None
        self.last_processed = np.empty(0, np.float32)
        self.completed_reasons: list[str] = []
        self.completed_final: list[bool] = []
        self._revision: list[np.ndarray] = []
        self._revision_start = 0.0
        self._revision_future = 0
        self.flushed_revision: tuple[np.ndarray, float, float] | None = None
        self._deferred_segments = []
        self.flushed_following = []
        self._fresh_samples = 0
        self._soft_boundary = None
        pre_roll_frames = max(1, int(self.config.pre_roll_seconds / _FRAME_SECONDS))
        self._pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_frames)
        self._pending = np.empty(0, dtype=np.float32)  # residue < one frame
        self._segment: list[np.ndarray] = []
        self._in_speech = False
        self._speech_seconds = 0.0
        self._silence_run = 0.0
        self._noise_floor = 0.0
        self._last_level = 0.0
        self._last_close_reason = ""
        self._frame_cursor = 0
        self._segment_start = 0.0
        self.completed_times: list[tuple[float, float]] = []
        self._emitted_time = (0.0, 0.0)
        self._reset_boundary()

    def push(self, samples: np.ndarray, rate: int) -> list[np.ndarray]:
        """Feed captured audio; returns zero or more completed speech segments."""
        if self._resampler is None:
            self._resampler = StreamingResampler(rate)
        if self._resampler.rate != rate:
            raise ValueError("Reset segmentation at a capture rate discontinuity")
        mono = self._resampler.push(samples)
        self.last_processed = mono
        self.completed_times = []
        self.completed_reasons = []
        self.completed_final = []
        if mono.size == 0:
            return []
        buffer = np.concatenate([self._pending, mono])
        frame_count = buffer.size // _FRAME_SAMPLES
        self._pending = buffer[frame_count * _FRAME_SAMPLES :]

        completed: list[np.ndarray] = []
        for i in range(frame_count):
            frame = buffer[i * _FRAME_SAMPLES : (i + 1) * _FRAME_SAMPLES]
            self._frame_cursor += 1
            if self._revision:
                self._revision.append(frame)
                self._revision_future += len(frame)
                if self._revision_future >= int(
                    self.config.revision_lookahead_seconds * TARGET_RATE
                ):
                    repair = np.concatenate(self._revision)
                    completed.append(repair)
                    self.completed_times.append((self._revision_start, len(repair) / TARGET_RATE))
                    self.completed_reasons.append(CLOSE_REVISION)
                    self.completed_final.append(True)
                    self._revision = []
                    for deferred, timing, reason, final in self._deferred_segments:
                        completed.append(deferred)
                        self.completed_times.append(timing)
                        self.completed_reasons.append(reason)
                        self.completed_final.append(final)
                    self._deferred_segments = []
            segment = self._process_frame(frame)
            if segment is not None:
                tentative = (
                    self._last_close_reason == CLOSE_MAX_LENGTH
                    and self.config.revision_lookahead_seconds > 0
                )
                if self._revision:
                    # Finalization must follow source order. A short following
                    # phrase can close before the previous lookahead completes.
                    # Publishing it first advances stitching past unrepaired words.
                    self._deferred_segments.append(
                        (segment, self._emitted_time, self._last_close_reason, not tentative)
                    )
                else:
                    completed.append(segment)
                    self.completed_times.append(self._emitted_time)
                    self.completed_reasons.append(self._last_close_reason)
                    self.completed_final.append(not tentative)
                if tentative:
                    self._revision = [segment]
                    self._revision_start = self._emitted_time[0]
                    self._revision_future = 0
        return completed

    def flush(self) -> np.ndarray | None:
        """Close any open segment (used when capture stops)."""
        tail = self._resampler.flush() if self._resampler else np.empty(0, np.float32)
        self.last_processed = tail
        residue = np.concatenate([self._pending, tail])
        if self._revision:
            repair = np.concatenate([*self._revision, residue])
            self.flushed_revision = (repair, self._revision_start, len(repair) / TARGET_RATE)
            self._revision = []
        self.flushed_following, self._deferred_segments = self._deferred_segments, []
        if residue.size:
            if self._in_speech:
                self._segment.append(residue)
                self._fresh_samples += len(residue)
            elif _rms(residue) >= self._speech_gate():
                self._segment = [*self._pre_roll, residue]
                self._segment_start = (
                    self._frame_cursor * _FRAME_SAMPLES - sum(len(f) for f in self._pre_roll)
                ) / TARGET_RATE
                self._speech_seconds += len(residue) / TARGET_RATE
                self._fresh_samples += len(residue)
        segment = self._close_segment(CLOSE_FLUSH)
        self._pre_roll.clear()
        self._pending = np.empty(0, dtype=np.float32)
        return segment

    # ---------- diagnostics ----------

    @property
    def noise_floor(self) -> float:
        """Rolling estimate of the room/stream floor, in RMS."""
        return self._noise_floor

    @property
    def last_close_reason(self) -> str:
        """Why the most recent segment ended; one of the CLOSE_* constants."""
        return self._last_close_reason

    @property
    def last_level(self) -> float:
        """RMS of the most recent analysis frame, for level meters and health."""
        return self._last_level

    # ---------- internals ----------

    def _process_frame(self, frame: np.ndarray) -> np.ndarray | None:
        cfg = self.config
        level = _rms(frame)
        self._last_level = level
        is_speech = self.detector(frame) if self.detector else level >= self._speech_gate()
        if not is_speech:
            self._update_noise_floor(level)

        if not self._in_speech:
            self._pre_roll.append(frame)
            if is_speech:
                self._segment = list(self._pre_roll)
                self._segment_start = (self._frame_cursor - len(self._segment)) * _FRAME_SECONDS
                self._pre_roll.clear()  # its frames now belong to the segment
                self._in_speech = True
                self._speech_seconds = _FRAME_SECONDS
                self._fresh_samples = len(frame)
                self._silence_run = 0.0
                self._soft_boundary = None
                self._reset_boundary()
            return None

        self._segment.append(frame)
        self._fresh_samples += len(frame)
        if is_speech:
            self._speech_seconds += _FRAME_SECONDS
            self._silence_run = 0.0
            self._reset_boundary()
        else:
            self._silence_run += _FRAME_SECONDS
            self._track_boundary(level)
            if self._silence_run >= cfg.retained_boundary_silence:
                self._soft_boundary = len(self._segment), self._speech_seconds

        strong = cfg.adaptive_close and self._is_strong_boundary()
        close_after = cfg.fast_close_seconds if strong else cfg.close_after_silence
        if self._silence_run >= close_after:
            return self._close_segment(CLOSE_FAST_SILENCE if strong else CLOSE_SILENCE)

        if (
            self._speech_seconds >= cfg.long_speech_seconds
            and self._silence_run >= cfg.weak_boundary_silence
        ):
            return self._close_segment(CLOSE_LONG_SPEECH)

        if self._segment_seconds() >= cfg.max_segment_seconds:
            # A brief real pause can fall below the hard-close threshold and
            # precede a forced cut mid-word. Prefer that recorded acoustic
            # boundary, retaining all subsequent samples for the next window.
            # This is retrospective segmentation, not discarding the tail.
            if self._soft_boundary:
                cut, prefix_speech = self._soft_boundary
                prefix_seconds = cut * _FRAME_SECONDS
                age = (len(self._segment) - cut) * _FRAME_SECONDS
                if (
                    prefix_seconds >= min(2.0, cfg.max_segment_seconds / 2)
                    and age <= cfg.max_segment_seconds / 2
                    and prefix_speech >= cfg.min_speech_seconds
                ):
                    overlap = int(
                        min(cfg.overlap_seconds, cfg.max_segment_seconds * 0.3) / _FRAME_SECONDS
                    )
                    following = self._segment[max(0, cut - overlap) :]
                    following_speech = self._speech_seconds - prefix_speech
                    fresh = len(self._segment) - cut
                    self._segment = self._segment[:cut]
                    self._speech_seconds = prefix_speech
                    segment = self._emit_if_speechy()
                    self._last_close_reason = CLOSE_SPEECH_BOUNDARY
                    self._segment = list(following)
                    self._segment_start = (self._frame_cursor - len(following)) * _FRAME_SECONDS
                    self._speech_seconds = following_speech + cfg.min_speech_seconds
                    self._fresh_samples = fresh * _FRAME_SAMPLES
                    self._soft_boundary = None
                    self._reset_boundary()
                    return segment
            # Mid-speech split: keep a small tail so the next segment
            # still contains the word being spoken across the boundary.
            overlap = min(cfg.overlap_seconds, cfg.max_segment_seconds * 0.3)
            overlap_frames = max(1, int(overlap / _FRAME_SECONDS))
            tail = self._segment[-overlap_frames:]
            segment = self._emit_if_speechy()
            self._last_close_reason = CLOSE_MAX_LENGTH
            self._segment = list(tail)
            self._segment_start = (self._frame_cursor - len(tail)) * _FRAME_SECONDS
            self._speech_seconds = cfg.min_speech_seconds  # assume tail is speech
            self._silence_run = 0.0
            self._fresh_samples = 0
            self._soft_boundary = None
            self._reset_boundary()
            return segment

        return None

    def _close_segment(self, reason: str) -> np.ndarray | None:
        segment = self._emit_if_speechy()
        self._last_close_reason = reason
        self._segment = []
        self._in_speech = False
        self._speech_seconds = 0.0
        self._silence_run = 0.0
        self._fresh_samples = 0
        self._soft_boundary = None
        self._reset_boundary()
        return segment

    def _emit_if_speechy(self) -> np.ndarray | None:
        if (
            not self._segment
            or self._speech_seconds < self.config.min_speech_seconds
            or self._fresh_samples == 0
        ):
            return None
        self._emitted_time = (self._segment_start, sum(len(f) for f in self._segment) / TARGET_RATE)
        return np.concatenate(self._segment)

    def _segment_seconds(self) -> float:
        return len(self._segment) * _FRAME_SECONDS

    # ---------- boundary strength ----------

    def _speech_gate(self) -> float:
        # Energy-only fallback if the bundled VAD cannot load. An absolute
        # threshold of 0.008 previously erased clean speech at modest volume.
        base = self.config.threshold
        if self.config.silence_threshold is None:
            base *= 0.1
        return max(base, self._noise_floor * 2.5)

    def _update_noise_floor(self, level: float) -> None:
        weight = _FLOOR_FALL if level < self._noise_floor else _FLOOR_RISE
        self._noise_floor += (level - self._noise_floor) * weight

    def _quiet_gate(self) -> float:
        """Level at or below which audio counts as "back to the floor"."""
        threshold = self.config.threshold
        return min(threshold * 0.9, max(self._noise_floor * 1.6, threshold * 0.55))

    def _reset_boundary(self) -> None:
        self._fell_to_floor = False
        self._fall_frames = 0
        self._tail_peak = 0.0

    def _track_boundary(self, level: float) -> None:
        gate = self._quiet_gate()
        if not self._fell_to_floor:
            if level <= gate:
                self._fell_to_floor = True
                self._tail_peak = level
            else:
                self._fall_frames += 1  # still decaying: the stop was not abrupt
        elif level > self._tail_peak:
            self._tail_peak = level

    def _is_strong_boundary(self) -> bool:
        """True when energy fell fast and has stayed near the noise floor."""
        return (
            self._fell_to_floor
            and self._fall_frames <= _MAX_FALL_FRAMES
            and self._tail_peak <= self._quiet_gate()
        )


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0
