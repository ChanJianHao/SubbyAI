"""Hardware capabilities, probed off the GUI thread and cached.

GPU probing runs in an isolated subprocess; a missing runtime or broken driver
degrades to CPU. Heavy dependencies are imported only when needed."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from dataclasses import asdict, dataclass, replace

from .. import paths
from ..core.settings import TIER_MODELS, QualityTier
from .models import estimated_size_mb, required_free_mb

log = logging.getLogger(__name__)

# A graphics card below this cannot hold the largest model plus its activations.
MAXIMUM_VRAM_GB = 6.0
DETAILED_RAM_GB = 8.0
BALANCED_RAM_GB = 4.0
BALANCED_CORES = 8

_cache: MachineCapability | None = None
_lock = threading.Lock()
_dll_handles: list[object] = []
_dll_directories: set[str] = set()


@dataclass(frozen=True, slots=True)
class MachineCapability:
    """A snapshot of the hardware, in the terms the tier rules care about."""

    has_cuda: bool = False
    gpu_name: str = ""
    vram_gb: float = 0.0
    """0.0 means unknown, not "none" — see ``tier_available``."""

    cpu_cores: int = 1
    """Logical processors, which is what decides throughput on CPU decoding."""

    ram_gb: float = 0.0
    free_disk_gb: float = 0.0
    platform: str = ""


def detect(force_refresh: bool = False) -> MachineCapability:
    """Probe the machine, reusing the previous answer unless asked not to.

    The answer is also cached on disk, so the GPU probe is paid once per machine
    rather than once per launch. Hardware changes (a new card, an eGPU) need
    ``force_refresh``, which Settings offers.
    """
    global _cache
    with _lock:
        if _cache is not None and not force_refresh:
            return _cache
        if not force_refresh:
            stored = _load_cached()
            if stored is not None:
                _cache = stored
                return _cache
        _cache = _probe()
        _store_cached(_cache)
        log.info(
            "Machine: cuda=%s gpu=%r vram=%.1fGB cores=%d ram=%.1fGB free=%.1fGB",
            _cache.has_cuda,
            _cache.gpu_name,
            _cache.vram_gb,
            _cache.cpu_cores,
            _cache.ram_gb,
            _cache.free_disk_gb,
        )
        return _cache


def recommended_tier(cap: MachineCapability | None = None) -> QualityTier:
    """A conservative starting point that leaves room for playback and games.

    Maximum remains an explicit choice. Hardware capacity is not a throughput
    benchmark; use real speech measurements before changing the CPU floors.
    """
    cap = cap or detect()
    if cap.has_cuda:
        return QualityTier.DETAILED if cap.vram_gb >= 4.0 else QualityTier.BALANCED
    if cap.cpu_cores >= BALANCED_CORES and cap.ram_gb >= DETAILED_RAM_GB:
        return QualityTier.BALANCED
    return QualityTier.QUICK


def tier_available(tier: QualityTier, cap: MachineCapability | None = None) -> tuple[bool, str]:
    """Whether a tier can run here, and if not, why — in the user's words.

    Reasons are shown verbatim next to a disabled option, so they carry no
    jargon and no numbers the user cannot act on. Unknown VRAM never blocks:
    we would rather let someone try MAXIMUM and fall back than lock them out of
    the hardware they paid for.
    """
    cap = cap or detect()
    if tier is QualityTier.MAXIMUM:
        if not cap.has_cuda:
            return False, "Needs a graphics card."
        if 0.0 < cap.vram_gb < MAXIMUM_VRAM_GB:
            return False, "Needs a graphics card with more memory."
    elif tier is QualityTier.DETAILED:
        if not cap.has_cuda and cap.ram_gb > 0.0 and cap.ram_gb < DETAILED_RAM_GB:
            return False, "Needs more memory than this computer has."
    elif tier is QualityTier.BALANCED and 0.0 < cap.ram_gb < BALANCED_RAM_GB:
        return False, "Needs more memory than this computer has."

    needed_gb = required_free_mb(TIER_MODELS[tier][0]) / 1024.0
    if cap.free_disk_gb > 0.0 and cap.free_disk_gb < needed_gb:
        return False, f"Needs about {needed_gb:.1f} GB of free space to download."
    return True, ""


def tier_download_mb(tier: QualityTier) -> int:
    """Download size to show beside a tier."""
    return estimated_size_mb(TIER_MODELS[tier][0])


# ---------- on-disk cache ----------

#: Bumped when the probe's meaning changes, so stale answers are ignored.
_CACHE_VERSION = 2


def _cache_file():
    return paths.config_dir() / "hardware.json"


def _load_cached() -> MachineCapability | None:
    import json

    try:
        raw = json.loads(_cache_file().read_text(encoding="utf-8"))
        if raw.get("version") != _CACHE_VERSION or raw.get("platform") != sys.platform:
            return None
        data = {k: v for k, v in raw.items() if k not in ("version",)}
        capability = MachineCapability(**data)
    except (OSError, ValueError, TypeError):
        return None
    # Free space changes constantly; everything else is stable hardware.
    return replace(capability, free_disk_gb=_probe_free_disk())


def _store_cached(capability: MachineCapability) -> None:
    import json

    try:
        payload = asdict(capability) | {"version": _CACHE_VERSION}
        _cache_file().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        log.debug("Could not cache hardware probe", exc_info=True)


# ---------- probes ----------


def _probe() -> MachineCapability:
    has_cuda, gpu_name, vram_gb = _probe_gpu()
    cores, ram_gb = _probe_cpu()
    return MachineCapability(
        has_cuda=has_cuda,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        cpu_cores=cores,
        ram_gb=ram_gb,
        free_disk_gb=_probe_free_disk(),
        platform=sys.platform,
    )


def _probe_gpu() -> tuple[bool, str, float]:
    if _cuda_device_count() <= 0:
        return False, "", 0.0
    name, vram_gb = _query_nvidia_smi()
    return True, name, vram_gb


def _cuda_device_count() -> int:
    """CUDA devices visible to CTranslate2, or 0 if anything at all goes wrong.

    A short-lived subprocess releases the CUDA runtime's memory after probing
    and isolates driver crashes from the application. Only the count is kept.
    """
    command = [sys.executable]
    if getattr(sys, "frozen", False):
        command.append("--probe-cuda")  # the packaged app re-execs itself
    else:
        command += ["-m", "subbyai.asr.capability"]
    try:
        import subprocess

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int((result.stdout or "0").strip().splitlines()[-1])
    except Exception:
        log.debug("CUDA probe failed", exc_info=True)
        return 0


def probe_cuda_in_process() -> int:
    """The body of the subprocess probe. Not called on the app's own path."""
    try:
        _register_nvidia_wheel_dlls()
        if sys.platform == "win32":
            import ctypes

            # A driver can enumerate a card while inference libraries are
            # absent. Report CPU capability instead of failing at first speech.
            for name in ("cublas64_12.dll", "cudnn64_9.dll"):
                ctypes.WinDLL(name)
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def _query_nvidia_smi() -> tuple[str, float]:
    """Card name and total VRAM, or ("", 0.0) when nvidia-smi is unavailable."""
    import subprocess

    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            **kwargs,  # type: ignore[arg-type]
        )
        line = result.stdout.strip().splitlines()[0]
        name, _, memory = line.partition(",")
        return name.strip(), float(memory.strip()) / 1024.0
    except Exception:
        log.debug("nvidia-smi query failed", exc_info=True)
        return "", 0.0


