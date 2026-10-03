"""Local recognition engines and a bounded warm model cache.

Recognition returns original-language text, confidence and language metadata.
Translation is an independent stage. Heavy imports and model loading happen off
the GUI thread; cache entries change when model, device or precision changes."""

from __future__ import annotations

import gc
import logging
import math
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import capability
from .models import ModelManager

if TYPE_CHECKING:  # only for annotations; keep the import cost off startup
    import numpy as np

log = logging.getLogger(__name__)

# Zero-length segments still carry a probability worth averaging.
_MIN_SEGMENT_WEIGHT = 0.05


class EngineError(Exception):
    """A load or transcribe failure, carrying text fit to show the user."""


@dataclass(frozen=True, slots=True)
class Transcript:
    """One decode. The pipeline turns this into a ``CaptionSegment``."""

    text: str
    language: str | None = None
    language_confidence: float = 0.0
    confidence: float = 0.0
    speech_probability: float = 0.0
    duration: float = 0.0

    confidence_known: bool = True
    """False when the engine reports no confidence at all.

    Not the same as a confidence of zero. Some engines return text and nothing
    else, and scoring that as "completely unsure" would italicise and dim every
    caption they produce — presenting an absent signal as a negative one.
    """


def engine_for(model_name: str, device_preference: str = "auto", models: Any = None) -> Any:
    """Build the engine that can actually run this model.

    Routing is by the catalogue's ``provider`` field, not by guessing from the
    name: two engines could plausibly offer a model called "large-v3", and the
    one that answers has to be a recorded fact rather than a string match.
    """
    from .catalogue import spec_or_guess

    if spec_or_guess(model_name).provider == "onnx-asr":
        from .parakeet import ParakeetEngine

        return ParakeetEngine(model_name, device_preference, models)
    return TranscriptionEngine(model_name, device_preference, models)


def resolve_compute_device(preference: str) -> str:
    """Map auto/gpu/cpu onto what this machine can really do.

    Named for ``Settings.captions.compute_device`` and kept distinct from
    ``subbyai.audio.resolve_device``, which picks an audio *input* device.
    """
    if preference == "cpu":
        return "cpu"
    if capability.detect().has_cuda:
        capability._register_nvidia_wheel_dlls()
        return "cuda"
    if preference == "gpu":
        log.warning("GPU requested but none is usable; running on CPU")
    return "cpu"


def summarize_segments(segments: Iterable[Any], info: Any) -> Transcript:
    """Fold faster-whisper's segments and info into a single ``Transcript``.

    Pure, and separated from the engine so the confidence mapping can be tested
    without a model. ``exp(avg_logprob)`` is the decoder's average per-token
    probability; weighting it by segment duration stops a stray half-second
    fragment from dragging a long, confident utterance down. ``no_speech_prob``
    is averaged plainly — it is a per-window judgement about whether there was
    speech at all, not a property of the text.
    """
    texts: list[str] = []
    weights: list[float] = []
    probabilities: list[float] = []
    no_speech: list[float] = []

    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if text:
            texts.append(text)
        start = _as_float(getattr(segment, "start", 0.0))
        end = _as_float(getattr(segment, "end", 0.0))
        weights.append(max(end - start, _MIN_SEGMENT_WEIGHT))
        # avg_logprob is <= 0 by definition; clamping first keeps exp() from
        # overflowing on a garbage value.
        logprob = min(0.0, _as_float(getattr(segment, "avg_logprob", 0.0)))
        probabilities.append(_clamp01(math.exp(logprob)))
        no_speech.append(_clamp01(_as_float(getattr(segment, "no_speech_prob", 0.0))))

    if weights:
        confidence = _clamp01(
            sum(p * w for p, w in zip(probabilities, weights, strict=True)) / sum(weights)
        )
        speech_probability = _clamp01(1.0 - sum(no_speech) / len(no_speech))
    else:
        # Nothing decoded: no evidence of speech, and no confidence to claim.
        confidence = 0.0
        speech_probability = 0.0

    language = getattr(info, "language", None) or None
    return Transcript(
        text=" ".join(texts).strip(),
        language=str(language) if language else None,
        language_confidence=_clamp01(_as_float(getattr(info, "language_probability", 0.0))),
        confidence=confidence,
        speech_probability=speech_probability,
        duration=max(0.0, _as_float(getattr(info, "duration", 0.0))),
    )


