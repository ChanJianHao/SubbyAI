"""Real install/cancel/resume/load acceptance in an isolated workspace cache."""

from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import os
import sys
import tempfile
import time
import winsound
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.pop("HF_HUB_OFFLINE", None)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from subbyai import paths  # noqa: E402
from subbyai.app import Controller  # noqa: E402
from subbyai.audio.base import create_capture  # noqa: E402
from subbyai.core.logging_setup import SafeFormatter  # noqa: E402
from subbyai.core.settings import SettingsStore  # noqa: E402
from subbyai.ui import theme  # noqa: E402

OUTPUT = ROOT / "build/validation"
OUTPUT.mkdir(parents=True, exist_ok=True)
arguments = argparse.ArgumentParser()
arguments.add_argument("--fresh", action="store_true", help="Use a second isolated download cache")
options = arguments.parse_args()
cache = (
    Path(tempfile.mkdtemp(prefix="install-models-", dir=OUTPUT))
    if options.fresh
    else (OUTPUT / "install-models")
)
paths.models_dir = lambda: cache
trace = (OUTPUT / "install-thread-diagnostics.log").open("w", encoding="utf-8")
faulthandler.dump_traceback_later(90, repeat=True, file=trace)
paths.data_dir = lambda: OUTPUT
paths.config_dir = lambda: OUTPUT
paths.exports_dir = lambda: OUTPUT
paths.log_dir = lambda: OUTPUT
paths.translation_models_dir = lambda: OUTPUT / "install-translation"
paths.sessions_db = lambda: OUTPUT / "unused-install-session.db"
handler = logging.FileHandler(OUTPUT / "model-install.log", encoding="utf-8", mode="w")
handler.setFormatter(SafeFormatter("%(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
for name in ("httpx", "httpcore", "huggingface_hub", "filelock"):
    logging.getLogger(name).setLevel(logging.WARNING)

app = QApplication([])
theme.apply(app, "dark", reduce_motion=True)
store = SettingsStore(OUTPUT / "install-settings.json")
store.settings.captions.model_override = "small"
store.settings.captions.compute_device = "cpu"
store.settings.captions.source_language = "en"
store.settings.captions.target_language = ""
store.settings.history.enabled = False
store.settings.overlay.visible = True
controller = Controller(app, store)
controller.shell.setWindowTitle("SubbyAI model install validation")
controller.shell.show_segment(2)
controller.settings_view.show_section("models")
controller.shell.show()
result = {"phases": [], "gui_ticks": 0, "cancelled": False, "captions": 0}
began = time.monotonic()
model = "tiny.en"


def progress(name, event):
    if name != model:
        return
    if not result["phases"] or result["phases"][-1] != event.phase:
        result["phases"].append(event.phase)
        print(f"Model install stage: {event.phase}", flush=True)
    if event.phase == "downloading":
        row = controller.settings_view.intelligence.catalogue.rows[model]
        assert not row.install_button.isEnabled()
        if not result["cancelled"] and event.downloaded_bytes >= 2 * 1024**2:
            result["cancelled"] = True
            controller.downloads.cancel()
    if event.phase == "error":
        result["error"] = event.message


def finished(name, ok):
    if name != model:
        return
    if not ok and controller.downloads.progress[name].phase == "cancelled":
        QTimer.singleShot(0, lambda: controller.settings_view.intelligence._on_install(model))
        return
    if not ok:
        result["result"] = "failed"
        controller.quit()
        return
    assert controller.settings.model_name == "small"
    assert not controller._speech_state.model
    result["installed_without_switch"] = True
    controller.settings_view.intelligence._on_model_chosen(model)


controller.downloads.changed.connect(progress)
controller.downloads.finished.connect(finished)
controller.captioner.segment_ready.connect(lambda _: result.update(captions=result["captions"] + 1))
controller._on_devices_changed(create_capture().list_devices())
controller.settings_view.intelligence._on_install(model)
controller.settings_view.intelligence._on_install(model)  # repeated click must share the job
playing = False


def tick():
    global playing
    result["gui_ticks"] += 1
    state = controller._speech_state
    if state.phase.value == "ready" and not playing:
        assert controller.engines.current.is_loaded
        assert controller.engines.current.model_name == model
        assert "Ready" in controller.live.speech_indicator.text()
        controller.start_captions()
        playing = True
    if playing and state.phase.value == "active" and not result.get("audio_started"):
        result["audio_started"] = True
        winsound.PlaySound(
            str(ROOT / "build/public-speech/en_us-pcm.wav"),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
        )
    if result["captions"] and state.model == model:
        assert controller.overlay.isVisible()
        result["active_model"] = state.model
        result["elapsed_seconds"] = round(time.monotonic() - began, 1)
        result["result"] = "passed"
        (OUTPUT / "model-install.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        controller.shell.grab().save(str(OUTPUT / "model-installed.png"))
        timer.stop()
        winsound.PlaySound(None, 0)
        controller.quit()


timer = QTimer()
timer.setInterval(50)
timer.timeout.connect(tick)
timer.start()
QTimer.singleShot(300000, controller.quit)
app.exec()
faulthandler.cancel_dump_traceback_later()
winsound.PlaySound(None, 0)
(OUTPUT / "model-install.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result), flush=True)
raise SystemExit(0 if result.get("result") == "passed" else 1)
