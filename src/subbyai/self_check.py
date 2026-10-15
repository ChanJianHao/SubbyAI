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
            ("build identity", _check_build_info),
            ("Qt", _check_ui),
            ("shortcut settings", _check_shortcuts),
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
        "subbyai.system.shortcut_settings",
        "subbyai.core.shortcuts",
    ]
    failures = []
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:
            failures.append(f"import {name}: {exc}")
    return failures


def _check_build_info() -> list[str]:
    from .build_info import get_build_info

    build = get_build_info()
    if getattr(sys, "frozen", False) and build.built_at is None:
        return ["the packaged application is missing its version/build timestamp"]
    print(f"Build identity: {build.display_text}")
    if build.commit:
        print(f"Build source: {build.commit} ({'dirty' if build.source_dirty else 'clean'})")
    return []


def _check_shortcuts() -> list[str]:
    """Verify discoverability and persisted editing in the actual frozen UI."""
    import tempfile

    from PySide6.QtWidgets import QApplication

    from .core.settings import SettingsStore
    from .core.shortcuts import default_bindings
    from .ui.settings_view import SettingsView

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        profile = Path(directory) / "settings.json"
        store = SettingsStore(profile)
        view = SettingsView(store)
        try:
            view.show_section("shortcuts")
            if view.advanced_toggle.isChecked() or view.nav.currentItem().isHidden():
                return ["Shortcuts is not reachable in normal Settings"]
            view.search.setText("shortcut")
            if view.nav.currentItem() is None or view.nav.currentItem().isHidden():
                return ["Shortcuts is not searchable in normal Settings"]
            view.shortcuts.propose("toggle_captions", "Ctrl+Alt+J")
            reloaded = SettingsStore(profile).settings.shortcuts.globals
            if reloaded["toggle_captions"] != "Ctrl+Alt+J":
                return ["shortcut edits did not persist"]
            view.shortcuts.clear("toggle_captions")
            if SettingsStore(profile).settings.shortcuts.globals["toggle_captions"]:
                return ["cleared shortcut did not persist"]
            view.shortcuts.reset("toggle_captions")
            if SettingsStore(profile).settings.shortcuts.globals != default_bindings():
                return ["reset shortcut did not persist"]
            view.show_section("diagnostics")
            view.search.setText("diagnostics")
            view.advanced_toggle.setChecked(False)
            if view.nav.currentRow() != -1 or view.stack.currentWidget().widget() is None:
                return ["filtered Settings retained an invisible selection"]
            view.show_section("shortcuts")
            if view.advanced_toggle.isChecked() or view.nav.currentItem().isHidden():
                return ["Settings did not recover from an empty search"]
        finally:
            view.close()
            view.deleteLater()
            app.processEvents()
    print("Shortcut settings: OK (normal navigation, search, persisted edits, clear, reset)")
    return []