class TranscriptionEngine:
    """Owns one loaded Whisper model.

    ``load`` is safe to call from several threads and is a no-op once loaded;
    ``transcribe`` expects a single worker thread, matching the pipeline.
    """

    def __init__(
        self,
        model_name: str,
        device_preference: str = "auto",
        models: ModelManager | None = None,
    ) -> None:
        self._model_name = model_name
        self._device_preference = device_preference
        # Injected so the engine and the catalogue can never disagree about
        # where models live; a bare ModelManager() would ignore a relocated
        # cache and make every test that passes one invisible here.
        self._models = models or ModelManager()
        self._device: str | None = None
        self._model: Any = None
        self._lock = threading.Lock()
        self.compute_type = "auto"
        self.cpu_threads = 0
        self.beam_size = 1

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device_preference(self) -> str:
        return self._device_preference

    @property
    def device(self) -> str:
        """Where this engine runs — predicted before load, actual after."""
        return self._device or resolve_compute_device(self._device_preference)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Bring the model into memory. Raises ``EngineError`` with a message."""
        with self._lock:
            if self._model is not None:
                return
            device = resolve_compute_device(self._device_preference)
            local_path = self._models.local_path(self._model_name)
            if local_path is None:
                log.warning("Model %s is not downloaded", self._model_name)
            log.info("Loading model %s on %s", self._model_name, device)
            try:
                self._model = self._construct(local_path, device)
            except EngineError:
                # Already phrased for the user, and retrying on CPU cannot help
                # a model that simply is not on disk. Re-wrapping turned "it
                # isn't downloaded" into "try a lower quality level".
                raise
            except Exception as exc:
                if device != "cuda":
                    log.exception("Model load failed")
                    raise EngineError(_friendly_load_error(exc)) from exc
                log.warning("GPU load failed (%s); retrying on CPU", exc)
                try:
                    self._model = self._construct(local_path, "cpu")
                except Exception as cpu_exc:
                    log.exception("Model load failed on CPU as well")
                    raise EngineError(_friendly_load_error(cpu_exc)) from cpu_exc
                device = "cpu"
            self._device = device
            log.info("Model %s ready on %s", self._model_name, device)

    def _construct(self, path: Path | None, device: str):
        if self.compute_type == "auto" and not self.cpu_threads:
            return _build_model(self._model_name, path, device)
        precision = self.compute_type
        if device == "cpu" and precision in ("float16", "int8_float16"):
            precision = "int8"  # GPU fallback must not ask the CPU for FP16.
        return _build_model(
            self._model_name, path, device, precision=precision, threads=self.cpu_threads
        )

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, vocabulary: str = ""
    ) -> Transcript:
        """Decode one 16 kHz mono float32 buffer held in memory."""
        model = self._model
        if model is None:
            raise EngineError("The speech model isn't loaded yet.")
        try:
            prompt = vocabulary_prompt(vocabulary)
            segments, info = model.transcribe(
                _with_trailing_silence(audio),
                task="transcribe",  # never "translate": the original text must survive
                language=language or None,
                beam_size=self.beam_size,
                # Off deliberately. The segmenter has already found the speech,
                # and this would trim the trailing silence that tells the
                # decoder the utterance ended — which the shorter encoder
                # window depends on.
                vad_filter=False,
                condition_on_previous_text=False,  # stops repetition loops on live audio
                # What condition_on_previous_text gave up was a way to bias the
                # decoder with context. The vocabulary prompt puts a controlled
                # piece of that back: it primes this window only, and is not
                # carried between calls, so it cannot compound into a
                # repetition loop.
                initial_prompt=prompt or None,
            )
            return summarize_segments(segments, info)
        except Exception as exc:
            log.exception("Transcription failed")
            raise EngineError("Couldn't process that audio.") from exc

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            self._device = None
            gc.collect()  # CTranslate2 frees VRAM when the last reference drops
            log.info("Unloaded model %s", self._model_name)


class EngineCache:
    """Holds the one engine that survives pipeline stop/start.

    ``get`` intentionally does not load: loading a large model can take minutes,
    and doing it under this lock would stall every other caller. Callers invoke
    ``load()``, which is idempotent and serialized per engine.
    """

    def __init__(self, factory: Callable[[str, str], Any] | None = None) -> None:
        factory = factory or engine_for
        self._factory = factory
        self._lock = threading.Lock()
        self._engine: TranscriptionEngine | None = None
        self._key: tuple[str, str, str, str, int] | None = None

    @property
    def current(self) -> Any:
        return self._engine

    def get(
        self,
        model_name: str,
        device_preference: str = "auto",
        compute_type: str = "auto",
        cpu_threads: int = 0,
    ) -> Any:
        from .catalogue import spec_or_guess

        key = (
            spec_or_guess(model_name).provider,
            model_name,
            device_preference,
            compute_type,
            cpu_threads,
        )
        with self._lock:
            if self._engine is not None and self._key == key:
                return self._engine
            if self._engine is not None:
                log.info("Engine key changed %s -> %s; releasing the old model", self._key, key)
                self._engine.unload()
            self._engine = self._factory(model_name, device_preference)
            self._engine.compute_type = compute_type
            self._engine.cpu_threads = cpu_threads
            self._key = key
            return self._engine

    def evict_all(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.unload()
            self._engine = None
            self._key = None


_default_cache: EngineCache | None = None
_default_cache_lock = threading.Lock()


def default_cache() -> EngineCache:
    """The process-wide cache. Use this rather than making one per session."""
    global _default_cache
    with _default_cache_lock:
        if _default_cache is None:
            _default_cache = EngineCache()
        return _default_cache


# ---------- vocabulary ----------

#: The prompt is decoder conditioning and shares the context window with the
#: audio. Whisper's window is small; an unbounded word list would crowd out
#: the very speech it is meant to improve.
_MAX_PROMPT_TERMS = 24
_MAX_PROMPT_CHARS = 400


def vocabulary_prompt(vocabulary: str) -> str:
    """Turn a comma/newline-separated word list into a decoder prompt.

    Whisper's ``initial_prompt`` is plain conditioning text, so the list is
    shaped into a sentence the model can lean on. Pure, so the bounding rules
    are testable without a model.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for raw in vocabulary.replace("\n", ",").replace("\r", ",").split(","):
        term = raw.strip().strip(chr(34) + chr(39))
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    terms = terms[:_MAX_PROMPT_TERMS]
    if not terms:
        return ""
    prompt = f"Terms that may come up: {', '.join(terms)}."
    if len(prompt) <= _MAX_PROMPT_CHARS:
        return prompt
    # Trim at a term boundary and keep the full stop.
    clipped = prompt[:_MAX_PROMPT_CHARS]
    cut = clipped.rfind(",")
    return (clipped[:cut] if cut > 0 else clipped).rstrip() + "."


