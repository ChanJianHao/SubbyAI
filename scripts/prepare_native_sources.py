"""Collect hash-pinned corresponding Qt/PySide source beside release binaries."""

import hashlib
import json
import time
import tomllib
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "packaging" / "native-sources.json").read_text())
    folder = ROOT / "build" / "native-sources"
    folder.mkdir(parents=True, exist_ok=True)
    for entry in manifest["sources"]:
        path = folder / entry["filename"]
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]:
            continue
        temporary = path.with_suffix(".partial")
        try:
            deadline, size = time.monotonic() + 900, 0
            with (
                httpx.Client(timeout=30, trust_env=False, follow_redirects=True) as client,
                client.stream("GET", entry["url"]) as response,
                temporary.open("wb") as stream,
            ):
                response.raise_for_status()
                for block in response.iter_bytes():
                    size += len(block)
                    if size > 256 * 1024**2 or time.monotonic() > deadline:
                        raise ValueError("The source archive exceeded its download limit.")
                    stream.write(block)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("The native source archive failed its integrity check.")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    destination = ROOT / "dist" / f"SubbyAI-native-sources-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for entry in manifest["sources"]:
            archive.write(folder / entry["filename"], entry["filename"])
        archive.write(ROOT / "packaging" / "native-sources.json", "manifest.json")
        archive.write(ROOT / "docs" / "native-libraries.md", "BUILDING.md")
    print("Verified and assembled the corresponding Qt/PySide source distribution.")


if __name__ == "__main__":
    main()
