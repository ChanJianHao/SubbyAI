"""The hardware probe must be cheap and must not be repeated needlessly.

Probing for a graphics card loads the CUDA runtime, which measured ~290 MB
resident when done in-process. It now runs in a subprocess whose result is
cached on disk, so the app pays neither the memory nor the startup cost.
"""

from __future__ import annotations

import json

import pytest

from subbyai.asr import capability


@pytest.fixture(autouse=True)
def _reset_cache():
    capability._cache = None
    yield
    capability._cache = None


def test_probe_result_is_cached_on_disk(monkeypatch):
    calls = []

    def fake_probe():
        calls.append(1)
        return capability.MachineCapability(
            has_cuda=True,
            gpu_name="Fake GPU",
            vram_gb=8.0,
            cpu_cores=8,
            ram_gb=16.0,
            free_disk_gb=100.0,
            platform="win32",
        )

    monkeypatch.setattr(capability, "_probe", fake_probe)
    monkeypatch.setattr(capability, "sys", type("s", (), {"platform": "win32"}))

    first = capability.detect()
    assert first.gpu_name == "Fake GPU"
    assert len(calls) == 1

    # A fresh process (in-memory cache cleared) must reuse the stored answer.
    capability._cache = None
    second = capability.detect()
    assert second.gpu_name == "Fake GPU"
    assert len(calls) == 1, "the expensive probe must not run again"


def test_force_refresh_reprobes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        capability,
        "_probe",
        lambda: (calls.append(1), capability.MachineCapability(cpu_cores=4))[1],
    )
    capability.detect()
    capability.detect(force_refresh=True)
    assert len(calls) == 2


def test_corrupt_cache_is_ignored(monkeypatch):
    capability._cache_file().write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(capability, "_probe", lambda: capability.MachineCapability(cpu_cores=2))
    assert capability.detect().cpu_cores == 2


def test_cache_from_another_platform_is_ignored(monkeypatch):
    capability._cache_file().write_text(
        json.dumps({"version": 1, "platform": "some-other-os", "cpu_cores": 99}),
        encoding="utf-8",
    )
    monkeypatch.setattr(capability, "_probe", lambda: capability.MachineCapability(cpu_cores=4))
    assert capability.detect().cpu_cores == 4


def test_cuda_probe_runs_out_of_process(monkeypatch):
    """Guards the memory fix: ctranslate2 must not be imported by the app."""
    import subprocess

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return type("R", (), {"stdout": "1\n"})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert capability._cuda_device_count() == 1
    assert seen["command"][0].endswith(("python.exe", "python", "python3")) or seen["command"][
        1:2
    ] == ["--probe-cuda"]


def test_cuda_probe_failure_reads_as_no_gpu(monkeypatch):
    import subprocess

    def boom(*args, **kwargs):
        raise OSError("no such executable")

    monkeypatch.setattr(subprocess, "run", boom)
    assert capability._cuda_device_count() == 0
def test_cuda_search_handles_survive_and_frozen_package_root_is_searched(tmp_path, monkeypatch):
    import ctypes
    import site
    import sys
    from types import SimpleNamespace

    folder = tmp_path / "nvidia/cublas/bin"
    folder.mkdir(parents=True)
    opened = []
    flags = []

    class ConfigureSearch:
        def __call__(self, value):
            flags.append(value)
            return 1

    monkeypatch.setattr(
        ctypes, "WinDLL",
        lambda *_args, **_kwargs: SimpleNamespace(SetDefaultDllDirectories=ConfigureSearch()),
        raising=False,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(site, "getsitepackages", lambda: [])
    monkeypatch.setattr(capability, "_dll_handles", [])
    monkeypatch.setattr(capability, "_dll_directories", set())

    def add(path):
        handle = object()
        opened.append((path, handle))
        return handle

    monkeypatch.setattr(capability.os, "add_dll_directory", add, raising=False)
    capability._register_nvidia_wheel_dlls()
    capability._register_nvidia_wheel_dlls()
    assert len(opened) == 1
    assert capability._dll_handles == [opened[0][1]]
    assert flags == [0x1000, 0x1000]


def test_gpu_probe_requires_inference_libraries_not_just_a_driver(monkeypatch):
    import ctypes
    import sys
    from types import SimpleNamespace

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(capability, "_register_nvidia_wheel_dlls", lambda: None)
    monkeypatch.setitem(
        sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1)
    )

    def missing(_name):
        raise OSError("Missing inference runtime")

    monkeypatch.setattr(ctypes, "WinDLL", missing, raising=False)
    assert capability.probe_cuda_in_process() == 0
    monkeypatch.setattr(ctypes, "WinDLL", lambda _name: object())
    assert capability.probe_cuda_in_process() == 1
