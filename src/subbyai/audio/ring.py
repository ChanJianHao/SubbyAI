"""A bounded float32 ring between capture and the pipeline.

Capture callbacks write raw frames here without running segmentation or inference.
The consumer discards and counts the oldest frames when it falls behind. Multi-channel
blocks remain interleaved, so the consumer reshapes them with (-1, channels)."""

from __future__ import annotations

import threading

import numpy as np


class RingBuffer:
    """Single-producer/single-consumer ring. Safe for one writer and one reader."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._buffer = np.zeros(capacity, dtype=np.float32)
        self._write = 0
        self._size = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @classmethod
    def for_seconds(cls, seconds: float, rate: int, channels: int = 1) -> RingBuffer:
        """Size the ring by wall-clock audio, which is how the pipeline thinks."""
        return cls(max(1, int(seconds * rate * channels)))

    # ---------- producer ----------

    def write(self, block: np.ndarray) -> int:
        """Append samples, dropping the oldest on overflow. Returns frames dropped."""
        samples = np.ascontiguousarray(block, dtype=np.float32).reshape(-1)
        count = samples.size
        if count == 0:
            return 0

        capacity = self._buffer.size
        with self._lock:
            if count >= capacity:
                # The block alone outruns the ring: keep only its newest tail.
                dropped = self._size + count - capacity
                self._buffer[:] = samples[count - capacity :]
                self._write = 0
                self._size = capacity
                self._dropped += dropped
                return dropped

            end = self._write + count
            if end <= capacity:
                self._buffer[self._write : end] = samples
            else:
                split = capacity - self._write
                self._buffer[self._write :] = samples[:split]
                self._buffer[: end - capacity] = samples[split:]
            self._write = end % capacity

            dropped = max(0, self._size + count - capacity)
            self._size = min(capacity, self._size + count)
            self._dropped += dropped
            return dropped

    # ---------- consumer ----------

    def read(self, max_frames: int) -> np.ndarray:
        """Take up to ``max_frames`` oldest samples. Returns a copy, possibly empty."""
        if max_frames <= 0:
            return np.empty(0, dtype=np.float32)

        capacity = self._buffer.size
        with self._lock:
            count = min(max_frames, self._size)
            if count == 0:
                return np.empty(0, dtype=np.float32)
            start = (self._write - self._size) % capacity
            end = start + count
            if end <= capacity:
                out = self._buffer[start:end].copy()
            else:
                out = np.concatenate((self._buffer[start:], self._buffer[: end - capacity]))
            self._size -= count
            return out

    def clear(self) -> None:
        """Discard pending audio and reset the drop count (a fresh start)."""
        with self._lock:
            self._buffer.fill(0)
            self._write = 0
            self._size = 0
            self._dropped = 0

    # ---------- diagnostics ----------

    @property
    def capacity(self) -> int:
        return int(self._buffer.size)

    @property
    def available(self) -> int:
        with self._lock:
            return self._size

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped
