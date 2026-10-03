"""Pipeline tests with fake capture, engine and translator — no hardware, no models.

These assert the two contracts the whole design rests on: the original caption
is emitted without waiting for translation, and stopping is asynchronous and
cannot leave two pipelines running.
"""

import threading
import time
from typing import ClassVar

import numpy as np
import pytest

from subbyai.asr.engine import Transcript
from subbyai.audio.base import AudioCapture, AudioDevice
from subbyai.core.events import PrivacyTier, TranslationState
from subbyai.core.health import HealthState
from subbyai.core.settings import SessionConfig, Settings
from subbyai.pipeline.captioner import Captioner, PipelinePhase
from subbyai.translation.base import TranslationError, TranslationResult

RATE = 16000
TONE = (0.3 * np.sin(2 * np.pi * 440 * np.arange(RATE) / RATE)).astype(np.float32)
SILENCE = np.zeros(RATE, dtype=np.float32)


class FakeCapture(AudioCapture):
    script: ClassVar[list[np.ndarray]] = []
    delay: ClassVar[float] = 0.0

    @staticmethod
    def list_devices():
        return [AudioDevice(id="0", name="Fake Speakers", sample_rate=RATE, channels=1,
                            is_loopback=True, is_default=True)]

    def start(self, device, callback):
        self._stop = threading.Event()

        def feed():
            for block in self.script:
                if self._stop.is_set():
                    return
                callback(block.reshape(-1, 1), RATE)
                if self.delay:
                    time.sleep(self.delay)

        threading.Thread(target=feed, daemon=True).start()
        return self.list_devices()[0]

    def stop(self):
        if hasattr(self, "_stop"):
            self._stop.set()


class FakeEngine:
    texts: ClassVar[list[str]] = []
    speech_probability: ClassVar[float] = 1.0
    confidence: ClassVar[float] = 0.9
    delay: ClassVar[float] = 0.0

    def __init__(self):
        self.calls = 0
        self.device = "cpu"
        self.loads = 0

    def load(self):
        self.loads += 1

    def transcribe(self, audio, language=None, vocabulary=""):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        text = self.texts[self.calls - 1] if self.calls <= len(self.texts) else f"line {self.calls}"
        return Transcript(
            text=text,
            language="ja",
            language_confidence=0.99,
            confidence=self.confidence,
            speech_probability=self.speech_probability,
            duration=float(len(audio)) / RATE,
        )

    def unload(self):
        pass


class FakeTranslator:
    def __init__(self, texts=None, fail=False, delay=0.0):
        self.texts = texts or {}
        self.fail = fail
        self.delay = delay
        self.contexts = []

    def translate(self, text, source, target, context=None):
        self.contexts.append(list(context or []))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise TranslationError("provider offline")
        return TranslationResult(
            text=self.texts.get(text, f"[{target}] {text}"),
            provider_id="fake",
            provider_label="Fake",
            tier=PrivacyTier.ON_DEVICE,
        )

    def reset(self):
        pass


def _config(**overrides) -> SessionConfig:
    settings = Settings()
    settings.captions.source_language = overrides.pop("source_language", "ja")
    settings.captions.target_language = overrides.pop("target_language", "")
    settings.captions.filter_hallucinations = overrides.pop("filter_hallucinations", False)
    for key, value in overrides.items():
        setattr(settings.captions, key, value)
    return SessionConfig.from_settings(settings)


