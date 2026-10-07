"""Exercise real Qt interactions and rendering with fictional, isolated content.

Run with QT_QPA_PLATFORM=windows for native Windows checks, and repeat with
QT_SCALE_FACTOR=1.5 or 2. Screenshots and timing results go to ignored build output.
No microphone, network provider or personal application data is accessed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QAbstractAnimation, QPoint, Qt, qInstallMessageHandler  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from subbyai.asr.capability import MachineCapability  # noqa: E402
from subbyai.core.events import CaptionSegment, PrivacyTier  # noqa: E402
from subbyai.core.health import HealthReport, HealthState  # noqa: E402
from subbyai.core.settings import OverlayPreset, SettingsStore  # noqa: E402
from subbyai.storage import SessionStore  # noqa: E402
from subbyai.ui import theme  # noqa: E402
from subbyai.ui.history_view import HistoryView  # noqa: E402
from subbyai.ui.live_view import LiveView  # noqa: E402
from subbyai.ui.motion import policy  # noqa: E402
from subbyai.ui.onboarding import OnboardingWizard  # noqa: E402
from subbyai.ui.overlay import CaptionOverlay  # noqa: E402
from subbyai.ui.settings_view import SettingsDeps, SettingsView  # noqa: E402
from subbyai.ui.shell import Shell  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "motion-smoke")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    paint_errors = []

    def message_handler(kind, context, message):
        if any(word in message for word in ("QPainter::", "QBackingStore::", "paint engine")):
            paint_errors.append(message)

    qInstallMessageHandler(message_handler)
    app = QApplication([])
    navigation_ms = []
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        store = SettingsStore(scratch / "settings.json")
        store.settings.general.close_to_tray = False
        store.settings.captions.target_language = "fr"
        sessions = SessionStore(scratch / "sessions.db")
        capability = MachineCapability(
            has_cuda=False, cpu_cores=8, ram_gb=16, free_disk_gb=100, platform=sys.platform
        )
        shell = Shell(store.settings)
        live = LiveView(store.settings)
        history = HistoryView(sessions)
        settings = SettingsView(store, SettingsDeps(capability=capability, session_store=sessions))
        for surface in (live, history, settings):
            shell.add_surface(surface)
        overlay = CaptionOverlay(store.settings)
        shell.show()
        QTest.qWait(100)
        for mode in ("dark", "light"):
            theme.apply(app, mode, reduce_motion=False)
            policy()._system = False  # Exercise motion even on a reduced-motion test host.
            for index in (1, 2, 0, 2, 1, 0):
                before = time.perf_counter()
                shell.show_segment(index)
                navigation_ms.append((time.perf_counter() - before) * 1000)
                assert shell.current_segment == index
                QTest.qWait(35)
            QTest.qWait(260)
            assert shell.stack._snapshot.pixmap.isNull()
            live.set_running(True)
            live.audio_source.showPopup()
            QTest.qWait(35)
            live.audio_source.hidePopup()
            live.set_health(HealthReport(HealthState.STARTING))
            QTest.qWait(70)
            live.set_download_progress(-1, "Preparing a one-time language download")
            QTest.qWait(70)
            live.set_download_progress(0.68, "Almost ready for your subtitles")
            QTest.qWait(220)
            shell.grab().save(str(args.output / f"loading-{mode}.png"))
            live.hide_download_progress()
            live.set_health(HealthReport(HealthState.LISTENING, level=0.15))
            segment = CaptionSegment(text="A little hello from Mochi.", language="en")
            live.add_segment(segment)
            block = live._segment_blocks[segment.id]
            live.update_segment(
                segment.with_translation("Un petit bonjour de Mochi.", "fr", "Local",
                                         PrivacyTier.ON_DEVICE)
            )
            assert live._segment_blocks[segment.id] is block
            overlay.show_segment(segment)
            overlay._pill.reveal()
            overlay._pill.conceal()
            overlay._pill.reveal()
            QTest.qWait(260)
            shell.grab().save(str(args.output / f"live-{mode}.png"))
            live.banner.show_message("Your subtitles are ready.", "info")
            QTest.qWait(40)
            live.banner._dismiss.click()
            live.banner.show_message("A fresh notification", "info")
            QTest.qWait(220)
            assert live.banner.isVisible()
            shell.show_segment(2)
            settings.show_section("captions")
            QTest.qWait(260)
            custom = settings.captions.preset_cards.card(OverlayPreset.CUSTOM.value)
            QTest.mouseClick(custom, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            assert store.settings.overlay.preset is OverlayPreset.CUSTOM
            QTest.qWait(280)
            assert settings.captions.custom_reveal.expanded
            settings.show_section("general")
            QTest.qWait(260)
            shell.grab().save(str(args.output / f"settings-{mode}.png"))
            settings.general.reduce_motion.click()
            assert policy().reduced
            shell.show_segment(0)
            assert shell.stack._snapshot.pixmap.isNull()
            assert live.activity._cycle.state() == QAbstractAnimation.State.Stopped
            settings.general.reduce_motion.click()
            live.clear_segments()
            live.set_running(False)
        wizard = OnboardingWizard(store.settings, capability=capability, devices=[])
        wizard.show()
        for index in (1, 3, 2, 5, 6, 0):
            wizard._show_step(index)
            assert wizard.current_index == index
            QTest.qWait(35)
        QTest.qWait(260)
        wizard.grab().save(str(args.output / "onboarding.png"))
        wizard.close()
        shell.show_segment(0)
        live.set_health(HealthReport(HealthState.STARTING))
        shell.hide()
        assert live.activity._cycle.state() == QAbstractAnimation.State.Stopped
        assert live.progress._cycle.state() == QAbstractAnimation.State.Stopped
        assert shell.stack._snapshot.pixmap.isNull()
        overlay.close()
        shell.close()
        sessions.close()
    assert not paint_errors, paint_errors
    result = {
        "platform": app.platformName(),
        "scale_factor": os.environ.get("QT_SCALE_FACTOR", "system"),
        "device_pixel_ratio": shell.devicePixelRatioF(),
        "navigation_median_ms": round(statistics.median(navigation_ms), 2),
        "navigation_max_ms": round(max(navigation_ms), 2),
        "paint_errors": len(paint_errors),
        "result": "passed",
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
