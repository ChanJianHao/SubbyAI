"""Native Windows acceptance: real loopback, ASR, translation and overlay.

Uses the public speech/model fixtures in build/acceptance; never reads personal
settings or saves captured transcripts. Serve the test WAV to a foreground
browser at http://127.0.0.1:8769/ and press Play in that page.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import winsound
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["HF_HUB_OFFLINE"] = "1"

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from subbyai import paths  # noqa: E402
from subbyai.app import Controller  # noqa: E402
from subbyai.audio.base import create_capture  # noqa: E402
from subbyai.core.logging_setup import SafeFormatter  # noqa: E402
from subbyai.core.settings import SettingsStore  # noqa: E402
from subbyai.ui import theme  # noqa: E402

OUTPUT = ROOT / "build" / "validation"
OUTPUT.mkdir(parents=True, exist_ok=True)
FIXTURES = ROOT / "build" / "acceptance" / "Roaming" / "subbyai"
paths.models_dir = lambda: FIXTURES / "models"
paths.data_dir = lambda: OUTPUT
paths.config_dir = lambda: OUTPUT
paths.translation_models_dir = lambda: FIXTURES / "translation"
paths.sessions_db = lambda: OUTPUT / "unused-session.db"
paths.exports_dir = lambda: OUTPUT
paths.log_dir = lambda: OUTPUT
handler = logging.FileHandler(OUTPUT / "desktop-flow.log", encoding="utf-8", mode="w")
handler.setFormatter(SafeFormatter("%(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.DEBUG)
for name in ("httpx", "httpcore", "huggingface_hub", "filelock", "faster_whisper"):
    logging.getLogger(name).setLevel(logging.WARNING)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/speech.wav":
            data = (ROOT / "build/public-speech/en_us-pcm.wav").read_bytes()
            content_type = "audio/wav"
        else:
            data = (
                b"<!doctype html><title>SubbyAI overlay validation</title>"
                b"<style>body{background:#eef2ff;color:#17253d;font:24px system-ui;"
                b"padding:6vw}audio{width:80%}</style><h1>SubbyAI overlay validation</h1>"
                b"<p>Public English speech. Press Play to test browser audio captions.</p>"
                b'<audio controls loop src="/speech.wav"></audio>'
            )
            content_type = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8769), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
app = QApplication([])
theme.apply(app, "dark", reduce_motion=True)
store = SettingsStore(OUTPUT / "desktop-settings.json")
store.settings.captions.model_override = "tiny"
store.settings.captions.compute_device = "cpu"
store.settings.captions.source_language = ""
store.settings.captions.target_language = "fr"
store.settings.history.enabled = False
store.settings.overlay.visible = True
store.settings.overlay.auto_hide = True
store.settings.overlay.click_through = True
store.settings.general.reduce_motion = True
store.settings.general.onboarding_complete = True
controller = Controller(app, store)
controller.shell.setWindowTitle("SubbyAI desktop validation")
controller.shell.show()
counts = {"captions": 0, "translations": 0, "mapped_captions": 0, "mapped_translations": 0}


def received(segment):
    counts["captions"] += 1
    counts["mapped_captions"] += int(controller.overlay.isVisible())
    assert controller.overlay.visible_pairs
    assert controller.overlay.windowOpacity() > 0


def updated(segment):
    if segment.display_translation:
        counts["translations"] += 1
        counts["mapped_translations"] += int(controller.overlay.isVisible())


controller.captioner.segment_ready.connect(received)
controller.captioner.segment_updated.connect(updated)


def inventory():
    try:
        devices = create_capture().list_devices()
        logging.info("Acceptance audio inventory count=%s", len(devices))
        controller.devices_changed.emit(devices)
    except Exception:
        logging.exception("Acceptance audio inventory failed")


threading.Thread(target=inventory, daemon=True).start()
controller.start_captions()


def report():
    result = {
        **counts,
        "devices": len(controller._devices),
        "speech_phase": controller._speech_state.phase.value,
        "active_model": controller._speech_state.model,
        "overlay_state": controller.overlay.state.value,
        "overlay_mapped": controller.overlay.isVisible(),
        "opacity": controller.overlay.windowOpacity(),
        "geometry": controller.overlay.geometry().getRect(),
        "dpr": controller.overlay.devicePixelRatioF(),
    }
    (OUTPUT / "desktop-flow.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    controller.shell.grab().save(str(OUTPUT / "live.png"))
    if counts["translations"]:
        controller.overlay.grab().save(str(OUTPUT / "overlay.png"))
    print(json.dumps(result), flush=True)


timer = QTimer()
timer.setInterval(10000)
timer.timeout.connect(report)
timer.start()
acceptance = {"phase": 0, "checks": []}
previous_engine = None
if "--auto" in sys.argv:
    (OUTPUT / "desktop-acceptance.json").write_text(json.dumps(acceptance, indent=2))


def drive():
    global previous_engine
    from subbyai.asr.lifecycle import SpeechPhase
    from subbyai.core.settings import SessionConfig
    from subbyai.ui.overlay import OverlayState

    state = controller._speech_state
    phase = acceptance["phase"]
    if phase == 0 and state.phase is SpeechPhase.ACTIVE:
        winsound.PlaySound(
            str(ROOT / "build/public-speech/en_us-pcm.wav"),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
        )
        acceptance["phase"] = 1
    elif phase == 1 and counts["translations"] >= 2:
        assert controller.overlay.isVisible()
        assert controller.overlay.windowOpacity() > 0
        assert controller.overlay.state is OverlayState.VISIBLE
        assert controller.overlay._ring[-1].text == controller.live._segments[-1].text
        controller._on_overlay_toggled(False)
        assert not controller.overlay.isVisible()
        controller._on_overlay_toggled(True)
        assert controller.overlay.isVisible() and controller.overlay.windowOpacity() > 0
        controller.settings.overlay.font_size = 34
        controller.store.notify("overlay")
        assert controller.overlay.caption_style.font_size == 34
        controller.overlay.save_geometry()
        controller.settings_view.captions.reset_position()
        assert (
            controller.overlay.screen().availableGeometry().contains(controller.overlay.geometry())
        )
        controller.shell.hide()
        assert controller.overlay.isVisible()
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.GetTopWindow.argtypes = [wintypes.HWND]
        user32.GetTopWindow.restype = wintypes.HWND
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        hwnd = int(controller.overlay.winId())
        style = get_style(hwnd, -20)
        assert style & 0x8  # WS_EX_TOPMOST
        assert style & 0x20  # WS_EX_TRANSPARENT
        assert style & 0x08000000  # WS_EX_NOACTIVATE
        foreground = user32.GetForegroundWindow()
        assert foreground != hwnd
        cursor = user32.GetTopWindow(None)
        overlay_above_foreground = False
        while cursor:
            if cursor == hwnd:
                overlay_above_foreground = True
                break
            if cursor == foreground:
                break
            cursor = user32.GetWindow(cursor, 2)  # GW_HWNDNEXT
        acceptance["native_overlay_above_foreground"] = overlay_above_foreground
        assert overlay_above_foreground
        acceptance["checks"].extend(
            [
                "real_loopback_asr_translation_overlay",
                "live_toggle",
                "live_style",
                "reset_position",
                "main_window_hidden",
                "native_topmost_clickthrough_noactivate",
            ]
        )
        controller.settings_view.languages.hear.choose("en")
        acceptance["phase"] = 2
    elif phase == 2 and state.phase is SpeechPhase.ACTIVE and state.source == "en":
        acceptance["checks"].append("language_runtime_reconnect")
        controller.settings_view.languages.hear.choose("")
        acceptance["phase"] = 3
    elif phase == 3 and state.phase is SpeechPhase.ACTIVE and state.source == "":
        acceptance["checks"].append("auto_detect_runtime_reconnect")
        previous_engine = controller.engines.current
        controller.settings_view.intelligence._on_model_chosen("small")
        acceptance["phase"] = 4
    elif phase == 4 and state.phase is SpeechPhase.ACTIVE and state.model == "small":
        assert previous_engine is not controller.engines.current
        assert not previous_engine.is_loaded
        acceptance["checks"].append("real_model_switch_releases_previous")
        acceptance["small_start_count"] = counts["captions"]
        acceptance["phase"] = 5
    elif phase == 5 and counts["captions"] > acceptance["small_start_count"]:
        acceptance["checks"].append("switched_model_generates_captions")
        controller.settings_view.intelligence._on_model_chosen("tiny")
        acceptance["phase"] = 6
    elif phase == 6 and state.phase is SpeechPhase.ACTIVE and state.model == "tiny":
        acceptance["checks"].append("switch_back")
        controller.store.notify("captions")
        restored = SettingsStore(controller.store.path).settings
        assert SessionConfig.from_settings(restored).model_name == "tiny"
        assert restored.overlay.visible
        acceptance["checks"].append("persisted_selection_and_overlay")
        controller.overlay.grab().save(str(OUTPUT / "overlay.png"))
        controller.shell.show()
        controller.shell.grab().save(str(OUTPUT / "live.png"))
        acceptance["result"] = "passed"
        (OUTPUT / "desktop-acceptance.json").write_text(json.dumps(acceptance, indent=2))
        driver.stop()
        winsound.PlaySound(None, 0)
        controller.quit()


def guarded_drive():
    try:
        drive()
    except Exception:
        logging.exception("Desktop acceptance assertion failed")
        acceptance["result"] = "failed"
        (OUTPUT / "desktop-acceptance.json").write_text(json.dumps(acceptance, indent=2))
        driver.stop()
        winsound.PlaySound(None, 0)
        controller.quit()


driver = QTimer()
driver.setInterval(200)
driver.timeout.connect(guarded_drive)
if "--auto" in sys.argv:
    driver.start()


def timeout():
    if "--auto" in sys.argv and acceptance.get("result") != "passed":
        acceptance["result"] = "timed_out"
        (OUTPUT / "desktop-acceptance.json").write_text(json.dumps(acceptance, indent=2))
    controller.quit()


QTimer.singleShot(180000, timeout)
app.exec()
if "--auto" in sys.argv:
    winsound.PlaySound(None, 0)
report()
server.shutdown()
if "--auto" in sys.argv:
    raise SystemExit(0 if acceptance.get("result") == "passed" else 1)
