"""Windows code pages must not corrupt or fail the packaged diagnostic protocol."""

import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

from subbyai import self_check


def test_frozen_diagnostics_emit_utf8_from_an_ansi_console(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    for name in (
        "_check_imports",
        "_check_build_info",
        "_check_ui",
        "_check_shortcuts",
        "_check_ctranslate2",
        "_check_onnx_runtime",
        "_check_recognition",
        "_check_parakeet",
    ):
        monkeypatch.setattr(self_check, name, lambda *args: [])

    def translation(_skipped):
        print("translated en->ko: 안녕하세요 · ✓")
        return []

    monkeypatch.setattr(self_check, "_check_translation", translation)
    assert self_check.run_self_check(require_models=True) == 0
    output = raw.getvalue().decode("utf-8")
    assert "안녕하세요 · ✓" in output
    assert "SELF-CHECK OK" in output


def test_package_verifier_preserves_unicode_on_ansi_stdout_and_stderr(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/verify_package.py"
    spec = importlib.util.spec_from_file_location("verify_package_unicode", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output, errors = io.BytesIO(), io.BytesIO()
    stdout = io.TextIOWrapper(output, encoding="cp1252", write_through=True)
    stderr = io.TextIOWrapper(errors, encoding="cp1252", write_through=True)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="Build identity: · ✓\nSELF-CHECK OK\n",
            stderr="진단 ✓",
            returncode=0,
        ),
    )
    monkeypatch.setattr(sys, "argv", ["verify_package", "--executable", "字幕.exe"])
    assert module.main() == 0
    assert "Build identity: · ✓" in output.getvalue().decode("utf-8")
    assert "PACKAGE: OK" in output.getvalue().decode("utf-8")
    assert "진단 ✓" in errors.getvalue().decode("utf-8")
