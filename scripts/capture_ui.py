"""Render real Qt surfaces using fictional content and isolated temporary data."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from subbyai.audio.base import AudioDevice  # noqa: E402
from subbyai.core.events import CaptionSegment, PrivacyTier  # noqa: E402
from subbyai.core.settings import SettingsStore  # noqa: E402
from subbyai.ui import theme  # noqa: E402
from subbyai.ui.live_view import LiveView  # noqa: E402
from subbyai.ui.settings_view import SettingsDeps, SettingsView  # noqa: E402
from subbyai.ui.shell import Shell  # noqa: E402


def main():
    app = QApplication([])
    if sys.platform == "win32":
        from PySide6.QtGui import QFontDatabase

        for filename in ("segoeui.ttf", "segoeuib.ttf", "YuGothR.ttc", "msyh.ttc"):
            QFontDatabase.addApplicationFont(
                str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename)
            )
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = SettingsStore(Path(directory) / "settings.json")
        store.settings.captions.source_language = "ja"
        store.settings.captions.target_language = "en"
        shell = Shell(store.settings)
        live = LiveView(store.settings)
        deps = SettingsDeps(
            audio_devices=lambda: [AudioDevice("demo", "My headphones", 48000, 2, True, True)]
        )
        settings = SettingsView(store, deps)
        shell.add_surface(live)
        shell.add_surface(QWidget())
        shell.add_surface(settings)
        shell.resize(940, 700)
        shell.show()
        segment = CaptionSegment(text="今日は、どんな物語を見よう？", language="ja")  # noqa: RUF001
        live.add_segment(
            segment.with_translation(
                "What story shall we watch today?",
                "en",
                "Built-in",
                PrivacyTier.ON_DEVICE,
            )
        )
        for mode in ("dark", "light"):
            theme.apply(app, mode, "sakura")
            settings.refresh_theme()
            shell.show_segment(0)
            app.processEvents()
            live._render_segments()
            app.processEvents()
            shell.grab().save(str(output / f"live-{mode}.png"))
            shell.show_segment(2)
            app.processEvents()
            shell.grab().save(str(output / f"settings-{mode}.png"))
        if "--measure" in sys.argv:
            shell.resize(640, 460)
            app.processEvents()
            print("shell", shell.minimumSizeHint().width())
            for child in settings.everyday.findChildren(QWidget):
                if child.minimumSizeHint().width() > 350:
                    print(
                        type(child).__name__,
                        child.objectName(),
                        child.minimumSizeHint().width(),
                        child.text()[:70] if hasattr(child, "text") else "",
                    )
        shell.close()
    print("Rendered Live and Everyday settings in both themes.")


if __name__ == "__main__":
    main()
