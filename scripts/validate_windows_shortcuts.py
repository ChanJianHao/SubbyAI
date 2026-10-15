"""Exercise real Win32 registration, live editing, competition and restart.

No audio capture or synthetic keyboard input. Run on Windows; native keyboard
acceptance additionally exercises physical input through the visible app.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from subbyai.core.settings import SettingsStore  # noqa: E402
from subbyai.core.shortcuts import default_bindings  # noqa: E402
from subbyai.system.hotkeys import HotkeyManager, is_valid_global  # noqa: E402
from subbyai.system.shortcut_settings import ShortcutSettingsController  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/windows-shortcuts.json")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Real Windows hotkey validation requires Windows.")
    app = QApplication.instance() or QApplication([])
    manager, competitor = HotkeyManager(), HotkeyManager()
    checks = []
    try:
        with tempfile.TemporaryDirectory(prefix="subbyai-shortcuts-") as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            # Avoid user-configured default chords on a developer's desktop.
            empty = dict.fromkeys(default_bindings(), "")
            original = empty | {"toggle_captions": "Ctrl+Alt+Shift+F9"}
            replacement = empty | {"toggle_captions": "Ctrl+Alt+Shift+F10"}
            store.settings.shortcuts.globals = original
            runtime = ShortcutSettingsController(store, manager)
            assert runtime.reload() == {}, runtime.failures
            assert manager.registered_bindings == {"toggle_captions": "Ctrl+Alt+Shift+F9"}
            checks.append("saved bindings registered with Win32")
            assert competitor.register_all({"occupied": "Ctrl+Alt+Shift+F10"}) == {}
            runtime.apply(replacement)
            assert runtime.bindings == original
            assert "wasn't saved" in runtime.failures["toggle_captions"]
            assert manager.registered_bindings == {"toggle_captions": "Ctrl+Alt+Shift+F9"}
            checks.append("real external collision rolls back registration and settings")
            competitor.unregister_all()
            runtime.retry()
            assert runtime.failures == {}
            assert manager.registered_bindings == {"toggle_captions": "Ctrl+Alt+Shift+F10"}
            checks.append("real recovery and retry")
            runtime.set_suspended(True)
            assert not manager.registered_bindings
            assert competitor.register_all({"probe": "Ctrl+Alt+Shift+F10"}) == {}
            competitor.unregister_all()
            runtime.set_suspended(False)
            assert manager.registered_bindings == {"toggle_captions": "Ctrl+Alt+Shift+F10"}
            checks.append("capture releases OS registrations and restores them")
            for _ in range(12):
                assert manager.register_all(runtime.bindings) == {}
            assert competitor.register_all({"probe": "Ctrl+Alt+Shift+F10"})
            checks.append("repeated registration has one OS owner")
            manager.unregister_all()
            restarted = SettingsStore(Path(directory) / "settings.json")
            assert restarted.settings.shortcuts.globals == runtime.bindings
            assert manager.register_all(restarted.settings.shortcuts.globals) == {}
            checks.append("restart loads and registers exact saved keys")
            duplicate = manager.register_all(
                {"one": "Ctrl+Alt+Shift+F9", "two": "alt+shift+ctrl+f9"}
            )
            assert set(duplicate) == {"two"}
            assert manager.registered_bindings == {"one": "Ctrl+Alt+Shift+F9"}
            checks.append("canonical duplicates cannot register twice")
            for sequence in (
                "Ctrl+Alt+Delete",
                "Alt+Tab",
                "Win+L",
                "Ctrl+F12",
                "J",
                "Shift+J",
                "Ctrl+A+B",
                "Numpad+1",
                "AltGr+Q",
            ):
                assert not is_valid_global(sequence)[0], sequence
            checks.append("reserved and unsupported combinations rejected")
            runtime.apply(empty)
            assert not manager.registered_bindings
            assert (
                SettingsStore(Path(directory) / "settings.json").settings.shortcuts.globals == empty
            )
            assert competitor.register_all({"probe": "Ctrl+Alt+Shift+F9"}) == {}
            checks.append("clear persists and releases Windows ownership")
    finally:
        manager.unregister_all()
        competitor.unregister_all()
        QCoreApplication.processEvents()
    result = {"result": "passed", "platform": app.platformName(), "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
