"""One application instance per profile, protected by operating-system locks.

Windows uses a session-local mutex. The file fallback holds a kernel lock for
the lifetime of the process; crashes release ownership automatically. Lock
files contain no process metadata and are never interpreted as ownership.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import sys
from pathlib import Path

from filelock import FileLock, Timeout

from .. import paths
from ..branding import SINGLE_INSTANCE_KEY

log = logging.getLogger(__name__)

_ERROR_ALREADY_EXISTS = 183
_MAX_NAME = 180  # named-object limit is 260; leave room for the namespace prefix
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class SingleInstance:
    """Process-wide launch guard. ``acquire`` once at startup, ``release`` at exit."""

    def __init__(self, key: str = SINGLE_INSTANCE_KEY) -> None:
        self._key = key
        self._name = _safe_name(key)
        self._handle: int | None = None
        self._lock_path: Path | None = None
        self._file_lock: FileLock | None = None
        self._kernel32 = None

    @property
    def is_held(self) -> bool:
        return self._handle is not None or self._lock_path is not None

    def acquire(self) -> bool:
        """True if this process now owns the app; False if another one already does."""
        if self.is_held:
            return True
        if sys.platform == "win32":
            result = self._acquire_mutex()
            if result is not None:
                return result
        return self._acquire_lock_file()

    def release(self) -> None:
        """Give up ownership. Safe to call twice, and safe if never acquired."""
        if self._handle is not None and self._kernel32 is not None:
            with contextlib.suppress(Exception):
                self._kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._file_lock is not None:
            self._file_lock.release()
            self._file_lock = None
            self._lock_path = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    # ---------- backends ----------

    def _acquire_mutex(self) -> bool | None:
        """Windows named mutex. ``None`` means "could not try" — fall back to a file."""
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            # Local\ scopes the mutex to the logon session, which is exactly the
            # scope of "one app on this desktop"; Global\ needs privileges.
            handle = kernel32.CreateMutexW(None, False, f"Local\\{self._name}")
            error = ctypes.get_last_error()
        except Exception:
            log.debug("Named mutex unavailable; falling back to a lock file", exc_info=True)
            return None
        if not handle:
            return None
        if error == _ERROR_ALREADY_EXISTS:
            with contextlib.suppress(Exception):
                kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = handle
        return True

    def _acquire_lock_file(self) -> bool:
        path = paths.config_dir() / f"{self._name}.lock"
        lock = FileLock(path, timeout=0, mode=0o600)
        try:
            lock.acquire()
        except Timeout:
            return False
        self._file_lock = lock
        self._lock_path = path
        return True


def _safe_name(key: str) -> str:
    """A key usable as both a Win32 object name and a filename."""
    cleaned = _UNSAFE.sub("-", key.strip()).strip("-") or "subbyai"
    if len(cleaned) > _MAX_NAME:
        digest = hashlib.blake2s(key.encode("utf-8"), digest_size=6).hexdigest()
        cleaned = f"{cleaned[: _MAX_NAME - len(digest) - 1]}-{digest}"
    return cleaned
