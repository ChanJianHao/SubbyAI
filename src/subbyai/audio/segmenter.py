"""Segment continuous audio into bounded speech phrases.

Downmix and resample to 16 kHz mono, retain a short preroll, and close phrases
at silence boundaries or a configurable duration limit. Overlap protects words
across forced splits. An adaptive energy gate avoids decoding pure silence;
recognition confidence supplies another filter. This is not a semantic VAD.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

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

# Noise-floor adaptation per 20 ms non-speech frame. Asymmetric on purpose: fall
# quickly to a quieter room, climb slowly so one noisy pause cannot desensitise
# boundary detection for the next minute.
_FLOOR_FALL = 0.25
_FLOOR_RISE = 0.005

_MAX_FALL_FRAMES = 2  # <=40 ms from speech to the floor counts as an abrupt stop
_INDEX_CACHE_LIMIT = 32

# Resample index arrays keyed by (input rate, block size). Block sizes from a
# given device are near-constant, so this is a handful of entries in practice.
_index_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


@dataclass
class SegmenterConfig:
    sensitivity: str = DEFAULT_SENSITIVITY  # low | standard | high
    silence_threshold: float | None = None  # explicit override; None = from sensitivity
    min_speech_seconds: float = 0.3  # segments with less speech are discarded
    close_after_silence: float = 0.6  # silence needed to end a segment
    fast_close_seconds: float = 0.35  # ...unless the boundary is unambiguous
    long_speech_seconds: float = 4.0  # after this much speech, a dip is enough
    weak_boundary_silence: float = 0.18  # what counts as that dip
    max_segment_seconds: float = 8.0  # force-split ceiling to keep latency bounded
    pre_roll_seconds: float = 0.25  # audio kept before speech onset
    overlap_seconds: float = 0.2  # carried into the next segment on force-split
    adaptive_close: bool = True  # False pins the close to close_after_silence

    @property
    def threshold(self) -> float:
        """RMS below this is silence."""
        if self.silence_threshold is not None:
            return self.silence_threshold
        return SENSITIVITY_THRESHOLDS.get(
            self.sensitivity, SENSITIVITY_THRESHOLDS[DEFAULT_SENSITIVITY]
        )


def to_mono_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    """Downmix to mono and linearly resample to 16 kHz float32.

    Pure, and the hottest function in the audio path — it runs on every captured
    block. Rebuilding the interpolation indices per call allocated 400-500
    temporaries a second, so they are cached by (rate, block size) instead.
    """
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    samples = np.asarray(samples, dtype=np.float32)
    if rate == TARGET_RATE or samples.size == 0:
        return samples
    positions, source = _resample_index(rate, samples.size)
    return np.interp(positions, source, samples).astype(np.float32)


def _resample_index(rate: int, size: int) -> tuple[np.ndarray, np.ndarray]:
    key = (rate, size)
    cached = _index_cache.get(key)
    if cached is None:
        target_count = max(1, round(size / rate * TARGET_RATE))
        positions = np.linspace(0, size - 1, target_count)
        source = np.arange(size, dtype=np.float64)  # float64 so np.interp needn't convert
        if len(_index_cache) >= _INDEX_CACHE_LIMIT:
            # Churn means the stream changed shape; a whole reset beats an LRU here.
            _index_cache.clear()
        cached = (positions, source)
        _index_cache[key] = cached
    return cached


class SpeechSegmenter:
    """Stateful stream segmenter. Not thread-safe; feed it from one thread."""

    def __init__(self, config: SegmenterConfig | None = None):
        self.config = config or SegmenterConfig()
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
        mono = to_mono_16k(samples, rate)
        self.completed_times = []
        if mono.size == 0:
            return []
        buffer = np.concatenate([self._pending, mono])
        frame_count = buffer.size // _FRAME_SAMPLES
        self._pending = buffer[frame_count * _FRAME_SAMPLES :]

        completed: list[np.ndarray] = []
        for i in range(frame_count):
            frame = buffer[i * _FRAME_SAMPLES : (i + 1) * _FRAME_SAMPLES]
            self._frame_cursor += 1
            segment = self._process_frame(frame)
            if segment is not None:
                completed.append(segment)
                self.completed_times.append(self._emitted_time)
        return completed

    def flush(self) -> np.ndarray | None:
        """Close any open segment (used when capture stops)."""
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
        is_speech = level >= cfg.threshold
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
                self._silence_run = 0.0
                self._reset_boundary()
            return None

        self._segment.append(frame)
        if is_speech:
            self._speech_seconds += _FRAME_SECONDS
            self._silence_run = 0.0
            self._reset_boundary()
        else:
            self._silence_run += _FRAME_SECONDS
            self._track_boundary(level)

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
            # Mid-speech split: keep a small tail so the next segment
            # still contains the word being spoken across the boundary.
            overlap_frames = max(1, int(cfg.overlap_seconds / _FRAME_SECONDS))
            tail = self._segment[-overlap_frames:]
            segment = self._emit_if_speechy()
            self._last_close_reason = CLOSE_MAX_LENGTH
            self._segment = list(tail)
            self._segment_start = (self._frame_cursor - len(tail)) * _FRAME_SECONDS
            self._speech_seconds = cfg.min_speech_seconds  # assume tail is speech
            self._silence_run = 0.0
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
        self._reset_boundary()
        return segment

    def _emit_if_speechy(self) -> np.ndarray | None:
        if not self._segment or self._speech_seconds < self.config.min_speech_seconds:
            return None
        self._emitted_time = (self._segment_start, len(self._segment) * _FRAME_SECONDS)
        return np.concatenate(self._segment)

    def _segment_seconds(self) -> float:
        return len(self._segment) * _FRAME_SECONDS

    # ---------- boundary strength ----------

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
