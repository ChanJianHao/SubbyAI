"""Stage the pinned recognizer with file decoding imported only when requested.

SubbyAI supplies captured NumPy frames; frozen builds do not distribute PyAV/FFmpeg.
The source environment retains faster-whisper's optional file-decoding dependency.
"""

import ast
import hashlib
import importlib.metadata
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def lazy_audio(source: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    insertions = []
    for node in tree.body:
        if isinstance(node, ast.Import) and any(alias.name == "av" for alias in node.names):
            lines[node.lineno - 1] = ""
        elif isinstance(node, ast.FunctionDef) and any(
            isinstance(child, ast.Name) and child.id == "av" for child in ast.walk(node)
        ):
            first = node.body[0]
            after_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            line = first.end_lineno if after_doc else first.lineno - 1
            insertions.append(line)
    if len(insertions) != 3:
        raise ValueError("The recognizer's audio API changed; review the build patch.")
    for line in reversed(insertions):
        lines.insert(line, "    import av\n\n")
    return "".join(lines)


def prepare() -> Path:
    distribution = importlib.metadata.distribution("faster-whisper")
    manifest = json.loads((ROOT / "packaging" / "whisper-audio.json").read_text())
    source = Path(distribution.locate_file("faster_whisper"))
    audio = (source / "audio.py").read_text(encoding="utf-8")
    if (
        distribution.version != manifest["version"]
        or hashlib.sha256(audio.encode()).hexdigest() != manifest["upstream_sha256"]
    ):
        raise ValueError("The pinned recognizer source does not match its reviewed build input.")
    target = ROOT / "build" / "freeze-input" / "faster_whisper"
    target.mkdir(parents=True, exist_ok=True)
    for file in source.glob("*.py"):
        shutil.copyfile(file, target / file.name)
    (target / "audio.py").write_text(lazy_audio(audio), encoding="utf-8", newline="\n")
    return target.parent


if __name__ == "__main__":
    prepare()
    print("Prepared the verified NumPy recognition build input.")
