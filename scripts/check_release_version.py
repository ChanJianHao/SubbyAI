"""Fail release builds when tag, project metadata and app version disagree."""

import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subbyai.branding import VERSION  # noqa: E402

project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
tag = os.environ.get("GITHUB_REF_NAME", "")
tag_mismatch = os.environ.get("GITHUB_REF_TYPE") == "tag" and tag != "v" + project["version"]
if tag_mismatch or project["version"] != VERSION:
    raise SystemExit("Release tag, pyproject.toml and app version must agree.")
print("Release version is consistent.")
