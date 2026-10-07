"""Launch and gracefully close the packaged Windows app with an isolated profile.

Uses platformdirs' supported folder overrides, a Unicode working directory, and
a PATH containing only Windows system folders. Never changes the user's profile
or sends a close message to a window owned by another process.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subbyai.branding import APP_FULL_NAME, APP_ID, APP_NAME  # noqa: E402
from subbyai.core.settings import SCHEMA_VERSION  # noqa: E402


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This check requires Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", default="1")
    parser.add_argument("--executable", type=Path, default=ROOT / "dist/SubbyAI/SubbyAI.exe")
    parser.add_argument("--first-run", action="store_true")
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        raise SystemExit("Build the app before checking its launch.")
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    with tempfile.TemporaryDirectory(prefix="subbyai-字幕-") as directory:
        scratch = Path(directory)
        roaming, local = scratch / "Roaming", scratch / "Local"
        profile = roaming / APP_ID
        profile.mkdir(parents=True)
        (profile / "settings.json").write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "general": {
                        "onboarding_complete": not args.first_run, "close_to_tray": False,
                    },
                    "history": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        windows = Path(environment.get("WINDIR", "C:/Windows"))
        environment["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
        for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            environment.pop(name, None)
        environment.update(
            {
                "WIN_PD_OVERRIDE_APPDATA": str(roaming),
                "WIN_PD_OVERRIDE_LOCAL_APPDATA": str(local),
                "QT_QPA_PLATFORM": "windows",
                "QT_SCALE_FACTOR": args.scale,
                "PYTHONNOUSERSITE": "1",
                "HF_HUB_OFFLINE": "1",
            }
        )
        process = subprocess.Popen([str(executable)], cwd=scratch, env=environment)
        main_window = None
        setup_window = None
        window_titles: set[str] = set()

        @callback_type
        def find_window(handle, _parameter):
            nonlocal main_window, setup_window
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == process.pid:
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(handle, title, len(title))
                if title.value:
                    window_titles.add(title.value)
                if title.value in {APP_FULL_NAME, f"{APP_FULL_NAME} - {APP_NAME}"}:
                    main_window = handle
                if title.value == f"Set up {APP_NAME}":
                    setup_window = handle
            return True

        try:
            deadline = time.monotonic() + 25
            while main_window is None and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("The packaged app exited before opening its main window.")
                user32.EnumWindows(find_window, 0)
                time.sleep(0.1)
            if main_window is None:
                raise RuntimeError("The packaged app did not open its main window.")
            time.sleep(2)  # Allow initialization and first paints to complete.
            if process.poll() is not None:
                raise RuntimeError("The packaged app failed during initialization.")
            if args.first_run:
                deadline = time.monotonic() + 25
                while setup_window is None and time.monotonic() < deadline:
                    user32.EnumWindows(find_window, 0)
                    time.sleep(0.1)
                if setup_window is None:
                    raise RuntimeError("The first-run setup did not appear.")
                user32.PostMessageW(setup_window, 0x0010, 0, 0)
                time.sleep(0.5)
            user32.PostMessageW(main_window, 0x0010, 0, 0)
            if process.wait(timeout=20) != 0:
                raise RuntimeError("The packaged app did not close cleanly.")
            for logfile in scratch.rglob("*.log"):
                text = logfile.read_text(encoding="utf-8", errors="replace")
                if "Traceback" in text or "Unexpected failure" in text:
                    raise RuntimeError("The isolated launch recorded a runtime failure.")
        except Exception:
            # Keep actionable diagnostics even though the test profile is temporary.
            output = ROOT / "build" / "windows-launch-failure.txt"
            output.parent.mkdir(exist_ok=True)
            logs = [
                file.read_text(encoding="utf-8", errors="replace")
                for file in scratch.rglob("*.log")
            ]
            output.write_text(
                json.dumps(sorted(window_titles), ensure_ascii=False) + "\n" + "\n".join(logs),
                encoding="utf-8",
            )
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    print(
        json.dumps(
            {
                "launch": "passed",
                "scale": args.scale,
                "unicode_profile": True,
                "clean_path": True,
                "graceful_exit": True,
                "first_run": args.first_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
