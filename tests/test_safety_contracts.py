"""Regression coverage for privacy, streaming, and release boundaries."""

import io
import json
import logging
import threading
import time
import wave
import zipfile

import httpx
import numpy as np
import pytest

from subbyai.asr.engine import EngineError, Transcript
from subbyai.asr.remote import RemoteASR, pcm_wav
from subbyai.audio.base import AudioDevice, resolve_device
from subbyai.core.events import CaptionSegment, PrivacyTier
from subbyai.core.logging_setup import SafeFormatter
from subbyai.core.network import validate_endpoint
from subbyai.core.profiles import apply_profile
from subbyai.core.settings import (
    SCHEMA_VERSION,
    ProcessingSettings,
    SessionConfig,
    Settings,
    SettingsStore,
    from_dict,
)
from subbyai.pipeline.captioner import AudioWork, Captioner, PipelinePhase
from subbyai.pipeline.reconciliation import Reconciler
from subbyai.translation.builtin import validate_archive


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "file:///model",
        "https://example.com?token=secret",
        "https://example.com#fragment",
        "https://example.com:99999",
        "https://example.com\n",
        "http://localhost:0",
    ],
)
def test_unsafe_endpoints_are_rejected(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint, canonical",
    [
        ("https://example.com/v1/", "https://example.com"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://192.168.1.2:8000/v1", "http://192.168.1.2:8000"),
    ],
)
def test_endpoint_normalization(endpoint, canonical):
    assert validate_endpoint(endpoint) == canonical


def remote(handler, **overrides):
    cfg = ProcessingSettings(
        asr_backend="remote",
        base_url="https://example.com",
        consent_url="https://example.com",
        retries=0,
    )
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return RemoteASR(cfg, "test-placeholder", transport=httpx.MockTransport(handler))


def test_remote_audio_requires_consent_for_this_endpoint():
    with pytest.raises(EngineError, match="Allow audio"):
        remote(
            lambda request: pytest.fail("No request before consent"),
            consent_url="https://another.example.com",
        )


def test_remote_posts_memory_wav_and_uses_manual_language():
    requests = []

    def handle(request):
        requests.append(request)
        body = request.read()
        assert b"RIFF" in body and b"audio/wav" in body
        assert b"\r\nja\r\n" in body and b"vocabulary" in body
        assert request.headers["Authorization"] == "Bearer test-placeholder"
        return httpx.Response(200, json={"text": "こんにちは", "language": "Japanese"})

    engine = remote(handle)
    try:
        result = engine.transcribe(np.zeros(1600), "ja", "vocabulary")
        assert result.text == "こんにちは" and result.language == "ja"
        assert result.duration == 0.1 and not result.confidence_known
        assert len(requests) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("status", [301, 401, 403])
def test_remote_does_not_retry_auth_errors_or_follow_redirects(status):
    calls = []
    engine = remote(
        lambda request: (
            calls.append(request)
            or httpx.Response(status, headers={"Location": "https://another.example.com"})
        ),
        retries=2,
    )
    try:
        with pytest.raises(EngineError):
            engine.transcribe(np.zeros(1600))
        assert len(calls) == 1
    finally:
        engine.close()


def test_remote_bounded_retry(monkeypatch):
    sleeps = []
    monkeypatch.setattr("subbyai.asr.remote.time.sleep", sleeps.append)
    calls = []
    engine = remote(lambda request: calls.append(request) or httpx.Response(429), retries=2)
    try:
        with pytest.raises(EngineError):
            engine.transcribe(np.zeros(1600))
        assert len(calls) == 3 and sleeps == [0.25, 0.5]
    finally:
        engine.close()


@pytest.mark.parametrize(
    "body",
    [b"not JSON", b"[]", b'{"text":42}', b"x" * (1024**2 + 1)],
    ids=["malformed", "array", "wrong-type", "oversized"],
)
def test_remote_rejects_invalid_or_oversized_responses(body):
    engine = remote(lambda request: httpx.Response(200, content=body))
    try:
        with pytest.raises(EngineError):
            engine.transcribe(np.zeros(1600))
    finally:
        engine.close()


def test_wav_is_finite_pcm_and_never_needs_a_file():
    data = pcm_wav(np.array([np.nan, np.inf, -np.inf, 4.0]))
    with wave.open(io.BytesIO(data)) as stream:
        assert stream.getframerate() == 16000 and stream.getsampwidth() == 2
        assert np.frombuffer(stream.readframes(4), dtype="<i2").tolist() == [
            0,
            32767,
            -32767,
            32767,
        ]


def test_new_install_is_private_but_existing_history_is_preserved(tmp_path):
    path = tmp_path / "settings.json"
    assert not SettingsStore(path).settings.history.enabled
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "history": {"enabled": True},
                "captions": {"model_override": "medium"},
            }
        )
    )
    store = SettingsStore(path)
    assert store.settings.history.enabled and store.settings.captions.model_override == "medium"
    assert path.with_suffix(".v3.bak.json").is_file()
    store.save()
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_future_schema_cannot_be_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    text = '{"schema_version": 999, "future": "preserve me"}'
    path.write_text(text)
    with pytest.raises(ValueError):
        SettingsStore(path)
    assert path.read_text() == text


