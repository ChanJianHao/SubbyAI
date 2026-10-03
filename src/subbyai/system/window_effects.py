"""Native window polish, and two things the overlay genuinely depends on.

Everything here is Windows-only and every call is failure-tolerant: an older
Windows build simply refuses the attribute and we return ``False``. Nothing in
this module is load-bearing for captions, so nothing in it may raise.

Two are not cosmetic:

- ``set_topmost`` re-asserts ``HWND_TOPMOST``. Borderless-fullscreen games take
  the top of the z-order when they gain focus, and Qt's ``WindowStaysOnTopHint``
  does not fight back. Re-asserting after focus changes is what keeps captions
  visible over a game.
- ``exclude_from_capture`` hides the overlay from screen recorders and meeting
  screen-shares. A user captioning a call should not broadcast their captions
  (and, with translation on, their reading level) to the whole meeting.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

BACKDROPS: tuple[str, ...] = ("mica", "acrylic", "none")

# dwmapi.h attribute ids.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_PRE_20H1 = 19
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWA_MICA_EFFECT = 1029  # undocumented; the only way in on Windows 11 21H2

_DWMWCP_ROUND = 2
_DWMSBT_NONE = 1
_BACKDROP_TYPES: dict[str, int] = {"none": _DWMSBT_NONE, "mica": 2, "acrylic": 3}

_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010

_WDA_NONE = 0x00000000
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

    class _MARGINS(ctypes.Structure):
        _fields_ = (
            ("cxLeftWidth", ctypes.c_int),
            ("cxRightWidth", ctypes.c_int),
            ("cyTopHeight", ctypes.c_int),
            ("cyBottomHeight", ctypes.c_int),
        )

    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    _dwmapi.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.LPCVOID,
        wintypes.DWORD,
    ]
    _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    _dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_MARGINS)]
    _dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long


def apply_dark_titlebar(win_id: int, dark: bool = True) -> bool:
    """Paint the non-client area to match a dark theme."""
    hwnd = _hwnd(win_id)
    if hwnd is None:
        return False
    value = int(bool(dark))
    if _set_attribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, value):
        return True
    # Windows 10 builds before 20H1 used a different attribute id for this.
    return _set_attribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_PRE_20H1, value)


def apply_backdrop(win_id: int, kind: str = "mica") -> bool:
    """Ask DWM for a material background. ``kind`` is mica, acrylic or none."""
    backdrop = _BACKDROP_TYPES.get(str(kind).strip().lower())
    if backdrop is None:
        return False
    hwnd = _hwnd(win_id)
    if hwnd is None:
        return False
    if backdrop != _DWMSBT_NONE:
        # The material is drawn in the frame, so the frame has to cover the window.
        _extend_frame(hwnd)
    if _set_attribute(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, backdrop):
        return True
    if backdrop == _BACKDROP_TYPES["mica"]:
        return _set_attribute(hwnd, _DWMWA_MICA_EFFECT, 1)
    return False


def apply_rounded_corners(win_id: int) -> bool:
    """Opt into the Windows 11 rounded corner shape."""
    hwnd = _hwnd(win_id)
    if hwnd is None:
        return False
    return _set_attribute(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)


def set_topmost(win_id: int) -> bool:
    """Re-assert always-on-top without stealing focus, moving or resizing.

    Call this whenever the foreground window changes: a game going borderless
    fullscreen otherwise ends up above the overlay and the captions vanish.
    """
    hwnd = _hwnd(win_id)
    if hwnd is None:
        return False
    try:
        return bool(
            _user32.SetWindowPos(
                hwnd,
                _HWND_TOPMOST,
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
        )
    except Exception:
        log.debug("SetWindowPos failed", exc_info=True)
        return False


def exclude_from_capture(win_id: int, excluded: bool = True) -> bool:
    """Hide (or unhide) the window from screen capture and screen sharing."""
    hwnd = _hwnd(win_id)
    if hwnd is None:
        return False
    affinity = _WDA_EXCLUDEFROMCAPTURE if excluded else _WDA_NONE
    try:
        return bool(_user32.SetWindowDisplayAffinity(hwnd, affinity))
    except Exception:
        log.debug("SetWindowDisplayAffinity failed", exc_info=True)
        return False


# ---------- internals ----------


def _hwnd(win_id: object) -> int | None:
    """Validate a Qt ``winId()`` and hand back a usable HWND, or ``None``."""
    if not IS_WINDOWS:
        return None
    try:
        handle = int(win_id)  # type: ignore[call-overload] - winId() is an opaque int-like
    except (TypeError, ValueError):
        return None
    if handle <= 0:
        return None
    try:
        return handle if _user32.IsWindow(wintypes.HWND(handle)) else None
    except Exception:
        log.debug("IsWindow failed for %r", win_id, exc_info=True)
        return None


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    try:
        data = ctypes.c_int(value)
        result = _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data)
        )
        return result == 0
    except Exception:
        log.debug("DwmSetWindowAttribute(%s) failed", attribute, exc_info=True)
        return False


def _extend_frame(hwnd: int) -> bool:
    try:
        margins = _MARGINS(-1, -1, -1, -1)
        result = _dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        return result == 0
    except Exception:
        log.debug("DwmExtendFrameIntoClientArea failed", exc_info=True)
        return False
