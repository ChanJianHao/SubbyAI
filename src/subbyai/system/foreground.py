"""What the user is actually looking at.

Sessions get named after it — "Chrome - 14:02" beats "Session 47" when you are
hunting through history a week later — and the fullscreen check drives the one
warning we cannot infer any other way: in exclusive fullscreen the overlay
cannot be drawn on top at all, so the user needs to be told to switch the game
to borderless rather than left wondering where their captions went.

No Qt here. Never raises: a missing name only costs a nicer session title.
"""

from __future__ import annotations

import logging
import os
import sys

import psutil

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Executable name -> what a human calls it. Small on purpose: it only needs to
# cover what people caption. Everything else falls through to _prettify.
_FRIENDLY_NAMES: dict[str, str] = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "librewolf.exe": "LibreWolf",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "opera_gx.exe": "Opera GX",
    "vivaldi.exe": "Vivaldi",
    "chromium.exe": "Chromium",
    "discord.exe": "Discord",
    "slack.exe": "Slack",
    "teams.exe": "Teams",
    "ms-teams.exe": "Teams",
    "zoom.exe": "Zoom",
    "webexmta.exe": "Webex",
    "skype.exe": "Skype",
    "telegram.exe": "Telegram",
    "whatsapp.exe": "WhatsApp",
    "vlc.exe": "VLC",
    "mpv.exe": "mpv",
    "mpc-hc64.exe": "MPC-HC",
    "potplayermini64.exe": "PotPlayer",
    "wmplayer.exe": "Windows Media Player",
    "spotify.exe": "Spotify",
    "itunes.exe": "iTunes",
    "steam.exe": "Steam",
    "steamwebhelper.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games",
    "obs64.exe": "OBS",
    "obs32.exe": "OBS",
    "code.exe": "VS Code",
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "outlook.exe": "Outlook",
    "onenote.exe": "OneNote",
    "acrord32.exe": "Acrobat Reader",
}

# Shell surfaces. Naming a session after these tells the user nothing, so we
# report no app and let the caller fall back to a generic title.
_SHELL_PROCESSES: frozenset[str] = frozenset(
    {
        "explorer.exe",
        "applicationframehost.exe",
        "searchhost.exe",
        "searchapp.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "textinputhost.exe",
        "lockapp.exe",
        "dwm.exe",
    }
)

_MONITOR_DEFAULTTONEAREST = 2

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    class _MONITORINFO(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        )

    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL


def foreground_app_name() -> str | None:
    """Friendly name of the app with keyboard focus, or ``None``.

    ``None`` means "nothing worth naming": another platform, a shell surface,
    SubbyAI itself, or a process we were not allowed to inspect.
    """
    if not IS_WINDOWS:
        return None
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == os.getpid():
            return None
        process_name = psutil.Process(pid.value).name()
    except Exception:
        log.debug("Could not identify the foreground app", exc_info=True)
        return None

    key = process_name.lower()
    if key in _SHELL_PROCESSES:
        return None
    return _FRIENDLY_NAMES.get(key) or _prettify(process_name)


def is_fullscreen_foreground() -> bool:
    """Whether the focused window covers an entire monitor."""
    if not IS_WINDOWS:
        return False
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        monitor = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return False
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        screen = info.rcMonitor
        return (
            rect.left <= screen.left
            and rect.top <= screen.top
            and rect.right >= screen.right
            and rect.bottom >= screen.bottom
        )
    except Exception:
        log.debug("Could not measure the foreground window", exc_info=True)
        return False


def _prettify(process_name: str) -> str:
    stem = process_name[:-4] if process_name.lower().endswith(".exe") else process_name
    stem = stem.strip() or process_name
    # Names that already carry capitals ("OneDrive") are their own branding;
    # only lowercase ones ("zoom") need title-casing.
    return stem if any(char.isupper() for char in stem) else stem.title()
