"""Frozen multiprocessing helpers must never start another application window."""

import multiprocessing
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "packaging" / "launcher.py"


def test_helper_dispatch_exits_before_application_start(monkeypatch):
    started = []

    def helper_dispatch():
        raise SystemExit(0)

    monkeypatch.setattr(multiprocessing, "freeze_support", helper_dispatch)
    monkeypatch.setitem(
        sys.modules, "subbyai.__main__", SimpleNamespace(main=lambda: started.append(1))
    )
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(LAUNCHER), run_name="__main__")
    assert caught.value.code == 0
    assert not started
