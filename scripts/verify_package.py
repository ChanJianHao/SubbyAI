"""Run fixed Qt, recognition and translation checks inside the frozen binary.

    python scripts/verify_package.py --require-models
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = [
    ROOT / "dist" / "SubbyAI" / "SubbyAI.exe",
    ROOT / "dist" / "SubbyAI.app" / "Contents" / "MacOS" / "SubbyAI",
    ROOT / "dist" / "SubbyAI" / "SubbyAI",
]


def main() -> int:
    app = next((path for path in CANDIDATES if path.exists()), None)
    if app is None:
        print("Build the app first — no packaged binary found in dist/")
        return 1

    print(f"Checking {app}")
    try:
        result = subprocess.run(
            [str(app), "--self-check", *sys.argv[1:]],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        for output in (exc.stdout, exc.stderr):
            if output:
                print(output.decode(errors="replace") if isinstance(output, bytes) else output)
        print("PACKAGE: BROKEN (the self-check did not finish — is the flag supported?)")
        return 1

    print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    ok = result.returncode == 0
    print("PACKAGE:", "OK" if ok else "BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