def _check_ui() -> list[str]:
    """Instantiate real Qt surfaces against a temporary, private-free profile."""
    import os
    import tempfile
    from pathlib import Path

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QWidget

        from .core.events import CaptionSegment
        from .core.health import HealthReport, HealthState
        from .core.settings import SettingsStore
        from .ui import theme
        from .ui.live_view import LiveView
        from .ui.motion import policy
        from .ui.overlay import CaptionOverlay
        from .ui.settings_view import SettingsView
        from .ui.shell import Shell
        from .ui.tray import Tray

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
            live = LiveView(store.settings)
            shell.add_surface(live)
            shell.add_surface(QWidget())
            settings = SettingsView(store)
            shell.add_surface(settings)
            overlay = CaptionOverlay(store.settings)
            shell.show()
            app.processEvents()
            failures = _check_overlay_startup(app, Path(scratch) / "overlay-control")
            if failures:
                return failures
            # Exercise the actual tray QAction and deferred restore path, not
            # just QWidget's logical visibility. Native hiding/minimizing can
            # otherwise pass Qt-only checks while the main window stays absent.
            tray = Tray(shell)
            tray.open_requested.connect(shell.show_for_user)
            failures = _check_tray_restore(app, shell, tray)
            if failures:
                return failures
            if not shell.version_label.isVisible():
                return ["the application version/build timestamp is not visible"]
            if shell.version_label.width() < shell.version_label.minimumSizeHint().width():
                return ["the application version/build timestamp is clipped"]
            # Exercise the release-blocking default auto-hide path in the
            # actual frozen Qt runtime. Opacity alone never maps a window.
            segment = CaptionSegment(text="Subtitles are ready.", language="en")
            overlay.show_segment(segment)
            app.processEvents()
            if not overlay.isVisible() or not overlay.visible_pairs:
                return ["the enabled overlay did not display its caption"]
            window_id = overlay.winId()
            overlay.set_enabled(False)
            if overlay.isVisible():
                return ["disabling the overlay did not hide its window"]
            overlay.set_enabled(True)
            app.processEvents()
            if not overlay.isVisible() or overlay.winId() != window_id:
                return ["enabling the overlay failed or recreated its native window"]
            if shell.minimumSizeHint().width() > 640:
                return ["the settings layout exceeds the supported minimum width"]
            for index in (1, 2, 0, 2, 0):
                shell.show_segment(index)
                if shell.current_segment != index:
                    return ["page navigation did not settle on the requested surface"]
            live.set_health(HealthReport(HealthState.STARTING))
            live.set_download_progress(0.6, "Checking download feedback")
            live.add_segment(CaptionSegment(text="Subtitles are ready.", language="en"))
            loop = QEventLoop()
            QTimer.singleShot(300, loop.quit)
            loop.exec()
            if not shell.stack._snapshot.pixmap.isNull() or live.progress.value() != 60:
                return ["the motion or progress state did not settle"]
            live.hide_download_progress()
            policy().configure(True)
            shell.show_segment(2)
            if not shell.stack._snapshot.pixmap.isNull():
                return ["reduced motion left a page transition running"]
            policy().configure(False)
            overlay.close()
            shell.close()
            shell.deleteLater()
            overlay.deleteLater()
            app.processEvents()
        print(f"Qt surfaces and motion: OK ({app.platformName()}, temporary profile)")
    except Exception as exc:
        return [f"Qt surfaces: {exc}"]
    return []


