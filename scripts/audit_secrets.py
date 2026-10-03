"""Run the hash-pinned secret scanner over released files and all reachable history."""

import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def scanner() -> Path:
    target = ROOT / "build" / "tools" / "gitleaks"
    binary = target / ("gitleaks.exe" if sys.platform == "win32" else "gitleaks")
    if binary.exists():
        return binary
    system = "windows" if sys.platform == "win32" else "darwin"
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    suffix = "zip" if system == "windows" else "tar.gz"
    config = json.loads((ROOT / "packaging" / "tools.json").read_text())["gitleaks"]
    archive = config["archives"][f"gitleaks_{config['version']}_{system}_{arch}.{suffix}"]
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=True) as client:
        response = client.get(archive["url"])
        response.raise_for_status()
        raw = response.content
    if hashlib.sha256(raw).hexdigest() != archive["sha256"]:
        raise ValueError("The secret scanner archive failed its integrity check.")
    target.mkdir(parents=True, exist_ok=True)
    if suffix == "zip":
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            content = bundle.read(binary.name)
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
            content = bundle.extractfile(binary.name).read()
    binary.write_bytes(content)
    binary.chmod(0o700)
    return binary


def check(binary: Path, mode: str, path: Path, name: str) -> bool:
    output = ROOT / "build" / f"gitleaks-{name}.json"
    command = [str(binary), mode, str(path), "--redact=100", "--no-banner",
               "--ignore-gitleaks-allow", "--report-format=json", f"--report-path={output}",
               "--timeout=180"]
    if mode == "git":
        command.append("--log-opts=--all")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=200)
    # Scanner diagnostics may contain matches or local paths; only print counts.
    findings = json.loads(output.read_text()) if output.exists() else []
    print(f"Secret scan {name}: {len(findings)} findings; status {result.returncode}.")
    return result.returncode == 0


def main() -> int:
    binary = scanner()
    files = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).split(b"\0")
    with tempfile.TemporaryDirectory(prefix="source-audit-") as scratch:
        tree = Path(scratch)
        for raw in set(files):
            if not raw:
                continue
            relative = Path(raw.decode("utf-8"))
            source = ROOT / relative
            if source.is_symlink():
                raise ValueError("A released source file cannot be a symbolic link.")
            if source.is_file():
                target = tree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        current = check(binary, "dir", tree, "tree")
    history = check(binary, "git", ROOT, "history")
    return 0 if current and history else 1


if __name__ == "__main__":
    raise SystemExit(main())
