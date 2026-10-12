"""Streaming Silero VAD v6, using the asset bundled with pinned faster-whisper.

State belongs to a session. Only the detector receives amplitude conditioning;
ASR receives unchanged audio. Speech is padded by the segmenter, never cut here.
"""

from __future__ import annotations

import numpy as np


class SileroSpeechDetector:
    def __init__(self, sensitivity: str = "standard"):
        from faster_whisper.vad import get_vad_model

        self._session = get_vad_model().session
        self.threshold = {"low": 0.6, "standard": 0.45, "high": 0.3}.get(sensitivity, 0.45)
        self._pending = np.empty(0, np.float32)
        self._context = np.zeros((1, 64), np.float32)
        self._h = np.zeros((1, 1, 128), np.float32)
        self._c = np.zeros((1, 1, 128), np.float32)
        self._active = False
        self.probability = 0.0

    def __call__(self, frame: np.ndarray) -> bool:
        self._pending = np.concatenate([self._pending, frame])
        while len(self._pending) >= 512:
            chunk, self._pending = self._pending[:512], self._pending[512:]
            rms = float(np.sqrt(np.mean(chunk**2)))
            # Quiet speech should not be mistaken for digital silence. Limit
            # gain to avoid amplifying near-zero capture noise without bound.
            chunk = chunk * min(32.0, max(1.0, 0.03 / max(rms, 1e-8)))
            inputs = np.concatenate([self._context, chunk[None, :]], axis=1)
            output, self._h, self._c = self._session.run(
                None, {"input": inputs, "h": self._h, "c": self._c}
            )
            self._context = chunk[None, -64:].copy()
            self.probability = float(np.asarray(output).reshape(-1)[-1])
            gate = self.threshold - 0.15 if self._active else self.threshold
            self._active = self.probability >= gate
        return self._active