def _check_overlay_startup(app, scratch: Path) -> list[str]:
    """Fixed controller/pipeline check, with controlled slow/failing dependencies.

    No real audio or account configuration is touched. Recognition with actual
    models is a separate mandatory check. This runs in the frozen Qt runtime too.
    """
    import contextlib
    import threading
    import time

    from PySide6.QtCore import QTimer

    from . import paths
    from .app import Controller
    from .asr.engine import EngineCache, EngineError, Transcript
    from .audio.base import AudioDevice
    from .core.events import CaptionSegment
    from .core.health import HealthState
    from .core.settings import SettingsStore
    from .pipeline import PipelinePhase
    from .system import window_effects

    scratch.mkdir(parents=True, exist_ok=True)
    overrides = {
        "data_dir": scratch,
        "config_dir": scratch,
        "models_dir": scratch / "models",
        "translation_models_dir": scratch / "translation",
        "log_dir": scratch / "logs",
        "sessions_db": scratch / "sessions.db",
        "exports_dir": scratch / "exports",
    }
    originals = {name: getattr(paths, name) for name in overrides}
    for name, value in overrides.items():
        setattr(paths, name, lambda value=value: value)
    controller = None
    release, entered = threading.Event(), threading.Event()
    save_release = threading.Event()
    save_release.set()
    history_release = threading.Event()
    history_release.set()
    heartbeat = QTimer()
    ticks = []
    heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
    heartbeat.start(10)

    def settle(predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        app.processEvents()
        if not predicate():
            raise AssertionError("a lifecycle transition timed out")

    native = window_effects.IS_WINDOWS and app.platformName() == "windows"
    durations = []
    device = AudioDevice("validation", "Validation source", 16000, 1, True, True)

    class Capture:
        def start(self, selected, callback):
            return device

        def stop(self):
            pass

    class Engine:
        def transcribe(self, audio, **kwargs):
            return Transcript("")

    try:
        store = SettingsStore(scratch / "settings.json")
        store.settings.captions.model_override = "tiny"
        store.settings.captions.compute_device = "cpu"
        store.settings.captions.target_language = ""
        store.settings.history.enabled = False
        store.settings.overlay.visible = False
        store.settings.general.reduce_motion = True
        controller = Controller(app, store)
        controller.engines = EngineCache()
        controller.models.is_downloaded = lambda model: True
        controller._devices_ready = True
        controller._devices = [device]
        controller.captioner._capture_factory = Capture
        controller.captioner._speech_detector_factory = None
        controller.shell.show()
        controller._on_overlay_toggled(False)
        app.processEvents()

        def loading(config):
            entered.set()
            if not release.wait(5):
                raise EngineError("Controlled startup timed out")
            raise EngineError("Controlled startup failure: retry captions")

        controller.captioner._engine_provider = loading
        controller._begin_session()
        settle(entered.is_set)
        settle(lambda: controller.captioner.health.state is HealthState.STARTING)

        def assert_window(enabled):
            assert controller.live.overlay_chip.isChecked() is enabled
            assert controller.overlay.isVisible() is enabled
            if enabled:
                assert controller.overlay._render_pairs(), "enabled bar painted no feedback"
            if native:
                assert (
                    bool(window_effects._user32.IsWindowVisible(int(controller.overlay.winId())))
                    is enabled
                )

        for _ in range(20):
            for enabled in (True, False):
                before = time.perf_counter()
                controller.live.overlay_chip.click()
                durations.append(time.perf_counter() - before)
                app.processEvents()
                assert_window(enabled)
        controller.live.overlay_chip.click()
        app.processEvents()
        assert "Starting" in controller.live.overlay_chip.text()
        handle = int(controller.overlay.winId())
        if native:
            foreground = (
                int(window_effects._user32.GetForegroundWindow())
                if hasattr(window_effects._user32, "GetForegroundWindow")
                else 0
            )
            window_effects._user32.ShowWindow(handle, 0)
            assert controller.overlay.isVisible()  # Qt/native state disagreement
            controller._on_overlay_toggled(True)
            assert_window(True)
            if foreground:
                assert int(window_effects._user32.GetForegroundWindow()) == foreground

        save_entered = threading.Event()
        original_write = store._write
        save_release.clear()

        def slow_write(settings):
            save_entered.set()
            if not save_release.wait(5):
                raise OSError("Controlled settings write timeout")
            original_write(settings)

        store._write = slow_write
        controller._on_overlay_toggled(False)
        settle(save_entered.is_set)
        tick_count = len(ticks)
        for enabled in (True, False, True):
            before = time.perf_counter()
            controller.live.overlay_chip.click()
            durations.append(time.perf_counter() - before)
            app.processEvents()
            assert_window(enabled)
        settle(lambda: len(ticks) >= tick_count + 3)
        save_release.set()
        assert store.flush_pending(5)
        store._write = original_write

        release.set()
        settle(lambda: not controller.captioner.has_workers)
        assert controller.captioner.health.state is HealthState.ERROR
        assert "Error" in controller.live.overlay_chip.text()
        assert_window(True)
        assert not controller.live.banner.isHidden()

        controller.captioner._engine_provider = lambda config: Engine()
        for _ in range(3):
            controller._begin_session()
            settle(lambda: controller.captioner.phase is PipelinePhase.RUNNING)
            controller.stop_captions()
            settle(lambda: not controller.captioner.has_workers)
            assert_window(True)

        history_entered = threading.Event()
        connect = controller.sessions._connect
        history_release.clear()

        @contextlib.contextmanager
        def slow_history():
            history_entered.set()
            if not history_release.wait(5):
                raise OSError("Controlled history write timeout")
            with connect() as connection:
                yield connection

        controller.sessions._connect = slow_history
        controller.settings.history.enabled = True
        controller.settings.history.save_original = True
        controller.settings.history.retention_days = 0
        before = time.perf_counter()
        controller._begin_session()
        lifecycle_duration = time.perf_counter() - before
        settle(history_entered.is_set)
        settle(lambda: controller.captioner.phase is PipelinePhase.RUNNING)
        session = controller._session_id
        controller._on_segment(CaptionSegment("Public history lifecycle check."))
        controller.stop_captions()
        settle(lambda: not controller.captioner.has_workers)
        tick_count = len(ticks)
        for enabled in (False, True, False, True):
            before = time.perf_counter()
            controller.live.overlay_chip.click()
            durations.append(time.perf_counter() - before)
            app.processEvents()
            assert_window(enabled)
        settle(lambda: len(ticks) >= tick_count + 3)
        history_release.set()
        controller.sessions.flush(5)
        controller.sessions._connect = connect
        assert lifecycle_duration < 0.15, f"history start blocked for {lifecycle_duration:.3f}s"
        assert [s.text for s in controller.sessions.segments(session)] == [
            "Public history lifecycle check.",
        ]
        assert controller.sessions.get_session(session).ended_at is not None
        controller._on_overlay_toggled(False)
        assert_window(False)
        assert int(controller.overlay.winId()) == handle
        assert max(durations) < 0.15, f"click blocked for {max(durations):.3f} seconds"
        print(
            f"Overlay lifecycle: OK ({app.platformName()}, {len(durations)} clicks, "
            f"max {max(durations) * 1000:.1f} ms, slow save/history/startup, "
            "failure/retry, same window)"
        )
    except Exception as exc:
        return [f"Overlay lifecycle: {exc}"]
    finally:
        release.set()
        save_release.set()
        history_release.set()
        heartbeat.stop()
        if controller is not None:
            controller.captioner.stop(drain=False)
            controller.captioner.wait(10)
            controller.store.flush_pending(5)
            controller._level_timer.stop()
            controller._model_timer.stop()
            controller._retention_timer.stop()
            if controller._retention_thread:
                controller._retention_thread.join(5)
            controller.sessions.close()
            controller.overlay.close()
            controller.shell.hide()
            controller.shell.deleteLater()
            controller.overlay.deleteLater()
            controller.deleteLater()
            app.processEvents()
        for name, value in originals.items():
            setattr(paths, name, value)
    return []


def _check_tray_restore(app, shell, tray) -> list[str]:
    from PySide6.QtCore import QEventLoop, QTimer

    def settle():
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()

    native = None
    if sys.platform == "win32" and app.platformName() == "windows":
        import ctypes
        from ctypes import wintypes

        native = ctypes.WinDLL("user32")
        for name in ("IsWindowVisible", "IsIconic", "IsZoomed"):
            function = getattr(native, name)
            function.argtypes = [wintypes.HWND]
            function.restype = wintypes.BOOL
        native.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    handle = int(shell.winId())
    scenarios = ["hidden", "minimized", "maximized_minimized"]
    if native is not None:
        scenarios.append("native_hidden")
    for scenario in scenarios:
        shell.showNormal()
        settle()
        if scenario == "hidden":
            shell.close()  # close-to-tray
        elif scenario == "native_hidden":
            native.ShowWindow(handle, 0)  # logical Qt visibility can remain true
        else:
            if scenario == "maximized_minimized":
                shell.showMaximized()
                settle()
            shell.showMinimized()
        settle()
        tray._open_action.trigger()
        settle()
        if not shell.isVisible() or shell.isMinimized() or int(shell.winId()) != handle:
            return [f"tray Open did not restore the {scenario} window"]
        if native is not None and (not native.IsWindowVisible(handle) or native.IsIconic(handle)):
            return [f"tray Open left the {scenario} native window hidden/minimized"]
        if scenario == "maximized_minimized" and not shell.isMaximized():
            return ["tray Open discarded the window's maximized state"]
    shell.showNormal()
    settle()
    print(f"Tray Open: OK ({', '.join(scenarios)}, same native window)")
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
