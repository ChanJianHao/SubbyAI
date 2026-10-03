"""The version number must agree everywhere it appears.

pyproject.toml is the source of truth; branding.py carries a copy for the
frozen app, and the packaging scripts read pyproject at build time. This test
fails the build the moment someone bumps one and not the other - which would
otherwise ship an installer whose name disagrees with what the app tells the
user they are running.
"""

import tomllib
from pathlib import Path

from subbyai import branding

ROOT = Path(__file__).resolve().parents[1]


def test_branding_version_matches_pyproject() -> None:
    with open(ROOT / "pyproject.toml", "rb") as f:
        pyproject_version = tomllib.load(f)["project"]["version"]
    assert pyproject_version == branding.VERSION
