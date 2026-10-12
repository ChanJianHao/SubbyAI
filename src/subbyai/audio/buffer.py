"""Bounded capture buffer retaining channel layout, rate and source-clock offsets."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class CapturedBlock:
    samples: np.ndarray
    rate: int
    start: float
    epoch: int


class CaptureBuffer:
    def __init__(self, seconds: float = 30):
        self.seconds = seconds
        self._lock = threading.Lock()
        self.clear()

    def write(
        self,
        samples: np.ndarray,
        rate: int,
        *,
        discontinuity: bool = False,
        gap_seconds: float = 0.0,
    ) -> CapturedBlock:
        if rate <= 0 or samples.ndim not in (1, 2):
            raise ValueError("Invalid capture format")
        audio = np.array(samples, dtype=np.float32, copy=True, order="C")
        channels = audio.shape[1] if audio.ndim == 2 else 1
        if channels < 1:
            raise ValueError("No capture channels")
        with self._lock:
            self._clock += max(0.0, gap_seconds)
            fmt = (rate, channels)
            if (
                discontinuity
                or gap_seconds > 0
                or (self._format is not None and fmt != self._format)
            ):
                self._epoch += 1
                self.discontinuities += 1
            self._format = fmt
            block = CapturedBlock(audio, rate, self._clock, self._epoch)
            self._clock += len(audio) / rate
            self._blocks.append(block)
            self._duration += len(audio) / rate
            # Drop whole frames/blocks; offsets on survivors describe the gap.
            while self._duration > self.seconds and self._blocks:
                old = self._blocks.popleft()
                self._duration -= len(old.samples) / old.rate
                self._dropped += old.samples.size
            return block

    def read(self) -> CapturedBlock | None:
        with self._lock:
            if not self._blocks:
                return None
            block = self._blocks.popleft()
            self._duration -= len(block.samples) / block.rate
            return block

    def clear(self) -> None:
        with self._lock:
            self._blocks: deque[CapturedBlock] = deque()
            self._clock = self._duration = 0.0
            self._format = None
            self._epoch = self._dropped = self.discontinuities = 0

    @property
    def dropped_frames(self) -> int:
        """Scalar channel samples lost; retained name matches diagnostics contract."""
        with self._lock:
            return self._dropped