def _run(qt_app, captioner, config, timeout=15.0, until=None):
    captioner.start(config, None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if until and until():
            break
        if captioner.phase is PipelinePhase.FAILED:
            break
        time.sleep(0.01)
    captioner.stop()
    deadline = time.monotonic() + 10
    while captioner.phase is not PipelinePhase.IDLE and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()


@pytest.fixture(autouse=True)
def _reset_fakes():
    FakeCapture.script = [TONE, SILENCE, TONE, SILENCE]
    FakeCapture.delay = 0.0
    FakeEngine.texts = []
    FakeEngine.delay = 0.0
    FakeEngine.speech_probability = 1.0
    FakeEngine.confidence = 0.9
    yield


def _captioner(engine=None, translator=None):
    engine = engine or FakeEngine()
    return Captioner(
        engine_provider=lambda config: engine,
        translation_provider=(lambda config: translator) if translator else None,
        capture_factory=FakeCapture,
    )


def test_original_caption_is_emitted_without_translation(qt_app):
    """The core contract: captions never wait for a translator."""
    FakeEngine.texts = ["こんにちは"]
    translator = FakeTranslator(delay=0.5)  # deliberately slow
    captioner = _captioner(translator=translator)

    ready, updated = [], []
    captioner.segment_ready.connect(ready.append)
    captioner.segment_updated.connect(updated.append)

    _run(qt_app, captioner, _config(target_language="en"), until=lambda: bool(ready))

    assert ready, "the original caption must be emitted"
    assert ready[0].text == "こんにちは"
    assert ready[0].translation_state is TranslationState.PENDING
    assert ready[0].display_translation is None  # nothing shown until it arrives


def test_translation_attaches_as_an_update(qt_app):
    FakeEngine.texts = ["こんにちは"]
    translator = FakeTranslator({"こんにちは": "Hello"})
    captioner = _captioner(translator=translator)

    ready, updated = [], []
    captioner.segment_ready.connect(ready.append)
    captioner.segment_updated.connect(updated.append)

    _run(qt_app, captioner, _config(target_language="en"), until=lambda: bool(updated))

    assert updated, "the translation must arrive as an update"
    assert updated[0].text == "こんにちは"  # original preserved
    assert updated[0].display_translation == "Hello"
    assert updated[0].translation_tier is PrivacyTier.ON_DEVICE
    assert updated[0].id == ready[0].id  # same segment, updated


def test_failed_translation_keeps_the_caption(qt_app):
    FakeEngine.texts = ["こんにちは"]
    captioner = _captioner(translator=FakeTranslator(fail=True))

    ready, updated, messages = [], [], []
    captioner.segment_ready.connect(ready.append)
    captioner.segment_updated.connect(updated.append)
    captioner.status_message.connect(messages.append)

    _run(qt_app, captioner, _config(target_language="en"), until=lambda: bool(updated))

    assert ready[0].text == "こんにちは"
    assert updated[0].translation_state is TranslationState.FAILED
    assert updated[0].display_translation is None
    assert any("original language" in m for m in messages)


def test_transcription_only_when_no_target_language(qt_app):
    FakeEngine.texts = ["plain caption"]
    captioner = _captioner()
    ready = []
    captioner.segment_ready.connect(ready.append)

    _run(qt_app, captioner, _config(target_language=""), until=lambda: bool(ready))

    assert ready[0].text == "plain caption"
    assert ready[0].translation_state is TranslationState.NONE


def test_translation_context_is_passed_and_grows(qt_app):
    # Two pending translations fit the bounded queue regardless of scheduling.
    FakeCapture.script = [TONE, SILENCE, TONE, SILENCE]
    FakeEngine.texts = ["one", "two"]
    translator = FakeTranslator()
    captioner = _captioner(translator=translator)

    updated = []
    captioner.segment_updated.connect(updated.append)
    _run(qt_app, captioner, _config(target_language="en"), until=lambda: len(updated) >= 2)

    assert translator.contexts[0] == []  # first line has no history
    assert len(translator.contexts) >= 2
    assert translator.contexts[1][0][0] == "one"  # previous source line carried forward


def test_stop_is_asynchronous(qt_app):
    """stop() must return promptly while native workers finish."""
    FakeEngine.delay = 0.4
    captioner = _captioner()
    captioner.start(_config(), None)
    deadline = time.monotonic() + 5
    while captioner.phase is not PipelinePhase.RUNNING and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)

    started = time.monotonic()
    captioner.stop()
    assert time.monotonic() - started < 0.1, "stop() must return immediately"
    assert captioner.phase is PipelinePhase.STOPPING

    stopped = []
    captioner.stopped.connect(lambda: stopped.append(True))
    deadline = time.monotonic() + 10
    while not stopped and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)
    assert stopped, "the stopped signal must fire"
    assert captioner.phase is PipelinePhase.IDLE


