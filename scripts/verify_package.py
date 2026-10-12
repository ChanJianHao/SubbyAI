"""Run fixed Qt, recognition and translation checks inside the frozen binary.

    python scripts/verify_package.py --require-models
"""

from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--expected-commit", help="Require this exact clean source revision")
    options, forwarded = parser.parse_known_args()
    app = options.executable or next((path for path in CANDIDATES if path.exists()), None)
    if app is None:
        print("Build the app first — no packaged binary found in dist/")
        return 1

    print(f"Checking {app}")
    try:
        result = subprocess.run(
            [str(app), "--self-check", *forwarded],
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
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
    reported = any(line.startswith("SELF-CHECK OK") for line in result.stdout.splitlines())
    ok = result.returncode == 0 and reported
    if options.expected_commit:
        identity = f"Build source: {options.expected_commit} (clean)"
        if identity not in result.stdout.splitlines():
            print("PACKAGE: source revision mismatch or uncommitted build input")
            ok = False
    if not reported:
        print("PACKAGE: missing diagnostic completion report")
    print("PACKAGE:", "OK" if ok else "BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
