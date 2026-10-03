"""System-wide, rebindable hotkeys.

Windows uses RegisterHotKey against a message-only window on a dedicated thread.
Queued Qt signals deliver activations to the GUI, including while the shell is
hidden. Pure helpers validate bindings before registration."""

from __future__ import annotations

import logging
import re
import sys
import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal, Slot

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Win32 hotkey modifier bits (winuser.h). Defined unconditionally: the parsing
# helpers speak this vocabulary on every platform so they stay testable.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIER_ALIASES: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "ctl": MOD_CONTROL,
    "alt": MOD_ALT,
    "opt": MOD_ALT,
    "option": MOD_ALT,
    "altgr": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
    "cmd": MOD_WIN,
    "command": MOD_WIN,
}

# Canonical print order. Matches what Qt shows, with "Win" instead of "Meta"
# because this is a Windows-first product and users read it off their keyboard.
_MODIFIER_ORDER: tuple[tuple[int, str], ...] = (
    (MOD_CONTROL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_WIN, "Win"),
)

# Virtual-key code -> the spelling we display and store.
_KEY_NAMES: dict[int, str] = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Return",
    0x13: "Pause",
    0x14: "CapsLock",
    0x1B: "Escape",
    0x20: "Space",
    0x21: "PgUp",
    0x22: "PgDown",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print",
    0x2D: "Insert",
    0x2E: "Delete",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
}

_KEY_ALIASES: dict[str, int] = {
    "esc": 0x1B,
    "enter": 0x0D,
    "del": 0x2E,
    "ins": 0x2D,
    "pageup": 0x21,
    "pagedown": 0x22,
    "printscreen": 0x2C,
    "prtsc": 0x2C,
    "plus": 0xBB,
    "+": 0xBB,
    "minus": 0xBD,
}

_KEY_LOOKUP: dict[str, int] = {
    **{name.lower(): vk for vk, name in _KEY_NAMES.items()},
    **_KEY_ALIASES,
}

_FUNCTION_KEY = re.compile(r"f([1-9]|1[0-9]|2[0-4])\Z")
_ASCII_KEY = re.compile(r"[a-z0-9]\Z")

# Combos Windows itself owns. RegisterHotKey either refuses them or silently
# loses to the shell, so reject them in the editor rather than in the log.
_RESERVED: frozenset[str] = frozenset(
    {
        "Ctrl+Alt+Delete",
        "Ctrl+Shift+Escape",
        "Ctrl+Escape",
        "Alt+Escape",
        "Alt+Tab",
        "Alt+F4",
        "Ctrl+Shift+Delete",
    }
)

_UNSUPPORTED_REASON = "Global shortcuts are only available on Windows right now."
_GENERIC_FAILURE = "Windows would not accept this shortcut."
_ALREADY_TAKEN = "Another app is already using {sequence}."
_DUPLICATE = "{sequence} is already assigned to another SubbyAI shortcut."

_START_TIMEOUT = 5.0
_STOP_TIMEOUT = 2.0

_warned_unsupported = False


# --------------------------------------------------------------------------
# Pure helpers. No OS calls, no Qt objects — safe to unit-test anywhere.
# --------------------------------------------------------------------------


def parse_sequence(text: str) -> tuple[int, int] | None:
    """Turn a Qt-style sequence such as ``Ctrl+Alt+Up`` into ``(modifiers, vk)``.

    Returns ``None`` when the text names no key, names more than one, or names
    something we have no virtual-key code for.
    """
    tokens = _tokenize(text)
    if not tokens:
        return None
    modifiers = 0
    vk: int | None = None
    for token in tokens:
        bit = _MODIFIER_ALIASES.get(token.lower())
        if bit is not None:
            modifiers |= bit
            continue
        if vk is not None:
            return None  # two non-modifier keys is not a hotkey
        vk = _key_code(token)
        if vk is None:
            return None
    if vk is None:
        return None
    return modifiers, vk


def normalize_sequence(text: str) -> str:
    """Canonical spelling of a sequence, or ``""`` if it cannot be parsed.

    Comparisons (conflict detection, reserved-combo checks, settings equality)
    all go through this so ``alt+ctrl+c`` and ``Ctrl+Alt+C`` are one shortcut.
    """
    parsed = parse_sequence(text)
    if parsed is None:
        return ""
    modifiers, vk = parsed
    parts = [name for bit, name in _MODIFIER_ORDER if modifiers & bit]
    parts.append(_key_name(vk))
    return "+".join(parts)


