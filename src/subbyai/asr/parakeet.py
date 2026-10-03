"""Local CPU recognition with the pinned, int8 Parakeet ONNX model.

The engine uses the application-managed model cache and never downloads while
loading. It supports 25 European languages, but reports neither a language nor
confidence and accepts no vocabulary hints. Translation requires an explicitly
selected input language. These limits are displayed in the model catalogue.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from .engine import EngineError, Transcript
from .models import ModelManager

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

#: Bound automatic CPU parallelism to leave resources for capture and the UI.
_INTRA_OP_THREADS_CAP = 8


def _session_options(cpu_threads: int = 0) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    # Basic rewrites keep startup costs bounded for an interactive application.
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    options.intra_op_num_threads = cpu_threads or _decode_threads()
    return options


def _decode_threads() -> int:
    try:
        import psutil

        cores = psutil.cpu_count(logical=False) or 0
    except Exception:
        cores = 0
    return max(1, min(_INTRA_OP_THREADS_CAP, cores)) if cores else 4


class ParakeetEngine:
    """One loaded Parakeet model. Mirrors ``TranscriptionEngine``'s surface."""

    def __init__(
        self,
        model_name: str,
        device_preference: str = "auto",
        models: Any = None,
    ) -> None:
        self._model_name = model_name
        self._device_preference = device_preference
        self._models = models if models is not None else ModelManager()
        self.cpu_threads = 0
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device_preference(self) -> str:
        return self._device_preference

    @property
    def device(self) -> str:
        # The bundled ONNX Runtime uses its CPU execution provider.
        return "cpu"

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                import onnx_asr
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise EngineError(
                    "This engine isn't installed with this copy of the app."
                ) from exc
            local = self._models.local_path(self._model_name)
            if local is None:
                # Download only through ModelManager, with explicit user action.
                raise EngineError("That speech model hasn't been downloaded yet.")
            log.info("Loading %s from %s", self._model_name, local)
            try:
                self._model = onnx_asr.load_model(
                    _onnx_asr_name(self._model_name),
                    str(local),
                    quantization="int8",
                    sess_options=_session_options(self.cpu_threads),
                )
            except Exception as exc:
                log.exception("Parakeet load failed")
                raise EngineError(
                    "Couldn't start that speech engine. Try a different one."
                ) from exc

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, vocabulary: str = ""
    ) -> Transcript:
        """Decode one 16 kHz mono float32 buffer.

        ``language`` and ``vocabulary`` are accepted for engine-interface
        parity and ignored: onnx-asr has no language hint and no decoder
        prompt to bias.
        """
        model = self._model
        if model is None:
            raise EngineError("The speech model isn't loaded yet.")
        try:
            text = model.recognize(audio, sample_rate=16000)
        except Exception as exc:
            log.exception("Transcription failed")
            raise EngineError("Couldn't process that audio.") from exc
        return Transcript(
            text=str(text or "").strip(),
            language=None,
            duration=len(audio) / 16000.0,
            # Nothing to report, rather than nothing to be confident about.
            confidence=1.0,
            speech_probability=1.0,
            confidence_known=False,
        )

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
        import gc

        gc.collect()
        log.info("Released %s", self._model_name)


def _onnx_asr_name(model_id: str) -> str:
    """Our catalogue id to the name onnx-asr knows it by."""
    return _ONNX_ASR_NAMES.get(model_id, model_id)


_ONNX_ASR_NAMES = {
    "parakeet-tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3",
    "parakeet-tdt-0.6b-v2": "nemo-parakeet-tdt-0.6b-v2",
}
