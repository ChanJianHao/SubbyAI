"""Release gates reject an old, dirty, or incomplete packaged application."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from subbyai import branding
from subbyai.build_info import get_build_info

CURRENT_WINDOWS_VERSION = (*map(int, branding.VERSION.split(".")), 0)


def test_build_identity_distinguishes_clean_revision_from_missing_metadata(monkeypatch):
    metadata = SimpleNamespace(
        VERSION=branding.VERSION, BUILT_AT_UTC="2026-10-12T10:00:00+00:00",
        COMMIT="a" * 40, SOURCE_DIRTY=False,
    )
    monkeypatch.setitem(sys.modules, "subbyai_build_info", metadata)
    build = get_build_info()
    assert build.commit == "a" * 40 and not build.source_dirty
    del metadata.SOURCE_DIRTY
    assert get_build_info().source_dirty
    metadata.COMMIT = "invalid"
    assert not get_build_info().commit


@pytest.mark.parametrize("identity,finished,code,expected", [
    ("a" * 40 + " (clean)", True, 0, 0),
    ("b" * 40 + " (clean)", True, 0, 1),
    ("a" * 40 + " (dirty)", True, 0, 1),
    ("a" * 40 + " (clean)", False, 0, 1),
    ("a" * 40 + " (clean)", True, 1, 1),
])
def test_package_verification_requires_matching_clean_revision_and_completion(
    monkeypatch, identity, finished, code, expected,
):
    path = Path(__file__).resolve().parents[1] / "scripts/verify_package.py"
    spec = importlib.util.spec_from_file_location("verify_package", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stdout = "Build source: " + identity + ("\nSELF-CHECK OK\n" if finished else "\n")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=stdout, stderr="", returncode=code,
    ))
    monkeypatch.setattr(sys, "argv", [
        "verify_package", "--executable", "candidate.exe", "--expected-commit", "a" * 40,
    ])
    assert module.main() == expected


@pytest.mark.parametrize("file_version,product_version,passes", [
    (CURRENT_WINDOWS_VERSION, CURRENT_WINDOWS_VERSION, True),
    ((0, 0, 0, 0), CURRENT_WINDOWS_VERSION, False),
    (CURRENT_WINDOWS_VERSION, (0, 0, 0, 0), False),
    ((1, 0, 0, 0), (1, 0, 0, 0), False),
])
def test_release_gate_checks_both_binary_versions(
    monkeypatch, file_version, product_version, passes,
):
    path = Path(__file__).resolve().parents[1] / "scripts/check_release_version.py"
    spec = importlib.util.spec_from_file_location("check_release_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "read_windows_versions", lambda path: (file_version, product_version),
    )
    monkeypatch.setattr(sys, "argv", [
        "check_release_version", "--windows-installer", "candidate.exe",
    ])
    assert module.main() == (0 if passes else 1)


def test_release_gate_rejects_missing_compiled_version_resource(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/check_release_version.py"
    spec = importlib.util.spec_from_file_location("check_release_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def absent(path):
        raise ValueError("Missing version resource")

    monkeypatch.setattr(module, "read_windows_versions", absent)
    monkeypatch.setattr(sys, "argv", [
        "check_release_version", "--windows-installer", "candidate.exe",
    ])
    assert module.main() == 1
