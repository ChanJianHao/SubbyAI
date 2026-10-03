"""Freeze the app with a Windows PATH free of unrelated developer DLLs."""

import os
import subprocess
import sys
from pathlib import Path

from stage_whisper import prepare

ROOT = Path(__file__).resolve().parents[1]
prepare()
env = dict(os.environ)
if sys.platform == "win32":
    windows = Path(env.get("WINDIR", "C:/Windows"))
    env["PATH"] = os.pathsep.join(
        str(path)
        for path in (
            Path(sys.executable).parent,
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "DLLs",
            windows / "System32",
            windows,
        )
    )
raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "packaging/pyinstaller.spec",
            "--noconfirm",
            *sys.argv[1:],
        ],
        cwd=ROOT,
        env=env,
    )
)