def test_settings_validate_finite_numbers_booleans_and_geometry():
    settings = from_dict(
        Settings,
        {
            "history": {"enabled": "false"},
            "captions": {"beam_size": 100},
            "overlay": {
                "opacity": float("nan"),
                "font_size": 999,
                "geometry": {"bad": [0, 0, -1, 400], "left": [-1920, 20, 600, 180]},
            },
        },
    )
    assert not settings.history.enabled
    assert settings.captions.beam_size == 10 and settings.overlay.font_size == 72
    assert settings.overlay.opacity == Settings().overlay.opacity
    assert settings.overlay.geometry == {"left": [-1920, 20, 600, 180]}


def test_profile_uses_shared_settings_and_snapshot_stays_immutable():
    settings = Settings()
    apply_profile(settings, "fast")
    snapshot = SessionConfig.from_settings(settings)
    assert snapshot.chunk_seconds == 3 and snapshot.beam_size == 1
    settings.captions.beam_size = 7
    settings.processing.base_url = "https://example.com"
    assert snapshot.beam_size == 1 and json.loads(snapshot.processing_json)["base_url"] == ""


def test_overlap_is_trimmed_but_deliberate_repetition_survives():
    reconciler = Reconciler()
    assert reconciler.accept("We will see you tomorrow.", 0, 3)
    assert reconciler.accept("you tomorrow. Bring snacks.", 2.7, 3) == "Bring snacks."
    assert reconciler.accept("Bring snacks.", 6, 1) == "Bring snacks."
    assert reconciler.accept("Bring snacks.", 6.8, 1) == ""


@pytest.mark.parametrize(
    "name", ["../escape", "a/../../escape", "/escape", "C:\\escape", "a\\..\\escape", "file:stream"]
)
def test_language_archive_cannot_escape_destination(tmp_path, name):
    path = tmp_path / "pack.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(name, b"untrusted")
    with pytest.raises(ValueError):
        validate_archive(path)


def test_language_archive_rejects_symlinks(tmp_path):
    path = tmp_path / "pack.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(link, "../outside")
    with pytest.raises(ValueError):
        validate_archive(path)


def test_translated_history_upserts_original_and_searches_translation(store):
    session = store.create_session("A session")
    segment = CaptionSegment(text="こんにちは", language="ja", audio_start=4.5)
    store.add_segment(session, segment)
    store.update_segment(
        session, segment.with_translation("Hello friend", "en", "builtin", PrivacyTier.ON_DEVICE)
    )
    store.flush()
    rows = store.segments(session)
    assert len(rows) == 1 and rows[0].text == "こんにちは"
    assert rows[0].translation == "Hello friend" and rows[0].audio_start == 4.5
    assert store.search("friend")[0].session_id == session


def test_logging_redacts_credentials_and_developer_paths():
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "",
        0,
        "Authorization: Bearer fake-secret C:\\Users\\Example\\project api_key=fake-private-value",
        (),
        None,
    )
    text = SafeFormatter("%(message)s").format(record)
    assert "fake-secret" not in text and "fake-private-value" not in text
    assert "Example" not in text


def test_selected_device_identity_survives_portaudio_index_changes():
    devices = [
        AudioDevice("2", "Speakers", 48000, 2, True, True),
        AudioDevice("8", "Headphones", 48000, 2, True),
    ]
    assert resolve_device(devices, "2", "Headphones", strict=True).id == "8"


