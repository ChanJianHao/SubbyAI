"""Native-channel downmix and stateful, anti-aliased rational resampling.

No gain normalization, denoising or silence removal is applied to ASR audio.
The resampler retains fractional phase and filter history between callbacks.
"""

from __future__ import annotations

from math import gcd

import numpy as np

TARGET_RATE = 16000


def downmix(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim not in (1, 2):
        raise ValueError("Audio must have shape (frames,) or (frames, channels)")
    if not np.isfinite(audio).all():
        raise ValueError("Audio contains non-finite samples")
    if audio.ndim == 1:
        return np.ascontiguousarray(audio)
    if audio.shape[1] < 1:
        raise ValueError("Audio has no channels")
    mono = audio.mean(axis=1)
    # Averaging is correct for ordinary stereo. Severe phase cancellation can
    # erase speech entirely; retain the strongest intact channel in that case.
    energies = np.mean(audio**2, axis=0) if len(audio) else np.zeros(audio.shape[1])
    strongest = int(np.argmax(energies))
    if len(audio) and np.mean(mono**2) < energies[strongest] * 0.05:
        mono = audio[:, strongest]
    return np.ascontiguousarray(mono, dtype=np.float32)


class StreamingResampler:
    def __init__(self, rate: int):
        if rate <= 0:
            raise ValueError("Sample rate must be positive")
        self.rate = rate
        self.input_samples = 0
        self.output_samples = 0
        self._closed = False
        self._data = np.empty(0, np.float32)
        self._data_start = 0
        self._half = int(np.ceil(32 * max(1, rate / TARGET_RATE)))
        self._offsets = np.arange(-self._half, self._half + 1)
        if rate != TARGET_RATE:
            # Polyphase Kaiser-windowed sinc; only the rational phases reachable
            # at this rate are needed. Integer sample positions prevent drift.
            self._phases = TARGET_RATE // gcd(rate, TARGET_RATE)
            fractions = np.arange(self._phases)[:, None] / self._phases
            distance = self._offsets[None, :] - fractions
            cutoff = 0.94 * min(1.0, TARGET_RATE / rate)
            window = np.i0(8.6 * np.sqrt(np.maximum(0, 1 - (distance / self._half) ** 2)))
            window /= np.i0(8.6)
            window[np.abs(distance) > self._half] = 0
            kernel = cutoff * np.sinc(cutoff * distance) * window
            self._kernel = kernel / kernel.sum(axis=1, keepdims=True)

    def push(self, samples: np.ndarray) -> np.ndarray:
        if self._closed:
            raise RuntimeError("Resampler is already flushed")
        mono = downmix(samples)
        if not len(mono):
            return mono
        if self.rate == TARGET_RATE:
            self.input_samples += len(mono)
            self.output_samples += len(mono)
            return mono
        self._data = np.concatenate([self._data, mono])
        self.input_samples += len(mono)
        return self._produce(final=False)

    def flush(self) -> np.ndarray:
        if self._closed:
            return np.empty(0, np.float32)
        self._closed = True
        return self._produce(final=True) if self.rate != TARGET_RATE else np.empty(0, np.float32)

    def _produce(self, *, final: bool) -> np.ndarray:
        if not self.input_samples:
            return np.empty(0, np.float32)
        if final:
            end = (self.input_samples * TARGET_RATE + self.rate // 2) // self.rate
        else:
            # Wait for the filter's right-hand context; do not extrapolate at
            # callback edges. Only actual stream ends receive endpoint extension.
            end = (
                max(0, self.input_samples - self._half) * TARGET_RATE + self.rate - 1
            ) // self.rate
        output = []
        for begin in range(self.output_samples, end, 2048):
            index = np.arange(begin, min(end, begin + 2048), dtype=np.int64)
            numerator = index * self.rate
            centers = numerator // TARGET_RATE
            phases = (numerator % TARGET_RATE) // gcd(self.rate, TARGET_RATE)
            source = np.clip(centers[:, None] + self._offsets, 0, self.input_samples - 1)
            values = self._data[source - self._data_start]
            output.append(np.sum(values * self._kernel[phases], axis=1).astype(np.float32))
        self.output_samples = max(self.output_samples, end)
        keep = max(0, self.output_samples * self.rate // TARGET_RATE - self._half)
        remove = min(len(self._data), max(0, keep - self._data_start))
        self._data = self._data[remove:].copy()
        self._data_start += remove
        return np.concatenate(output) if output else np.empty(0, np.float32)


def to_mono_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    """Whole-buffer helper. Streaming callers must retain StreamingResampler."""
    converter = StreamingResampler(rate)
    return np.concatenate([converter.push(samples), converter.flush()])
