"""The captioning pipeline: capture → segment → recognize → translate → emit.

Stage separation is the point. Four threads, each with one job:

    audio callback  ─write→  CaptureBuffer  ─read→  segment worker
                                                     │ speech segments
                                              recognition worker
                                                     │ CaptionSegment ── shown NOW
                                              translation worker
                                                     │ translation ── attached later

Two contracts follow from that shape and matter more than any other detail:

1. **Captions never wait for a translator.** The original is emitted the moment
   recognition finishes; the translation arrives as an update. If translation
   fails or the provider is down, the caption still stands.
2. **Nothing heavy runs on the audio callback.** It copies native frames into a
   bounded buffer. Downmix, resampling and segmentation run on the consumer.

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
from dataclasses import asdict, dataclass, replace
from enum import StrEnum

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..audio.base import AudioCapture, AudioCaptureError, AudioDevice, create_capture
from ..audio.buffer import CaptureBuffer
from ..audio.segmenter import (
    CLOSE_FLUSH,
    CLOSE_REVISION,
    SegmenterConfig,
    SpeechSegmenter,
)
from ..audio.vad import SileroSpeechDetector
from ..core.events import CaptionSegment, RecognitionState, TranslationState
from ..core.health import HealthMonitor, HealthReport, HealthState
from ..core.pipeline_trace import PipelineTrace
from ..core.settings import SessionConfig
from .context import RecognitionContext
from .hallucinations import HallucinationFilter
from .reconciliation import Reconciler
from .sentences import can_join, join_sources, sentence_finished

log = logging.getLogger(__name__)

#: Recognition backlog. Beyond this the oldest audio is dropped so captions stay
#: live rather than drifting minutes behind the sound.
_MAX_PENDING_SEGMENTS = 3
_RING_SECONDS = 30.0
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
    epoch: int = 0
    final: bool = True
    revision: bool = False
    reason: str = "silence"


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
        speech_detector_factory=SileroSpeechDetector,
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
        self._speech_detector_factory = speech_detector_factory
        self._trace: PipelineTrace | None = None
        self.stopped.connect(self._finish_trace)

        self._phase = PipelinePhase.IDLE
        self._stop_event = threading.Event()
        self._capture_stop = threading.Event()
        self._segmentation_done = threading.Event()
        self._recognition_done = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

        self._capture: AudioCapture | None = None
        self._capture_gap = False
        self._capture_gap_seconds = 0.0
        self._ring = CaptureBuffer(_RING_SECONDS)
        self._audio_started = False
        self._asr_queue: queue.Queue[AudioWork] = queue.Queue(_MAX_PENDING_SEGMENTS)
        self._translate_queue: queue.Queue[CaptionSegment] = queue.Queue(8)
        self._health = HealthMonitor()
        self._context: deque[tuple[str, str]] = deque(maxlen=_CONTEXT_PAIRS)

        self._session_start = 0.0
        self._dropped_segments = 0
        self._last_lag = 0.0
        self._asr_seconds = self._translation_seconds = self._queue_wait = 0.0
        self._dropped_translations = 0
        self._dropped_audio_samples = 0

    @property
    def metrics(self) -> PipelineMetrics:
        return PipelineMetrics(
            self._asr_seconds,
            self._translation_seconds,
            self._queue_wait,
            self._last_lag,
            self._dropped_segments,
            self._dropped_translations,
            self._dropped_audio_samples + self._ring.dropped_frames,
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
            self._capture_stop.clear()
            self._segmentation_done.clear()
            self._recognition_done.clear()
            self._ring.clear()
            self._capture_gap = False
            self._capture_gap_seconds = 0.0
            self._audio_started = False
            self._asr_queue = queue.Queue(_MAX_PENDING_SEGMENTS)
            self._translate_queue = queue.Queue(8)
            self._context.clear()
            self._dropped_segments = 0
            self._last_lag = 0.0
            self._asr_seconds = self._translation_seconds = self._queue_wait = 0.0
            self._dropped_translations = 0
            self._dropped_audio_samples = 0
            self._set_phase(PipelinePhase.PREPARING)
            self._health.set_state(HealthState.STARTING, "Warming up…")
            self._emit_health()

            runner = threading.Thread(
                target=self._run, args=(config, device), name="subbyai-pipeline", daemon=True
            )
            self._threads = [runner]
            runner.start()
            return True

    def stop(self, *, drain: bool = True) -> None:
        """Stop capture and drain by default; drain=False explicitly cancels work.

        Returns immediately; ``stopped`` follows after workers release models.
        """
        if self._phase in (PipelinePhase.IDLE, PipelinePhase.STOPPING):
            return
        self._set_phase(PipelinePhase.STOPPING)
        self._capture_stop.set()
        if not drain:
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
            if self._stop_event.is_set() or self._capture_stop.is_set():
                return
            if self._translation_provider is not None and config.target_language:
                translator = self._translation_provider(config)

            try:
                if self._trace is not None:
                    self._trace.close()
                self._trace = PipelineTrace.from_environment()
            except OSError:
                log.warning("Could not open developer trace directory")
                self._trace = None
            detector = self._new_detector(config.speech_sensitivity)

            segmenter = SpeechSegmenter(
                SegmenterConfig(
                    sensitivity=config.speech_sensitivity,
                    max_segment_seconds=config.chunk_seconds,
                    close_after_silence=config.silence_seconds,
                    fast_close_seconds=min(0.35, config.silence_seconds),
                    long_speech_seconds=min(4.0, config.chunk_seconds),
                ),
                detector=detector,
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
                    target=self._guard_recognize_loop,
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

            while not self._capture_stop.wait(0.2) and not self._stop_event.is_set():
                pass

        except AudioCaptureError as exc:
            # .message is the plain-language half; str(exc) is the driver detail.
            self._fail(exc.message or str(exc))
        except Exception as exc:  # the worker reports, never vanishes
            log.exception("Pipeline failed")
            self._fail(_friendly_error(exc))
        finally:
            self._capture_stop.set()
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
            self._stop_event.set()
            if translator is not None:
                with contextlib.suppress(Exception):
                    translator.reset()
                with contextlib.suppress(Exception):
                    translator.close()
            if engine is not None and getattr(engine, "session_scoped", False):
                with contextlib.suppress(Exception):
                    engine.close()
            self._dropped_audio_samples = self._ring.dropped_frames
            self._ring.clear()
            self._discard_queues()
            self._context.clear()
            # The engine is intentionally NOT unloaded: the cache keeps it warm
            # so the next start does not pay the model load again.
            if self._phase is not PipelinePhase.FAILED:
                self._set_phase(PipelinePhase.IDLE)
                self._health.set_state(HealthState.OFF)
                self._emit_health()
            self.stopped.emit()

    # ---------- audio callback (must stay trivial) ----------

    def _on_audio_block(
        self, samples: np.ndarray, rate: int, *, discontinuity: bool = False
    ) -> None:
        if self._stop_event.is_set() or self._capture_stop.is_set():
            return
        if not self._audio_started:
            self._session_start = time.monotonic() - len(samples) / rate
            self._audio_started = True
        block = self._ring.write(
            samples,
            rate,
            discontinuity=discontinuity or self._capture_gap,
            gap_seconds=self._capture_gap_seconds,
        )
        self._capture_gap = False
        self._capture_gap_seconds = 0.0
        self._record(
            "capture", audio=block.samples, rate=rate, start=block.start, epoch=block.epoch
        )

    def note_capture_gap(self, seconds=0.0):
        self._capture_gap = True
        self._capture_gap_seconds += max(0.0, seconds)

    # ---------- worker: segmentation ----------

    def _guard_segment_loop(self, segmenter: SpeechSegmenter) -> None:
        try:
            self._segment_loop(segmenter)
        except Exception:
            log.exception("Audio processing failed")
            self._fail("Audio processing stopped. Reconnect your output and try again.")
        finally:
            self._segmentation_done.set()

    def _new_detector(self, sensitivity):
        if self._speech_detector_factory is None:
            return None
        try:
            return self._speech_detector_factory(sensitivity)
        except Exception as exc:
            log.warning(
                "Speech detector unavailable; using energy fallback (%s)", type(exc).__name__
            )
            self._record("vad_fallback")
            return None

    def _record(self, stage, **kwargs):
        trace = self._trace
        if trace is not None:
            trace.record(stage, **kwargs)

    def _finish_trace(self):
        # Final caption signals are queued to the GUI before stopped. Give their
        # paint events a turn before closing, so stage 8 includes the last subtitle.
        trace = self._trace
        if trace is None:
            return

        def close_finished():
            if self._trace is trace:
                self._trace = None
            threading.Thread(target=trace.close, name="subbyai-trace-close", daemon=True).start()

        QTimer.singleShot(100, close_finished)

    def record_rendered(self, segment, *, lines=None, page=0, visible=True):
        self._record(
            "subtitle_rendered",
            caption_id=segment.id,
            revision=segment.revision,
            page=page,
            visible=visible,
            content={**asdict(segment), "painted_lines": lines},
        )

    def _discard_queues(self) -> None:
        for pending in (self._asr_queue, self._translate_queue):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break

    def _segment_loop(self, segmenter: SpeechSegmenter) -> None:
        last_level = 0.0
        timeline_offset = None
        expected_start = 0.0
        epoch = None
        generation = 0

        def enqueue_finished():
            for audio, (start, duration), reason, final in zip(
                segments,
                segmenter.completed_times,
                segmenter.completed_reasons,
                segmenter.completed_final,
                strict=True,
            ):
                self._health.note_speech()
                self._enqueue_segment(
                    AudioWork(
                        audio,
                        timeline_offset + start,
                        duration,
                        time.monotonic(),
                        generation,
                        final,
                        reason == CLOSE_REVISION,
                        reason,
                    )
                )

        def flush_open():
            tail = segmenter.flush()
            self._record(
                "preprocessed",
                audio=segmenter.last_processed,
                epoch=generation,
                start=timeline_offset
                + (segmenter._resampler.output_samples - len(segmenter.last_processed)) / 16000,
            )
            repair = segmenter.flushed_revision
            if repair:
                audio, start, duration = repair
                self._enqueue_segment(
                    AudioWork(
                        audio,
                        timeline_offset + start,
                        duration,
                        time.monotonic(),
                        generation,
                        True,
                        True,
                        CLOSE_REVISION,
                    )
                )
            for audio, (start, duration), reason, final in segmenter.flushed_following:
                self._enqueue_segment(
                    AudioWork(
                        audio,
                        timeline_offset + start,
                        duration,
                        time.monotonic(),
                        generation,
                        final=final,
                        reason=reason,
                    )
                )
            if tail is not None:
                start, duration = segmenter._emitted_time
                self._enqueue_segment(
                    AudioWork(
                        tail,
                        timeline_offset + start,
                        duration,
                        time.monotonic(),
                        generation,
                        reason=CLOSE_FLUSH,
                    )
                )

        while not self._stop_event.is_set():
            captured = self._ring.read()
            if captured is None:
                if self._capture_stop.is_set():
                    break
                # RingBuffer never blocks, so idling is this loop's job.
                time.sleep(_CONSUMER_BLOCK)
                continue
            block, rate = captured.samples, captured.rate
            if timeline_offset is None:
                timeline_offset = captured.start
            elif captured.epoch != epoch or abs(captured.start - expected_start) > 1 / rate:
                flush_open()
                generation += 1
                self._record(
                    "audio_discontinuity",
                    start=captured.start,
                    gap_seconds=max(0, captured.start - expected_start),
                    epoch=generation,
                )
                timeline_offset = captured.start
                segmenter = SpeechSegmenter(
                    segmenter.config, detector=self._new_detector(segmenter.config.sensitivity)
                )
            epoch = captured.epoch
            expected_start = captured.start + len(block) / rate
            now = time.monotonic()
            level = float(np.sqrt(np.mean(np.square(block))))
            if now - last_level >= _LEVEL_INTERVAL:
                last_level = now
                self._health.note_audio(level)
            segments = segmenter.push(block, rate)
            self._record(
                "preprocessed",
                audio=segmenter.last_processed,
                epoch=generation,
                start=timeline_offset
                + (segmenter._resampler.output_samples - len(segmenter.last_processed)) / 16000,
            )
            enqueue_finished()
        if not self._stop_event.is_set() and timeline_offset is not None:
            flush_open()

    def _enqueue_segment(self, segment: AudioWork) -> None:
        while self._asr_queue.qsize() >= _MAX_PENDING_SEGMENTS:
            try:
                dropped = self._asr_queue.get_nowait()
            except queue.Empty:
                break
            self._dropped_segments += 1
            self._record(
                "recognition_queue_drop",
                start=dropped.start,
                duration=dropped.duration,
                epoch=dropped.epoch,
                final=dropped.final,
                revision=dropped.revision,
            )
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

    def _guard_recognize_loop(self, engine, config, caption_filter):
        try:
            self._recognize_loop(engine, config, caption_filter)
        except Exception as exc:
            log.warning("Recognition processing failed (%s)", type(exc).__name__)
            self._fail("Speech processing stopped. Restart captions and try again.")
        finally:
            self._recognition_done.set()

    def _recognize_loop(self, engine, config: SessionConfig, caption_filter) -> None:
        reconciler = Reconciler()
        context = RecognitionContext(config.source_language)
        tentative: dict[tuple[int, int], CaptionSegment] = {}
        last_epoch = None
        last_end = -100.0
        continuity = 0
        last_drop_count = 0

        def abandon(segment):
            if not self._stop_event.is_set():
                self.segment_updated.emit(
                    replace(
                        segment,
                        recognition_state=RecognitionState.FAILED,
                        revision=segment.revision + 1,
                        translation_state=TranslationState.FAILED
                        if config.target_language
                        else TranslationState.NONE,
                    )
                )
                self._record("unresolved_revision", caption_id=segment.id)

        failures = 0
        while not self._stop_event.is_set():
            try:
                work = self._asr_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set() or self._segmentation_done.is_set():
                    for unresolved in tentative.values():
                        abandon(unresolved)
                    return
                continue
            if self._stop_event.is_set():
                return

            queued_at = time.monotonic()
            self._queue_wait = queued_at - work.queued_at
            key = (work.epoch, round(work.start * 16000))

            def reject_revision(key=key, final=work.final):
                previous = tentative.pop(key, None) if final else None
                if previous is not None:
                    abandon(previous)

            try:
                if (
                    work.epoch != last_epoch
                    or work.start - last_end > 8
                    or self._dropped_segments != last_drop_count
                ):
                    for unresolved in tentative.values():
                        abandon(unresolved)
                    tentative.clear()
                    reconciler = Reconciler()
                    context.reset()
                    continuity += 1
                    last_drop_count = self._dropped_segments
                language = context.prepare(engine, work)
                last_epoch, last_end = work.epoch, max(last_end, work.start + work.duration)
                kwargs = {"language": language, "vocabulary": config.vocabulary}
                if getattr(engine, "supports_context", False):
                    kwargs["context"] = context.text
                self._record(
                    "asr_input",
                    audio=work.audio,
                    start=work.start,
                    duration=work.duration,
                    epoch=work.epoch,
                    final=work.final,
                    revision=work.revision,
                    content={
                        "language": language,
                        "context": context.text,
                        "vocabulary": config.vocabulary,
                        "beam": config.beam_size,
                    },
                )
                result = engine.transcribe(
                    work.audio,
                    **kwargs,
                )
                self._record(
                    "asr_output",
                    start=work.start,
                    epoch=work.epoch,
                    content={
                        **asdict(result),
                        "inference_options": getattr(engine, "last_options", {}),
                    },
                )
            except Exception as exc:  # one bad segment must not end the run
                log.warning("Could not recognize a segment (%s)", type(exc).__name__)
                reject_revision()
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
            speech_end = max((w.end for w in getattr(result, "words", ())), default=work.duration)
            self._last_lag = max(
                0.0,
                time.monotonic()
                - (self._session_start + work.start + min(work.duration, speech_end)),
            )
            self._health.note_lag(self._last_lag)

            text = (result.text or "").strip()
            if caption_filter is not None:
                text = caption_filter.clean(text)
                if caption_filter.should_drop(text, result.speech_probability, result.confidence):
                    log.debug("Dropped a likely hallucination")
                    reject_revision()
                    continue
            words = getattr(result, "words", ())
            text = (reconciler.accept if work.final else reconciler.preview)(
                text, work.start, work.duration, words
            )
            if not text or self._stop_event.is_set():
                reject_revision()
                continue

            previous = tentative.pop(key, None) if work.final else None
            segment = CaptionSegment(
                text=text,
                language=result.language or language,
                language_confidence=result.language_confidence or context.language_confidence,
                confidence=result.confidence,
                confidence_known=result.confidence_known,
                speech_probability=result.speech_probability,
                audio_start=work.start,
                audio_duration=work.duration,
                translation_state=(
                    TranslationState.PENDING if config.target_language else TranslationState.NONE
                ),
                recognition_state=RecognitionState.FINAL
                if work.final
                else RecognitionState.PROVISIONAL,
                revision=previous.revision + 1 if previous else 0,
                context_epoch=continuity,
            )
            if previous is not None:
                segment = replace(segment, id=previous.id)
            if not work.final:
                tentative[key] = segment
                # Bounded even when a correction was evicted by overload.
                if len(tentative) > _MAX_PENDING_SEGMENTS:
                    abandon(tentative.pop(next(iter(tentative))))
            else:
                context.remember(text, result)
            self._record(
                "stitched_transcript",
                caption_id=segment.id,
                final=work.final,
                content=asdict(segment),
            )
            # Show the original immediately — this is the contract.
            (self.segment_updated if previous is not None else self.segment_ready).emit(segment)
            if config.target_language and work.final:
                self._queue_translation(segment)

    def _queue_translation(self, segment: CaptionSegment) -> None:
        # Only the newest segment matters; a backlog of translations would show
        # captions for speech the user has already moved past.
        while self._translate_queue.full():
            try:
                stale = self._translate_queue.get_nowait()
            except queue.Empty:
                break
            if stale is not None:
                self._dropped_translations += 1
                self._record("translation_queue_drop", caption_id=stale.id)
                self.segment_updated.emit(stale.with_translation_state(TranslationState.FAILED))
        self._translate_queue.put_nowait(segment)

    # ---------- worker: translation ----------

    def _translate_loop(self, translator, config: SessionConfig) -> None:
        pending = None
        previous_end = -100.0
        previous_source = None
        previous_epoch = None
        continuation = []
        while not self._stop_event.is_set():
            try:
                segment = pending or self._translate_queue.get(timeout=0.2)
                pending = None
            except queue.Empty:
                if self._stop_event.is_set() or self._recognition_done.is_set():
                    return
                continue
            if self._stop_event.is_set():
                return
            group = [segment]
            if (
                continuation
                and can_join(continuation[-1], segment)
                and len(continuation) < 3
                and len(join_sources(continuation)) < 400
            ):
                group = [*continuation, segment]
                # Replace an isolated fragment's translation when its next
                # clause arrives, without replaying the partial as context.
                if self._context and self._context[-1][0] == join_sources(
                    continuation, config.vocabulary
                ):
                    self._context.pop()
            continuation = []
            # Give a fragment a short opportunity to acquire its next clause.
            # A punctuation-complete sentence adds no accumulation delay.
            deadline = time.monotonic() + 0.45
            while (
                not sentence_finished(group[-1].text, group[-1].language or config.source_language)
                and len(group) < 3
                and len(join_sources(group)) < 400
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop_event.is_set():
                    break
                if self._recognition_done.is_set() and self._translate_queue.empty():
                    break
                try:
                    candidate = self._translate_queue.get(timeout=min(remaining, 0.1))
                except queue.Empty:
                    continue
                if not can_join(group[-1], candidate):
                    pending = candidate
                    break
                group.append(candidate)
            source_text = join_sources(group, config.vocabulary)
            segment = group[-1]
            if translator is None:
                for member in group:
                    self.segment_updated.emit(
                        member.with_translation_state(TranslationState.FAILED)
                    )
                continue

            source = segment.language or config.source_language or "auto"
            if (
                source != previous_source
                or group[0].audio_start - previous_end > 8
                or segment.context_epoch != previous_epoch
            ):
                self._context.clear()
            previous_source = source
            previous_epoch = segment.context_epoch
            previous_end = segment.audio_start + segment.audio_duration
            started = time.monotonic()
            self._record(
                "translation_input",
                caption_id=segment.id,
                group_size=len(group),
                content={
                    "text": source_text,
                    "source": source,
                    "target": config.target_language,
                    "context": list(self._context),
                },
            )
            try:
                result = translator.translate(
                    source_text, source, config.target_language, list(self._context)
                )
                self._record("translation_output", caption_id=segment.id, content=asdict(result))
            except Exception as exc:  # captions survive translator failure
                if self._stop_event.is_set():
                    return
                log.info("Translation unavailable (%s)", type(exc).__name__)
                for member in group:
                    self.segment_updated.emit(
                        member.with_translation_state(TranslationState.FAILED)
                    )
                self.status_message.emit(
                    "Translation paused — captions continue in the original language."
                )
                continue
            self._translation_seconds = time.monotonic() - started
            if self._stop_event.is_set():
                return
            self._context.append((source_text, result.text))
            if not sentence_finished(source_text, source) and len(group) < 3:
                continuation = group
            # The whole translation appears once, attached to the final source
            # caption. Earlier source captions remain intact and explicitly linked.
            for member in group[:-1]:
                self.segment_updated.emit(
                    replace(
                        member,
                        translation_state=TranslationState.GROUPED,
                        translation_group_id=segment.id,
                    )
                )
            self.segment_updated.emit(
                segment.with_translation(
                    result.text, config.target_language, result.provider_label, result.tier
                )
            )

    # ---------- worker: health ----------

    def _health_loop(self) -> None:
        last: HealthReport | None = None
        while not self._capture_stop.wait(_HEALTH_INTERVAL):
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
        self._capture_stop.set()
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
