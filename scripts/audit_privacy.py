"""Scan worktree, reachable Git blobs and optional artifacts without printing secrets."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "private-key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n"
        rb"[A-Za-z0-9+/=\r\n]{64,}-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "service-token": re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{30,}|"
        rb"AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{30,})\b"
    ),
    "credential-url": re.compile(
        rb"https?://[A-Za-z0-9._~%+-]{1,80}:[A-Za-z0-9._~%+-]{8,160}"
        rb"@(?!example\.(?:com|org)|localhost)"
    ),
}
HOME_PATH = re.compile(
    rb"(?i)(?:[a-z]:[\\/]{1,2}(?:users|documents and settings)[\\/]{1,2}"
    rb"|/(?:users|home)/)([a-z0-9._-]{2,})"
)
GENERIC_ACCOUNTS = {
    b"example", b"user", b"username", b"testuser", b"runner", b"runneradmin", b"build"
}


def repository_path_findings(relative, location):
    path = Path(relative)
    if (
        (path.name.lower().startswith(".env") and path.name != ".env.example")
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".db", ".dmp", ".log"}
        or any(
            part in {".venv", "__pycache__", ".codex", ".agents", "node_modules"}
            for part in path.parts
        )
    ):
        return [{"location": location, "rule": "private-or-generated-file"}]
    return []


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def private_markers():
    names = {os.environ.get("USERNAME", ""), getpass.getuser(), socket.gethostname()}
    # Hosted CI accounts also occur inside public upstream wheel metadata.
    # These generic service names are not a private person's identity.
    generic = {
        "runner", "runneradmin", "root", "user", "username", "administrator", "build", "testuser"
    }
    return {
        name.lower().encode() for name in names if len(name) > 3 and name.lower() not in generic
    }


def scan(data, location, markers):
    findings = [
        {"location": location, "rule": rule}
        for rule, pattern in RULES.items()
        if pattern.search(data)
    ]
    lower = data.lower().replace(b"\x00", b"")
    if not (location.startswith("artifact:") and "icudt" in location) and any(
        re.search(rb"(?<![a-z0-9_])" + re.escape(marker) + rb"(?![a-z0-9_])", lower)
        for marker in markers
    ):
        findings.append({"location": location, "rule": "current-machine-identity"})
    if location.startswith(("worktree:", "history:", "binary:")) and any(
        match.group(1).lower() not in GENERIC_ACCOUNTS
        for match in HOME_PATH.finditer(data)
    ):
        findings.append({"location": location, "rule": "private-home-path"})
    return findings


def inspect_archive(path, markers):
    """Inspect decompressed Python code filenames, including nested code objects."""
    import types

    from PyInstaller.archive.readers import CArchiveReader

    findings = []

    def code_tree(code, location):
        if isinstance(code, types.CodeType):
            findings.extend(scan(code.co_filename.encode(), location, markers))
            for value in code.co_consts:
                code_tree(value, location)

    reader = CArchiveReader(str(path))
    for name in reader.toc:
        if name.endswith(".pyz"):
            pyz = reader.open_embedded_archive(name)
            for module in pyz.toc:
                code_tree(pyz.extract(module), f"binary:{path.name}:{module}")
        else:
            data = reader.extract(name)
            if data:
                findings.extend(scan(data, f"binary:{path.name}:{name}", markers))
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "privacy-audit.json")
    args = parser.parse_args()
    markers, findings = private_markers(), []
    files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    seen = set()
    for file in files:
        if not file or file in seen:
            continue
        seen.add(file)
        relative = file.decode("utf-8", errors="replace")
        path = ROOT / relative
        if path.is_file():
            findings.extend(repository_path_findings(relative, f"worktree:{relative}"))
            findings.extend(scan(relative.encode(), f"worktree:{relative}:name", markers))
            findings.extend(scan(path.read_bytes(), f"worktree:{relative}", markers))
    blob_count, commits = 0, 0
    if args.history:
        commits = int(git("rev-list", "--count", "--all"))
        objects = git("rev-list", "--objects", "--all").splitlines()
        process = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        try:
            for entry in objects:
                oid, _, name = entry.partition(b" ")
                process.stdin.write(oid + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().split()
                data = process.stdout.read(int(header[2]))
                process.stdout.read(1)
                if header[1] == b"blob":
                    blob_count += 1
                    location = f"history:{oid.decode()[:12]}:{name.decode(errors='replace')}"
                    findings.extend(
                        repository_path_findings(name.decode(errors="replace"), location)
                    )
                    findings.extend(scan(name, location + ":name", markers))
                    findings.extend(scan(data, location, markers))
                    if name.lower().endswith((b".wav", b".mp3", b".flac", b".db", b".dmp")):
                        findings.append({"location": location, "rule": "historical-user-data"})
                elif header[1] in {b"commit", b"tag"}:
                    location = f"{header[1].decode()}:{oid.decode()[:12]}"
                    findings.extend(scan(data, location, markers))
                    for contact in re.findall(rb"^(?:author|committer) .* <([^>]+)>", data, re.M):
                        if not contact.endswith(b"users.noreply.github.com"):
                            findings.append({"location": location, "rule": "personal-contact"})
        finally:
            process.stdin.close()
            process.wait(timeout=10)
    artifact_files = 0
    if args.artifacts:
        for path in args.artifacts.rglob("*"):
            if not path.is_file():
                continue
            artifact_files += 1
            relative = path.relative_to(args.artifacts).as_posix()
            with path.open("rb") as stream:
                previous = b""
                while data := stream.read(1024**2):
                    findings.extend(scan(previous + data, f"artifact:{relative}", markers))
                    previous = data[-256:]
            if path.name in {"SubbyAI.exe", "SubbyAI"}:
                findings.extend(inspect_archive(path, markers))
            if path.suffix.lower() in {".pdb", ".dmp", ".log"} or path.name == "direct_url.json":
                findings.append(
                    {"location": f"artifact:{relative}", "rule": "development-artifact"}
                )
    findings = [dict(item) for item in {tuple(item.items()) for item in findings}]
    findings.sort(key=lambda item: (item["location"], item["rule"]))
    report = {
        "working_files": len(seen),
        "reachable_commits": commits,
        "unique_history_blobs": blob_count,
        "artifact_files": artifact_files,
        "findings": findings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: len(value) if key == "findings" else value for key, value in report.items()}
        )
    )
    for finding in findings[:25]:
        print(f"REVIEW {finding['rule']} {finding['location']}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