# ---------- internals ----------


def _build_model(
    model_name: str,
    local_path: Path | None,
    device: str,
    precision: str = "auto",
    threads: int = 0,
) -> Any:
    from faster_whisper import WhisperModel

    from . import snug_window

    snug_window.apply()

    compute_type = precision if precision != "auto" else ("float16" if device == "cuda" else "int8")
    if local_path is not None:
        # faster-whisper defaults to 4 threads whatever the machine has.
        threads = threads or (_decode_threads() if device == "cpu" else 0)
        # faster-whisper takes a directory verbatim, so a cached model loads
        # with no huggingface_hub involvement: no revision round-trip, and it
        # works with the network off.
        return WhisperModel(
            str(local_path),
            device=device,
            compute_type=compute_type,
            local_files_only=True,
            cpu_threads=threads,
        )
    # Deliberately no download fallback. faster-whisper would fetch the model
    # right here: synchronously, on the pipeline worker, with no progress and
    # no way to cancel — the exact failure this module's docstring says was
    # designed out. Downloading is the UI's job, ahead of time, where it can be
    # narrated and stopped.
    raise EngineError("That speech model hasn't been downloaded yet.")


def _with_trailing_silence(audio: np.ndarray) -> np.ndarray:
    """Append a little silence so the decoder can tell the utterance ended.

    A segment closed by the length limit is cut mid-word with nothing after it.
    Encoding that against a snug window is what made the decoder repeat itself
    and stall for seconds in testing.
    """
    import numpy

    from . import snug_window

    tail = int(snug_window.trailing_silence_seconds() * 16000)
    if tail <= 0 or not isinstance(audio, numpy.ndarray):
        return audio
    return numpy.concatenate([audio, numpy.zeros(tail, dtype=audio.dtype)])


def _decode_threads() -> int:
    """Physical cores, capped. Hyperthreads do not help a compute-bound decode."""
    try:
        import psutil

        cores = psutil.cpu_count(logical=False) or 0
    except Exception:
        cores = 0
    return max(1, min(8, cores)) if cores else 4


def _friendly_load_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "out of memory" in text or "cudaerrormemoryallocation" in text:
        return "The graphics card ran out of memory. Try a lower quality level."
    if isinstance(exc, PermissionError) or "permission" in text or "access is denied" in text:
        return "Couldn't read the model files. Check that the app can reach its data folder."
    if any(
        word in text
        for word in ("connect", "connection", "network", "offline", "resolve", "timed out", "404")
    ):
        return (
            "The speech model isn't downloaded yet and couldn't be fetched. "
            "Check your internet connection."
        )
    return "Couldn't start the speech model. Try a lower quality level, or restart the app."


def _as_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(result) or math.isinf(result) else result


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
