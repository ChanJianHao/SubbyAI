"""Minimal recognition contract shared by local and remote engines."""

from typing import Protocol

import numpy as np

from .engine import Transcript


class ASRProvider(Protocol):
    def load(self) -> None: ...

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, vocabulary: str = ""
    ) -> Transcript: ...

    def unload(self) -> None: ...