def is_valid_global(text: str) -> tuple[bool, str]:
    """Whether a sequence can serve as a global hotkey, plus why it cannot."""
    if not text or not text.strip():
        return False, "Pick a key combination."

    parsed = parse_sequence(text)
    if parsed is None:
        tokens = _tokenize(text)
        if tokens and all(token.lower() in _MODIFIER_ALIASES for token in tokens):
            return False, "Add a letter, number or arrow — modifiers alone won't do."
        return False, f"{text.strip()} isn't a key combination we recognise."

    modifiers, vk = parsed
    if modifiers & MOD_WIN:
        return False, "Windows keeps every shortcut that uses the Windows key."
    if modifiers == 0:
        return False, "A global shortcut needs Ctrl or Alt, otherwise it hijacks typing."
    # Shift alone still hijacks typing everywhere: Shift+C would fire on every
    # capital C, Shift+Up on every shift-selection. Function keys carry no such
    # meaning, so they are the one safe case.
    if modifiers == MOD_SHIFT and not _is_function_key(vk):
        return False, "Shift on its own isn't enough — add Ctrl or Alt."

    canonical = normalize_sequence(text)
    if canonical in _RESERVED:
        return False, f"{canonical} belongs to Windows and can't be reassigned."
    return True, ""


def find_conflicts(bindings: dict[str, str]) -> dict[str, list[str]]:
    """Map each doubly-bound sequence to the action ids fighting over it.

    Unbound actions (empty string) and unparseable text are ignored — they can
    never collide with anything.
    """
    seen: dict[str, list[str]] = {}
    for action, sequence in bindings.items():
        canonical = normalize_sequence(sequence)
        if not canonical:
            continue
        seen.setdefault(canonical, []).append(action)
    return {sequence: actions for sequence, actions in seen.items() if len(actions) > 1}


def _tokenize(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if raw == "+":
        return ["+"]
    # "Ctrl++" means Ctrl plus the plus key; a naive split would drop it.
    if raw.endswith("++"):
        return [*(part.strip() for part in raw[:-2].split("+")), "+"]
    return [part.strip() for part in raw.split("+")]


def _key_code(token: str) -> int | None:
    name = token.strip().lower()
    if not name:
        return None
    if _ASCII_KEY.fullmatch(name):
        return ord(name.upper())
    function_key = _FUNCTION_KEY.fullmatch(name)
    if function_key:
        return 0x6F + int(function_key.group(1))
    return _KEY_LOOKUP.get(name)


def _key_name(vk: int) -> str:
    if vk in _KEY_NAMES:
        return _KEY_NAMES[vk]
    if 0x30 <= vk <= 0x5A:  # digits and letters share their ASCII codes
        return chr(vk)
    if _is_function_key(vk):
        return f"F{vk - 0x6F}"
    return f"0x{vk:02X}"


def _is_function_key(vk: int) -> bool:
    return 0x70 <= vk <= 0x87


# --------------------------------------------------------------------------
# Win32 plumbing
# --------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(
        _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = (
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        )

    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = _LRESULT
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_WM_HOTKEY = 0x0312
_WM_CHIARO_QUIT = 0x0400 + 17  # WM_APP + n; ours alone, nothing else posts it
_HWND_MESSAGE = -3
_ERROR_CLASS_ALREADY_EXISTS = 1410
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_CLASS_NAME = "SubbyAIHotkeyWindow"
_class_lock = threading.Lock()
_class_atom = 0
_wndproc_ref = None  # the WNDPROC thunk must outlive the window class
_owners: dict[int, HotkeyManager] = {}  # hwnd -> manager that created it


@dataclass(frozen=True, slots=True)
class _Binding:
    action: str
    sequence: str
    modifiers: int
    vk: int


class HotkeyManager(QObject):
    """Owns the process-wide hotkey registrations and reports activations.

    ``activated`` always arrives on the thread the manager lives on, no matter
    which thread the OS notified.
    """

    activated = Signal(str)

    _fired = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.is_supported: bool = IS_WINDOWS
        self._thread: threading.Thread | None = None
        self._hwnd: int = 0
        self._bindings: dict[int, _Binding] = {}
        self._result: dict[str, str] = {}
        # Queued: _fired is emitted from the Win32 message loop, activated is
        # re-emitted here, on the thread that owns this object.
        self._fired.connect(self._republish, Qt.ConnectionType.QueuedConnection)

    # ---------- public API ----------

    def register_all(self, bindings: dict[str, str]) -> dict[str, str]:
        """Replace every registration. Returns ``{action_id: friendly reason}``.

        A reason means that one binding is dead; the rest still work. Actions
        bound to an empty string are deliberately unbound and never reported.
        """
        self.unregister_all()

        failures: dict[str, str] = {}
        wanted: list[_Binding] = []
        claimed: dict[tuple[int, int], str] = {}

        for action, sequence in bindings.items():
            if not sequence or not sequence.strip():
                continue
            ok, reason = is_valid_global(sequence)
            if not ok:
                failures[action] = reason
                continue
            combo = parse_sequence(sequence)
            if combo is None:  # unreachable: is_valid_global already parsed it
                failures[action] = _GENERIC_FAILURE
                continue
            if combo in claimed:
                failures[action] = _DUPLICATE.format(sequence=normalize_sequence(sequence))
                continue
            claimed[combo] = action
            wanted.append(_Binding(action, normalize_sequence(sequence), *combo))

        if not wanted:
            return failures
        if not self.is_supported:
            reason = _unsupported_reason()
            for binding in wanted:
                failures[binding.action] = reason
            return failures

        failures.update(self._start(wanted))
        return failures

    def unregister_all(self) -> None:
        """Drop every registration and stop the message loop. Safe to repeat."""
        thread, hwnd = self._thread, self._hwnd
        self._thread, self._hwnd = None, 0
        self._bindings = {}
        if thread is None or not thread.is_alive():
            return
        if hwnd:
            _user32.PostMessageW(hwnd, _WM_CHIARO_QUIT, 0, 0)
        thread.join(timeout=_STOP_TIMEOUT)
        if thread.is_alive():
            log.warning("Hotkey thread did not stop; shortcuts may linger until exit")

    # ---------- internals ----------

    @Slot(str)
    def _republish(self, action: str) -> None:
        self.activated.emit(action)

    def _start(self, wanted: list[_Binding]) -> dict[str, str]:
        self._bindings = dict(enumerate(wanted, start=1))
        self._result = {}
        ready = threading.Event()
        thread = threading.Thread(
            target=self._run, args=(ready,), name="subbyai-hotkeys", daemon=True
        )
        self._thread = thread
        thread.start()
        if not ready.wait(_START_TIMEOUT):
            log.warning("Hotkey thread did not come up within %.0fs", _START_TIMEOUT)
            return {binding.action: _GENERIC_FAILURE for binding in wanted}
        return dict(self._result)

    def _run(self, ready: threading.Event) -> None:
        """Message-loop thread: owns the window and therefore the hotkeys."""
        hwnd = 0
        registered: list[int] = []
        failures: dict[str, str] = {}
        try:
            hwnd = _create_message_window()
            if hwnd:
                _owners[hwnd] = self
                for hotkey_id, binding in self._bindings.items():
                    flags = binding.modifiers | MOD_NOREPEAT
                    if _user32.RegisterHotKey(hwnd, hotkey_id, flags, binding.vk):
                        registered.append(hotkey_id)
                    else:
                        failures[binding.action] = _failure_reason(
                            ctypes.get_last_error(), binding.sequence
                        )
            else:
                failures = {b.action: _GENERIC_FAILURE for b in self._bindings.values()}
        except Exception:
            log.exception("Global hotkeys could not be registered")
            failures = {b.action: _GENERIC_FAILURE for b in self._bindings.values()}
            hwnd = 0
        finally:
            self._hwnd = hwnd
            self._result = failures
            ready.set()

        if not hwnd:
            return
        try:
            msg = wintypes.MSG()
            while True:
                got = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):  # WM_QUIT, or an error we cannot recover from
                    break
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.exception("Hotkey message loop stopped")
        finally:
            for hotkey_id in registered:
                _user32.UnregisterHotKey(hwnd, hotkey_id)
            _owners.pop(hwnd, None)
            _user32.DestroyWindow(hwnd)

    def _on_hotkey(self, hotkey_id: int) -> None:
        binding = self._bindings.get(hotkey_id)
        if binding is not None:
            self._fired.emit(binding.action)