def test_second_start_is_refused_while_running(qt_app):
    """A finishing pipeline cannot overlap a new run."""
    captioner = _captioner()
    assert captioner.start(_config(), None) is True
    assert captioner.start(_config(), None) is False
    captioner.stop()
    captioner.wait(timeout=10)


def test_engine_is_not_unloaded_on_stop(qt_app):
    """Repeated starts reuse the loaded model."""
    engine = FakeEngine()
    engine.unload = lambda: pytest.fail("engine must stay loaded for the next run")
    captioner = _captioner(engine=engine)
    _run(qt_app, captioner, _config())


def test_backlog_drops_oldest_audio(qt_app):
    """Falling behind must stay live, not drift minutes behind."""
    FakeCapture.script = [TONE, SILENCE] * 8
    FakeEngine.delay = 0.25
    captioner = _captioner()

    messages = []
    captioner.status_message.connect(messages.append)
    _run(qt_app, captioner, _config(), timeout=8, until=lambda: bool(messages))

    assert any("falling behind" in m.lower() for m in messages)


def test_hallucination_gate_uses_speech_probability(qt_app):
    """A segment Whisper itself scores as non-speech is dropped."""
    FakeEngine.texts = ["Thanks for watching!"]
    FakeEngine.speech_probability = 0.1
    FakeEngine.confidence = 0.2
    captioner = _captioner()

    ready = []
    captioner.segment_ready.connect(ready.append)
    _run(qt_app, captioner, _config(filter_hallucinations=True), timeout=6)
    assert ready == []


def test_real_speech_is_never_edited_mid_sentence(qt_app):
    """Filtering must preserve words containing a filler substring."""
    FakeEngine.texts = ["goodbye, doctor — thanks for watching the experiment"]
    captioner = _captioner()

    ready = []
    captioner.segment_ready.connect(ready.append)
    _run(qt_app, captioner, _config(filter_hallucinations=True), until=lambda: bool(ready))

    assert ready, "legitimate speech must survive the filter"
    assert ready[0].text == "goodbye, doctor — thanks for watching the experiment"


def test_health_reports_listening_then_off(qt_app):
    captioner = _captioner()
    states = []
    captioner.health_changed.connect(lambda report: states.append(report.state))
    _run(qt_app, captioner, _config(), timeout=6)

    assert HealthState.LISTENING in states
    assert states[-1] is HealthState.OFF


def test_capture_failure_is_reported_in_plain_language(qt_app):
    class BrokenCapture(FakeCapture):
        def start(self, device, callback):
            from subbyai.audio.base import AudioCaptureError

            raise AudioCaptureError(
                "PortAudio error -9996 opening device 0",  # log detail
                "SubbyAI couldn't open Fake Speakers. Another app may be using it.",
            )

    captioner = Captioner(
        engine_provider=lambda config: FakeEngine(), capture_factory=BrokenCapture
    )
    errors = []
    captioner.error_occurred.connect(errors.append)

    captioner.start(_config(), None)
    deadline = time.monotonic() + 5
    while not errors and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)

    assert errors and "couldn't open Fake Speakers" in errors[0]
    assert "-9996" not in errors[0], "users must never see driver error codes"
    assert captioner.phase is PipelinePhase.FAILED


def test_engine_failure_is_reported(qt_app):
    from subbyai.asr.engine import EngineError

    def broken_provider(config):
        raise EngineError("SubbyAI couldn't load the caption engine.")

    captioner = Captioner(engine_provider=broken_provider, capture_factory=FakeCapture)
    errors = []
    captioner.error_occurred.connect(errors.append)
    captioner.start(_config(), None)

    deadline = time.monotonic() + 5
    while not errors and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)
    assert errors and "caption engine" in errors[0]
