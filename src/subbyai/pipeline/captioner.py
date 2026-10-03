"""The captioning pipeline: capture → segment → recognize → translate → emit.

Stage separation is the point. Four threads, each with one job:

    audio callback  ─write→  RingBuffer  ─read→  segment worker
                                                     │ speech segments
                                              recognition worker
                                                     │ CaptionSegment ── shown NOW
                                              translation worker
                                                     │ translation ── attached later

Two contracts follow from that shape and matter more than any other detail:

1. **Captions never wait for a translator.** The original is emitted the moment
   recognition finishes; the translation arrives as an update. If translation
   fails or the provider is down, the caption still stands.
2. **Nothing heavy runs on the audio callback.** It converts and writes to a
   ring buffer. Resampling and segmentation run on the consumer worker.

Stopping is asynchronous: ``stop()`` asks and returns; ``stopped`` tells you when
it is done. Another session waits until the previous workers have finished.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from PySide6.QtCore import QObject, Signal

from ..audio.base import AudioCapture, AudioCaptureError, AudioDevice, create_capture
from ..audio.ring import RingBuffer
from ..audio.segmenter import SegmenterConfig, SpeechSegmenter
from ..core.events import CaptionSegment, TranslationState
from ..core.health import HealthMonitor, HealthReport, HealthState
from ..core.settings import SessionConfig
from .hallucinations import HallucinationFilter
from .reconciliation import Reconciler

log = logging.getLogger(__name__)

#: Recognition backlog. Beyond this the oldest audio is dropped so captions stay
#: live rather than drifting minutes behind the sound.
_MAX_PENDING_SEGMENTS = 3
_RING_SECONDS = 30.0
#: Ring capacity is sized for the fastest rate a device is likely to deliver, so
#: it still holds _RING_SECONDS of 48 kHz audio.
_MAX_INPUT_RATE = 48000
#: Upper bound on one read; the segmenter is happy with any block size.
_READ_SAMPLES = _MAX_INPUT_RATE // 2
_CONSUMER_BLOCK = 0.05
_LEVEL_INTERVAL = 0.1
_HEALTH_INTERVAL = 0.5
#: How many previous line pairs the LLM translation lane gets for context.
_CONTEXT_PAIRS = 4


@dataclass(frozen=True, slots=True)
class AudioWork:
    audio: np.ndarray
    start: float
    duration: float
    queued_at: float


@dataclass(frozen=True, slots=True)
class PipelineMetrics:
    asr_seconds: float = 0.0
    translation_seconds: float = 0.0
    queue_wait_seconds: float = 0.0
    subtitle_latency_seconds: float = 0.0
    dropped_segments: int = 0
    dropped_translations: int = 0
    dropped_audio_samples: int = 0
    recognition_queue: int = 0
    translation_queue: int = 0


class PipelinePhase(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class Captioner(QObject):
    """Owns the pipeline. All signals are delivered on the Qt thread."""

    segment_ready = Signal(object)  # CaptionSegment — original text, show now
    segment_updated = Signal(object)  # CaptionSegment — translation attached
    health_changed = Signal(object)  # HealthReport
    phase_changed = Signal(object)  # PipelinePhase
    status_message = Signal(str)
    error_occurred = Signal(str)
    device_opened = Signal(str)
    stopped = Signal()

    def __init__(
        self,
        engine_provider,
        translation_provider=None,
        capture_factory=create_capture,
        parent: QObject | None = None,
    ):
        """
        ``engine_provider(config) -> TranscriptionEngine`` is called on the worker
        thread; it may block to load or download. ``translation_provider(config)``
        returns a chain or None. Injecting both keeps this class testable without
        models or audio hardware.
        """
        super().__init__(parent)
        self._engine_provider = engine_provider
        self._translation_provider = translation_provider
        self._capture_factory = capture_factory

        self._phase = PipelinePhase.IDLE
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

        self._capture: AudioCapture | None = None
        self._ring = RingBuffer.for_seconds(_RING_SECONDS, _MAX_INPUT_RATE)
        self._input_rate = _MAX_INPUT_RATE
        self._asr_queue: queue.Queue[AudioWork] = queue.Queue(_MAX_PENDING_SEGMENTS)
        self._translate_queue: queue.Queue[CaptionSegment] = queue.Queue(2)
        self._health = HealthMonitor()
        self._context: deque[tuple[str, str]] = deque(maxlen=_CONTEXT_PAIRS)

        self._session_start = 0.0
        self._dropped_segments = 0
        self._last_lag = 0.0
        self._asr_seconds = self._translation_seconds = self._queue_wait = 0.0
        self._dropped_translations = 0

    @property
    def metrics(self) -> PipelineMetrics:
        return PipelineMetrics(
            self._asr_seconds,
            self._translation_seconds,
            self._queue_wait,
            self._last_lag,
            self._dropped_segments,
            self._dropped_translations,
            self._ring.dropped_frames,
            self._asr_queue.qsize(),
            self._translate_queue.qsize(),
        )

    # ---------- state ----------

    @property
    def phase(self) -> PipelinePhase:
        return self._phase

    @property
    def is_active(self) -> bool:
        return self._phase in (PipelinePhase.PREPARING, PipelinePhase.RUNNING)

    @property
    def has_workers(self) -> bool:
        """True until native inference and every session worker have returned."""
        return any(thread.is_alive() for thread in self._threads)

    @property
    def health(self) -> HealthReport:
        return self._health.report()

    # ---------- control ----------

    def start(self, config: SessionConfig, device: AudioDevice | None) -> bool:
        """Begin captioning. Returns False if a run is already in flight."""
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                return False
            if self._phase is not PipelinePhase.IDLE and self._phase is not PipelinePhase.FAILED:
                log.warning("Ignoring start while pipeline is %s", self._phase.value)
                return False
            self._stop_event.clear()
            self._ring.clear()
            self._asr_queue = queue.Queue(_MAX_PENDING_SEGMENTS)
            self._translate_queue = queue.Queue(2)
            self._context.clear()
            self._dropped_segments = 0
            self._last_lag = 0.0
            self._asr_seconds = self._translation_seconds = self._queue_wait = 0.0
            self._dropped_translations = 0
            self._set_phase(PipelinePhase.PREPARING)
            self._health.set_state(HealthState.STARTING, "Warming up…")
            self._emit_health()

            runner = threading.Thread(
                target=self._run, args=(config, device), name="subbyai-pipeline", daemon=True
            )
            self._threads = [runner]
            runner.start()
            return True

    def stop(self) -> None:
        """Ask the pipeline to stop. Returns immediately; ``stopped`` follows."""
        if self._phase in (PipelinePhase.IDLE, PipelinePhase.STOPPING):
            return
        self._set_phase(PipelinePhase.STOPPING)
        self._stop_event.set()
        # Workers wait for at most 200ms. No blocking put into a full queue.

    def wait(self, timeout: float = 10.0) -> bool:
        """Block until fully stopped. For shutdown paths only, never the UI."""
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            remaining = max(0.0, deadline - time.monotonic())
            if thread.ident is not None:
                thread.join(timeout=remaining)
        return all(not t.is_alive() for t in self._threads)

    # ---------- worker: orchestration ----------

    def _run(self, config: SessionConfig, device: AudioDevice | None) -> None:
        engine = None
        translator = None
        workers: list[threading.Thread] = []
        try:
            engine = self._engine_provider(config)
            if self._stop_event.is_set():
                return
            if self._translation_provider is not None and config.target_language:
                translator = self._translation_provider(config)

            segmenter = SpeechSegmenter(
                SegmenterConfig(
                    sensitivity=config.speech_sensitivity,
                    max_segment_seconds=config.chunk_seconds,
                    close_after_silence=config.silence_seconds,
                    fast_close_seconds=min(0.35, config.silence_seconds),
                    long_speech_seconds=min(4.0, config.chunk_seconds),
                )
            )
            caption_filter = HallucinationFilter() if config.filter_hallucinations else None

            self._capture = self._capture_factory()
            self._session_start = time.monotonic()
            opened = self._capture.start(device, self._on_audio_block)
            self.device_opened.emit(opened.name)
            self._health.set_device(opened.name)
            self._health.set_state(HealthState.LISTENING)
            self._set_phase(PipelinePhase.RUNNING)
            self._emit_health()

            workers = [
                threading.Thread(
                    target=self._guard_segment_loop,
                    args=(segmenter,),
                    name="subbyai-segment",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._recognize_loop,
                    args=(engine, config, caption_filter),
                    name="subbyai-asr",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._translate_loop,
                    args=(translator, config),
                    name="subbyai-translate",
                    daemon=True,
                ),
                threading.Thread(target=self._health_loop, name="subbyai-health", daemon=True),
            ]
            self._threads.extend(workers)
            for worker in workers:
                worker.start()

            while not self._stop_event.wait(0.2):
                pass

        except AudioCaptureError as exc:
            # .message is the plain-language half; str(exc) is the driver detail.
            self._fail(exc.message or str(exc))
        except Exception as exc:  # the worker reports, never vanishes
            log.exception("Pipeline failed")
            self._fail(_friendly_error(exc))
        finally:
            self._stop_event.set()
            if self._capture is not None:
                try:
                    self._capture.stop()
                except Exception:
                    log.warning("Error stopping capture", exc_info=True)
                self._capture = None
            for worker in workers:
                # A timeout must never declare a session stopped while a native
                # inference call still holds its model. This join is off the UI.
                if worker.ident is not None:
                    worker.join()
            if translator is not None:
                with contextlib.suppress(Exception):
                    translator.reset()
                with contextlib.suppress(Exception):
                    translator.close()
            if engine is not None and getattr(engine, "session_scoped", False):
                with contextlib.suppress(Exception):
                    engine.close()
            self._ring.clear()
            self._discard_queues()
            # The engine is intentionally NOT unloaded: the cache keeps it warm
            # so the next start does not pay the model load again.
            if self._phase is not PipelinePhase.FAILED:
                self._set_phase(PipelinePhase.IDLE)
                self._health.set_state(HealthState.OFF)
                self._emit_health()
            self.stopped.emit()

    # ---------- audio callback (must stay trivial) ----------

    def _on_audio_block(self, samples: np.ndarray, rate: int) -> None:
        if self._stop_event.is_set():
            return
        # Downmix here and nowhere else. The ring is a flat float32 array with no
        # frame structure, so interleaved stereo becomes unreadable the moment an
        # overflow drops a partial frame and shifts the channel phase. mean() is
        # one cheap pass; the resampling this callback must not do stays on the
        # segment worker.
        if samples.ndim == 2 and samples.shape[1] > 1:
            samples = samples.mean(axis=1)
        self._input_rate = rate
        self._ring.write(samples)

    # ---------- worker: segmentation ----------

    def _guard_segment_loop(self, segmenter: SpeechSegmenter) -> None:
        try:
            self._segment_loop(segmenter)
        except Exception:
            log.exception("Audio processing failed")
            self._fail("Audio processing stopped. Reconnect your output and try again.")

    def _discard_queues(self) -> None:
        for pending in (self._asr_queue, self._translate_queue):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break

    def _segment_loop(self, segmenter: SpeechSegmenter) -> None:
        last_level = 0.0
        dropped = 0
        timeline_offset = 0.0
        consumed = 0.0
        while not self._stop_event.is_set():
            block = self._ring.read(_READ_SAMPLES)
            if block.size == 0:
                # RingBuffer never blocks, so idling is this loop's job.
                time.sleep(_CONSUMER_BLOCK)
                continue
            rate = self._input_rate
            lost = self._ring.dropped_frames
            if lost != dropped:
                # Discontinuous audio cannot be joined into a false utterance.
                timeline_offset = consumed + (lost - dropped) / rate
                consumed = timeline_offset
                dropped = lost
                segmenter = SpeechSegmenter(segmenter.config)
            consumed += block.size / rate
            now = time.monotonic()
            level = float(np.sqrt(np.mean(np.square(block))))
            if now - last_level >= _LEVEL_INTERVAL:
                last_level = now
                self._health.note_audio(level)
            segments = segmenter.push(block, rate)
            for segment, (start, duration) in zip(segments, segmenter.completed_times, strict=True):
                self._health.note_speech()
                self._enqueue_segment(
                    AudioWork(segment, timeline_offset + start, duration, time.monotonic())
                )

    def _enqueue_segment(self, segment: AudioWork) -> None:
        while self._asr_queue.qsize() >= _MAX_PENDING_SEGMENTS:
            try:
                self._asr_queue.get_nowait()
            except queue.Empty:
                break
            self._dropped_segments += 1
            if self._dropped_segments % 5 == 1:
                log.warning(
                    "Recognition is slower than real time; dropped %d segment(s)",
                    self._dropped_segments,
                )
                self.status_message.emit(
                    "Captions are falling behind. Try a faster quality setting."
                )
        if not self._stop_event.is_set():
            self._asr_queue.put_nowait(segment)

    # ---------- worker: recognition ----------

    def _recognize_loop(self, engine, config: SessionConfig, caption_filter) -> None:
        reconciler = Reconciler()
        failures = 0
        while not self._stop_event.is_set():
            try:
                work = self._asr_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            if self._stop_event.is_set():
                return

            queued_at = time.monotonic()
            self._queue_wait = queued_at - work.queued_at
            try:
                result = engine.transcribe(
                    work.audio,
                    language=config.source_language or None,
                    vocabulary=config.vocabulary,
                )
            except Exception as exc:  # one bad segment must not end the run
                log.warning("Could not recognize a segment (%s)", type(exc).__name__)
                failures += 1
                if failures >= 3:
                    self._fail(
                        "The speech engine failed repeatedly. "
                        "Try a lighter model or check your server."
                    )
                    return
                continue
            failures = 0
            self._asr_seconds = time.monotonic() - queued_at
            self._last_lag = max(
                0.0, time.monotonic() - (self._session_start + work.start + work.duration)
            )
            self._health.note_lag(self._last_lag)

            text = (result.text or "").strip()
            if caption_filter is not None:
                text = caption_filter.clean(text)
                if caption_filter.should_drop(text, result.speech_probability, result.confidence):
                    log.debug("Dropped a likely hallucination")
                    continue
            text = reconciler.accept(text, work.start, work.duration)
            if not text or self._stop_event.is_set():
                continue

            segment = CaptionSegment(
                text=text,
                language=result.language,
                language_confidence=result.language_confidence,
                confidence=result.confidence,
                confidence_known=result.confidence_known,
                speech_probability=result.speech_probability,
                audio_start=work.start,
                audio_duration=work.duration,
                translation_state=(
                    TranslationState.PENDING if config.target_language else TranslationState.NONE
                ),
            )
            # Show the original immediately — this is the contract.
            self.segment_ready.emit(segment)
            if config.target_language:
                self._queue_translation(segment)

    def _queue_translation(self, segment: CaptionSegment) -> None:
        # Only the newest segment matters; a backlog of translations would show
        # captions for speech the user has already moved past.
        while self._translate_queue.qsize() >= 2:
            try:
                stale = self._translate_queue.get_nowait()
            except queue.Empty:
                break
            if stale is not None:
                self._dropped_translations += 1
                self.segment_updated.emit(stale.with_translation_state(TranslationState.FAILED))
        self._translate_queue.put_nowait(segment)

    # ---------- worker: translation ----------

    def _translate_loop(self, translator, config: SessionConfig) -> None:
        while not self._stop_event.is_set():
            try:
                segment = self._translate_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            if self._stop_event.is_set():
                return
            if translator is None:
                self.segment_updated.emit(segment.with_translation_state(TranslationState.FAILED))
                continue

            source = segment.language or config.source_language or "auto"
            started = time.monotonic()
            try:
                result = translator.translate(
                    segment.text, source, config.target_language, list(self._context)
                )
            except Exception as exc:  # captions survive translator failure
                if self._stop_event.is_set():
                    return
                log.info("Translation unavailable (%s)", type(exc).__name__)
                self.segment_updated.emit(segment.with_translation_state(TranslationState.FAILED))
                self.status_message.emit(
                    "Translation paused — captions continue in the original language."
                )
                continue
            self._translation_seconds = time.monotonic() - started
            if self._stop_event.is_set():
                return
            self._context.append((segment.text, result.text))
            self.segment_updated.emit(
                segment.with_translation(
                    result.text, config.target_language, result.provider_label, result.tier
                )
            )

    # ---------- worker: health ----------

    def _health_loop(self) -> None:
        last: HealthReport | None = None
        while not self._stop_event.wait(_HEALTH_INTERVAL):
            report = self._health.report()
            if last is None or report.state is not last.state or _level_moved(last, report):
                last = report
                self.health_changed.emit(report)

    # ---------- helpers ----------

    def _emit_health(self) -> None:
        self.health_changed.emit(self._health.report())

    def _set_phase(self, phase: PipelinePhase) -> None:
        self._phase = phase
        self.phase_changed.emit(phase)

    def _fail(self, message: str) -> None:
        self._stop_event.set()
        self._set_phase(PipelinePhase.FAILED)
        self._health.set_state(HealthState.ERROR, message)
        self._emit_health()
        self.error_occurred.emit(message)


def _level_moved(previous: HealthReport, current: HealthReport) -> bool:
    return abs(previous.level - current.level) > 0.01


def _friendly_error(exc: Exception) -> str:
    """Plain-language error text. Users never see exception plumbing."""
    friendly = getattr(exc, "friendly", None)
    if friendly:
        return str(friendly)
    from ..asr.engine import EngineError

    return (
        str(exc)
        if isinstance(exc, EngineError)
        else "Captioning stopped unexpectedly. Try again or open Diagnostics."
    )