def _unsupported_reason() -> str:
    global _warned_unsupported
    if not _warned_unsupported:
        _warned_unsupported = True
        log.info("Global shortcuts are unavailable on %s", sys.platform)
    return _UNSUPPORTED_REASON


def _failure_reason(error: int, sequence: str) -> str:
    if error == _ERROR_HOTKEY_ALREADY_REGISTERED:
        return _ALREADY_TAKEN.format(sequence=sequence)
    log.debug("RegisterHotKey(%s) failed with error %s", sequence, error)
    return _GENERIC_FAILURE


def _dispatch(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    """Window procedure. Runs on the hotkey thread; must never raise into C."""
    try:
        if msg == _WM_HOTKEY:
            owner = _owners.get(hwnd or 0)
            if owner is not None:
                owner._on_hotkey(int(wparam))
            return 0
        if msg == _WM_CHIARO_QUIT:
            _user32.PostQuitMessage(0)
            return 0
    except Exception:
        log.exception("Hotkey window procedure failed")
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_window_class() -> None:
    global _class_atom, _wndproc_ref
    with _class_lock:
        if _class_atom:
            return
        _wndproc_ref = _WNDPROC(_dispatch)
        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = _wndproc_ref
        window_class.hInstance = _kernel32.GetModuleHandleW(None)
        window_class.lpszClassName = _CLASS_NAME
        atom = _user32.RegisterClassW(ctypes.byref(window_class))
        if not atom and ctypes.get_last_error() != _ERROR_CLASS_ALREADY_EXISTS:
            raise ctypes.WinError(ctypes.get_last_error())
        _class_atom = atom or 1


def _create_message_window() -> int:
    _ensure_window_class()
    hwnd = _user32.CreateWindowExW(
        0, _CLASS_NAME, "SubbyAI hotkeys", 0, 0, 0, 0, 0, _HWND_MESSAGE, None, None, None
    )
    if not hwnd:
        log.warning("Could not create the hotkey window (error %s)", ctypes.get_last_error())
    return int(hwnd or 0)