def test_stopping_never_reuses_a_model_still_in_native_inference(qt_app):
    entered, release = threading.Event(), threading.Event()
    ready = []

    class Engine:
        def transcribe(self, audio, **kwargs):
            entered.set()
            release.wait(5)
            return Transcript("late result", confidence=0.9, speech_probability=1)

    class Capture:
        def start(self, device, callback):
            return AudioDevice("0", "Fake", 16000, 1, True)

        def stop(self):
            pass

    captioner = Captioner(lambda cfg: Engine(), capture_factory=Capture)
    captioner.segment_ready.connect(ready.append)
    config = SessionConfig.from_settings(Settings())
    try:
        assert captioner.start(config, None)
        deadline = time.monotonic() + 2
        while captioner.phase != PipelinePhase.RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        captioner._asr_queue.put_nowait(AudioWork(np.zeros(1600), 1, 0.1, time.monotonic()))
        assert entered.wait(2)
        started = time.monotonic()
        captioner.stop()
        assert time.monotonic() - started < 0.1
        assert not captioner.wait(0.05) and not captioner.start(config, None)
        assert captioner.has_workers
        release.set()
        assert captioner.wait(3)
        qt_app.processEvents()
        assert not ready
    finally:
        release.set()
        captioner.stop()
        captioner.wait(5)


def test_simple_mode_preserves_advanced_values(qt_app, tmp_path):
    from subbyai.ui.settings_view import SettingsView

    store = SettingsStore(tmp_path / "settings.json")
    store.settings.captions.beam_size = 7
    store.settings.captions.model_override = "medium"
    view = SettingsView(store)
    try:
        assert not view.advanced_toggle.isChecked()
        view.advanced_toggle.setChecked(True)
        view.advanced_toggle.setChecked(False)
        assert store.settings.captions.beam_size == 7
        assert store.settings.captions.model_override == "medium"
        assert view.nav.item(view._keys.index("processing")).isHidden()
    finally:
        view.close()


def test_theme_subscription_does_not_keep_a_view_alive(qt_app):
    import gc
    import weakref

    from subbyai.ui import theme

    class Observer:
        def changed(self, palette):
            pass

    observer = Observer()
    reference = weakref.ref(observer)
    theme.subscribe(observer.changed)
    del observer
    gc.collect()
    assert reference() is None
    theme.apply(qt_app, "dark")


def test_audio_preview_captures_without_loading_a_model():
    from subbyai.audio.preview import AudioPreview

    opened, stopped = threading.Event(), threading.Event()

    class Capture:
        def start(self, device, callback):
            callback(np.full((1600, 1), 0.25, dtype=np.float32), 16000)
            opened.set()

        def stop(self):
            stopped.set()

    preview = AudioPreview(Capture)
    preview.set_active(True, None)
    try:
        assert opened.wait(2)
        assert preview.level > 0.1
    finally:
        preview.close()
    assert stopped.wait(2)


def test_failed_settings_write_keeps_effective_values_and_notifies_user(tmp_path, monkeypatch):
    from subbyai.core.settings import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    errors, changes = [], []
    store.subscribe_errors(errors.append)
    store.subscribe(changes.append)
    store.settings.captions.beam_size = 7

    def blocked():
        raise OSError("Disk is full")

    monkeypatch.setattr(store, "save", blocked)
    store.notify("captions")
    assert store.settings.captions.beam_size == 7
    assert changes == ["captions"]
    assert len(errors) == 1 and "couldn't be saved" in errors[0]
def test_local_pack_decoder_cleans_boundaries_and_preserves_identifiers(monkeypatch):
    from types import SimpleNamespace

    from subbyai.translation.builtin import ArgosProvider

    class Tokenizer:
        def encode(self, text, out_type):
            return [text]

        def decode(self, tokens):
            return "\u2581Bonjour\u2581test_name."

    class Model:
        def translate_batch(self, *args, **kwargs):
            return [SimpleNamespace(hypotheses=[["test"]])]

    provider = ArgosProvider()
    monkeypatch.setattr(provider, "_translation_for", lambda *_: (Model(), Tokenizer()))
    assert provider._translate_hop("Hello", "en", "fr") == "Bonjour test_name."
