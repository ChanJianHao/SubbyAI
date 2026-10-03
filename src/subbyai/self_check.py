"""Exercise recognition, translation and Qt inside the packaged application."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import numpy as np


def run_self_check(
    require_models: bool = False,
    require_gpu: bool = False,
    speech_fixture: Path | None = None,
    model_cache: Path | None = None,
) -> int:
    """Returns 0 when the packaged app can caption and translate.

    ``require_models`` requires Whisper, Parakeet and a translation pack. The
    release workflow prepares pinned fixtures before testing the frozen binary.
    ``model_cache`` optionally selects an isolated speech-model test cache.
    """
    # The check translates real text, and a Windows console is usually cp1252.
    # Without this, a successful Korean translation is reported as a failure
    # because printing the proof of success is what actually threw.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(errors="replace")

    failures: list[str] = []
    skipped: list[str] = []

    # A stalled native library must leave a useful traceback in release logs.
    import faulthandler

    with contextlib.suppress(Exception):
        faulthandler.dump_traceback_later(120)
    try:
        for name, check in (
            ("imports", _check_imports),
            ("Qt", _check_ui),
            ("CTranslate2", _check_ctranslate2),
            ("ONNX Runtime", _check_onnx_runtime),
            (
                "Whisper",
                lambda: _check_recognition(skipped, require_gpu, speech_fixture, model_cache),
            ),
            ("Parakeet", lambda: _check_parakeet(skipped, speech_fixture, model_cache)),
            ("translation", lambda: _check_translation(skipped)),
        ):
            print(f"Checking {name}...", flush=True)
            failures += check()
    finally:
        with contextlib.suppress(Exception):
            faulthandler.cancel_dump_traceback_later()

    if require_models and skipped:
        failures += [f"{what} was not available to test" for what in skipped]

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    for what in skipped:
        print(f"SKIP {what} (not present on this machine)")
    print("SELF-CHECK OK" + (" (with skips)" if skipped else ""))
    return 0


def _check_imports() -> list[str]:
    """Every module the running app touches must have survived packaging."""
    modules = [
        "subbyai.app",
        "subbyai.pipeline.captioner",
        "subbyai.ui.overlay",
        "subbyai.ui.shell",
        "subbyai.ui.onboarding",
        "subbyai.ui.settings_view",
        "subbyai.ui.history_view",
        "subbyai.asr.engine",
        "subbyai.asr.models",
        "subbyai.asr.parakeet",
        "subbyai.translation.builtin",
        "subbyai.translation.llm",
        "subbyai.storage.session_store",
        "subbyai.system.hotkeys",
    ]
    failures = []
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:
            failures.append(f"import {name}: {exc}")
    return failures


def _check_ui() -> list[str]:
    """Instantiate real Qt surfaces against a temporary, private-free profile."""
    import os
    import tempfile
    from pathlib import Path

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QWidget

        from .core.settings import SettingsStore
        from .ui import theme
        from .ui.live_view import LiveView
        from .ui.overlay import CaptionOverlay
        from .ui.settings_view import SettingsView
        from .ui.shell import Shell

        app = QApplication.instance() or QApplication([])
        if sys.platform == "win32":
            for filename in ("segoeui.ttf", "segoeuib.ttf", "YuGothR.ttc", "msyh.ttc"):
                QFontDatabase.addApplicationFont(
                    str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename)
                )
        with tempfile.TemporaryDirectory() as scratch:
            profile = Path(scratch) / "settings.json"
            profile.write_text('{"schema_version": 4}', encoding="utf-8")
            store = SettingsStore(profile)
            theme.apply(app, "dark", "sakura")
            shell = Shell(store.settings)
            shell.add_surface(LiveView(store.settings))
            shell.add_surface(QWidget())
            shell.add_surface(SettingsView(store))
            overlay = CaptionOverlay(store.settings)
            app.processEvents()
            if shell.minimumSizeHint().width() > 640:
                return ["the settings layout exceeds the supported minimum width"]
            overlay.close()
            shell.close()
            shell.deleteLater()
            overlay.deleteLater()
            app.processEvents()
        print("Qt surfaces: OK (temporary profile, no audio capture)")
    except Exception as exc:
        return [f"Qt surfaces: {exc}"]
    return []


def _check_ctranslate2() -> list[str]:
    try:
        import ctranslate2  # noqa: F401
    except Exception as exc:
        return [f"ctranslate2 unusable: {exc}"]
    if getattr(sys, "frozen", False) and any(name in sys.modules for name in ("torch", "av")):
        return ["an unused ML or media-decoding runtime was imported"]
    return []


def _check_onnx_runtime() -> list[str]:
    """The second engine's runtime must have survived packaging.

    onnx_asr is imported lazily inside ParakeetEngine.load(), so a build that
    dropped it would look fine until the first caption.
    """
    try:
        import onnx_asr  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception as exc:
        return [f"the Parakeet engine is unusable: {exc}"]
    return []


def _check_recognition(
    skipped: list[str],
    require_gpu: bool = False,
    speech_fixture: Path | None = None,
    model_cache: Path | None = None,
) -> list[str]:
    """Run the real decoder, optionally with a public speech fixture and CUDA."""
    try:
        from .asr.engine import TranscriptionEngine
        from .asr.models import ModelManager

        manager = ModelManager(model_cache)
        if not manager.is_downloaded("tiny"):
            skipped.append("Whisper recognition")
            return []
        audio = _fixture_audio(speech_fixture)
        engine = TranscriptionEngine("tiny", "gpu" if require_gpu else "cpu", manager)
        try:
            engine.load()
            if require_gpu and engine.device != "cuda":
                return ["CUDA was required, but the engine fell back to CPU"]
            result = engine.transcribe(audio, "en" if speech_fixture else None)
            if speech_fixture is not None and not result.text.strip():
                return ["the speech fixture produced no captions"]
            print(f"recognition: OK ({engine.device}, {'speech' if speech_fixture else 'silence'})")
        finally:
            engine.unload()
    except Exception as exc:
        return [f"recognition: {exc}"]
    return []


def _check_parakeet(
    skipped: list[str], speech_fixture: Path | None, model_cache: Path | None
) -> list[str]:
    try:
        from .asr.models import ModelManager
        from .asr.parakeet import ParakeetEngine

        manager = ModelManager(model_cache)
        name = "parakeet-tdt-0.6b-v3"
        if not manager.is_downloaded(name):
            skipped.append("Parakeet recognition")
            return []
        engine = ParakeetEngine(name, models=manager)
        try:
            engine.load()
            result = engine.transcribe(_fixture_audio(speech_fixture))
            if speech_fixture is not None and not result.text.strip():
                return ["Parakeet produced no captions for the speech fixture"]
            if result.confidence_known or result.language is not None:
                return ["Parakeet returned unsupported confidence or language metadata"]
            print(f"Parakeet recognition: OK ({engine.device})")
        finally:
            engine.unload()
    except Exception as exc:
        return [f"Parakeet recognition: {exc}"]
    return []


def _fixture_audio(path: Path | None) -> np.ndarray:
    if path is None:
        return np.zeros(16000, dtype=np.float32)
    import wave

    with wave.open(str(path)) as stream:
        format_ = (stream.getnchannels(), stream.getsampwidth(), stream.getframerate())
        if format_ != (1, 2, 16000):
            raise ValueError("the speech fixture must be 16 kHz mono 16-bit PCM WAV")
        if not 0 < stream.getnframes() <= 16000 * 60:
            raise ValueError("the speech fixture must contain up to 60 seconds of audio")
        frames = stream.readframes(stream.getnframes())
        if len(frames) != stream.getnframes() * 2:
            raise ValueError("the speech fixture is truncated")
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768


def _check_translation(skipped: list[str]) -> list[str]:
    """Translate one line with whatever language pack is installed."""
    try:
        from .translation.builtin import ArgosProvider

        provider = ArgosProvider()
        pairs = provider.installed_pairs()
        if not pairs:
            skipped.append("translation")
            return []
        source, target = pairs[0]
        text = provider.translate("Hello there.", source, target).text
        if not text.strip():
            return ["translation returned nothing"]
        if "\u2581" in text or "<0x" in text:
            return ["translation returned undecoded tokenizer markers"]
        print(f"translated {source}->{target}: {text}")
    except Exception as exc:
        return [f"translation: {exc}"]
    return []