def _probe_cpu() -> tuple[int, float]:
    try:
        import psutil

        cores = psutil.cpu_count(logical=True) or os.cpu_count() or 1
        ram_gb = psutil.virtual_memory().total / 2**30
    except Exception:
        log.debug("CPU/RAM probe failed", exc_info=True)
        return os.cpu_count() or 1, 0.0
    return int(cores), float(ram_gb)


def _probe_free_disk() -> float:
    """Free space where models land, which is the only volume that matters."""
    try:
        return shutil.disk_usage(paths.models_dir()).free / 2**30
    except OSError:
        log.debug("Disk probe failed", exc_info=True)
        return 0.0


def _register_nvidia_wheel_dlls() -> None:
    """Make CUDA DLLs shipped in the nvidia-* pip wheels loadable on Windows."""
    if sys.platform != "win32":
        return
    import ctypes
    import site

    # Native LoadLibrary calls must honor AddDllDirectory, too. Restrict the
    # search to the application, explicitly registered directories and System32;
    # the working directory and arbitrary PATH entries are excluded.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetDefaultDllDirectories.argtypes = [ctypes.c_uint32]
    kernel32.SetDefaultDllDirectories.restype = ctypes.c_int
    if not kernel32.SetDefaultDllDirectories(0x00001000):
        log.warning("Could not configure the secure GPU library search path")
        return
    try:
        roots = [p for p in site.getsitepackages() if p]
    except AttributeError:  # pragma: no cover - embedded interpreters
        return
    if getattr(sys, "frozen", False):
        roots.append(sys._MEIPASS)
    for root in roots:
        nvidia = os.path.join(root, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for entry in os.listdir(nvidia):
            bin_dir = os.path.join(nvidia, entry, "bin")
            if not os.path.isdir(bin_dir) or bin_dir in _dll_directories:
                continue
            try:
                # The returned object closes the search path when destroyed.
                _dll_handles.append(os.add_dll_directory(bin_dir))
                _dll_directories.add(bin_dir)
            except OSError:  # pragma: no cover - defensive
                log.debug("Could not add DLL directory %s", bin_dir)


if __name__ == "__main__":  # pragma: no cover - runs only as the probe subprocess
    print(probe_cuda_in_process())
