"""OS integration. Everything here runs headless and touches nothing global."""

from __future__ import annotations

import os
import sys

import pytest

from subbyai.system import foreground, single_instance, window_effects
from subbyai.system.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    HotkeyManager,
    find_conflicts,
    is_valid_global,
    normalize_sequence,
    parse_sequence,
)

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")

VK_C = 0x43
VK_UP = 0x26
VK_F5 = 0x74
VK_DELETE = 0x2E
VK_OEM_PLUS = 0xBB


# ---------- parse_sequence ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ctrl+Alt+C", (MOD_CONTROL | MOD_ALT, VK_C)),
        ("Ctrl+Alt+Up", (MOD_CONTROL | MOD_ALT, VK_UP)),
        ("Shift+F5", (MOD_SHIFT, VK_F5)),
        ("ctrl+alt+c", (MOD_CONTROL | MOD_ALT, VK_C)),
        ("Alt + Ctrl + C", (MOD_CONTROL | MOD_ALT, VK_C)),
        ("Ctrl+Alt+Del", (MOD_CONTROL | MOD_ALT, VK_DELETE)),
        ("F5", (0, VK_F5)),
        ("Ctrl++", (MOD_CONTROL, VK_OEM_PLUS)),  # the plus key, not a stray separator
    ],
)
def test_parse_sequence_reads_qt_style_text(text, expected):
    assert parse_sequence(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Ctrl+",
        "Ctrl+Alt",  # modifiers only, no key to register
        "Ctrl+Alt+Nonsense",
        "Ctrl+A+B",  # two real keys is not a hotkey
        "Ctrl+F25",
        "Ctrl+Alt+Ctrl",  # a modifier cannot be the key
    ],
)
def test_parse_sequence_rejects_nonsense(text):
    assert parse_sequence(text) is None


# ---------- is_valid_global ----------


@pytest.mark.parametrize("text", ["Ctrl+Alt+C", "Ctrl+Shift+O", "Shift+F5", "Ctrl+Alt+Up"])
def test_is_valid_global_accepts_real_combinations(text):
    valid, reason = is_valid_global(text)
    assert valid is True
    assert reason == ""


@pytest.mark.parametrize(
    "text",
    [
        "",  # unbound
        "Ctrl",  # modifier only
        "Ctrl+Alt",  # modifiers only
        "C",  # bare key
        "F5",  # bare key
        "Shift+C",  # would fire on every capital C typed anywhere
        "Shift+Up",  # would hijack shift-selection everywhere
        "Ctrl+Alt+Del",  # owned by Windows
        "Ctrl+Alt+Delete",
        "Ctrl+Shift+Esc",
        "Alt+F4",
        "Win+C",  # Windows key is reserved wholesale
        "Meta+Shift+S",
        "Ctrl+Win+Left",
        "Ctrl+Alt+Banana",
    ],
)
def test_is_valid_global_rejects_with_a_reason(text):
    valid, reason = is_valid_global(text)
    assert valid is False
    assert reason  # every refusal has to be explainable to the user


# ---------- normalize_sequence ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("alt+ctrl+c", "Ctrl+Alt+C"),
        ("Ctrl+Alt+C", "Ctrl+Alt+C"),
        ("shift+ctrl+alt+o", "Ctrl+Alt+Shift+O"),
        ("ctrl+alt+del", "Ctrl+Alt+Delete"),
        ("CTRL+ALT+UP", "Ctrl+Alt+Up"),
        ("shift+f5", "Shift+F5"),
        ("meta+c", "Win+C"),
        ("ctrl+esc", "Ctrl+Escape"),
    ],
)
def test_normalize_sequence_is_canonical(text, expected):
    assert normalize_sequence(text) == expected


def test_normalize_sequence_is_idempotent():
    once = normalize_sequence("alt+ctrl+c")
    assert normalize_sequence(once) == once


@pytest.mark.parametrize("text", ["", "Ctrl+Alt", "gibberish"])
def test_normalize_sequence_gives_up_quietly(text):
    assert normalize_sequence(text) == ""


# ---------- find_conflicts ----------


def test_find_conflicts_groups_actions_sharing_a_sequence():
    conflicts = find_conflicts(
        {
            "toggle_captions": "Ctrl+Alt+C",
            "toggle_overlay": "alt+ctrl+c",  # same shortcut, spelled differently
            "toggle_translation": "Ctrl+Alt+L",
            "scrub_back": "",
            "scrub_forward": "",
        }
    )
    assert conflicts == {"Ctrl+Alt+C": ["toggle_captions", "toggle_overlay"]}


def test_find_conflicts_is_empty_when_every_binding_is_distinct():
    assert find_conflicts({"a": "Ctrl+Alt+C", "b": "Ctrl+Alt+O", "c": ""}) == {}


# ---------- HotkeyManager ----------


def test_hotkey_manager_reports_support_for_the_platform(qt_app):
    manager = HotkeyManager()
    assert manager.is_supported is (sys.platform == "win32")
    manager.unregister_all()  # never acquired anything; must not raise


def test_hotkey_manager_rejects_bad_bindings_without_touching_the_os(qt_app):
    manager = HotkeyManager()
    failures = manager.register_all(
        {
            "bad_reserved": "Ctrl+Alt+Del",
            "bad_bare": "F5",
            "bad_win": "Win+C",
            "unbound": "",
        }
    )
    assert set(failures) == {"bad_reserved", "bad_bare", "bad_win"}
    assert all(failures.values())
    manager.unregister_all()


def test_hotkey_manager_reports_a_shortcut_bound_twice(qt_app, monkeypatch):
    # Pretend we are on an unsupported platform so nothing is registered for
    # real; duplicate detection happens before the OS is ever consulted.
    manager = HotkeyManager()
    monkeypatch.setattr(manager, "is_supported", False)
    failures = manager.register_all({"first": "Ctrl+Alt+C", "second": "alt+ctrl+c"})
    assert "second" in failures
    assert "Ctrl+Alt+C" in failures["second"]


def test_hotkey_manager_explains_itself_on_unsupported_platforms(qt_app, monkeypatch):
    manager = HotkeyManager()
    monkeypatch.setattr(manager, "is_supported", False)
    failures = manager.register_all({"toggle_captions": "Ctrl+Alt+C"})
    assert list(failures) == ["toggle_captions"]
    assert failures["toggle_captions"]


# ---------- window_effects ----------


@pytest.mark.parametrize("win_id", [0, -1, 1, 123456789, None, "not a handle"])
def test_window_effects_refuse_an_invalid_window(win_id):
    assert window_effects.apply_dark_titlebar(win_id, True) is False
    assert window_effects.apply_backdrop(win_id, "mica") is False
    assert window_effects.apply_rounded_corners(win_id) is False
    assert window_effects.set_topmost(win_id) is False
    assert window_effects.exclude_from_capture(win_id, True) is False


def test_window_effects_reject_an_unknown_backdrop():
    assert window_effects.apply_backdrop(123456789, "holographic") is False


# ---------- foreground ----------


def test_foreground_app_name_never_raises():
    name = foreground.foreground_app_name()
    assert name is None or (isinstance(name, str) and name)


def test_is_fullscreen_foreground_never_raises():
    assert isinstance(foreground.is_fullscreen_foreground(), bool)


@pytest.mark.parametrize(
    ("process_name", "expected"),
    [
        ("chrome.exe", "Chrome"),
        ("Discord.exe", "Discord"),
        ("vlc.exe", "VLC"),
        ("obs64.exe", "OBS"),
    ],
)
def test_known_processes_have_friendly_names(process_name, expected):
    assert foreground._FRIENDLY_NAMES[process_name.lower()] == expected


@pytest.mark.parametrize(
    ("process_name", "expected"),
    [
        ("someapp.exe", "Someapp"),
        ("OneDrive.exe", "OneDrive"),  # already branded; leave it alone
        ("mystery", "Mystery"),
    ],
)
def test_unknown_processes_are_prettified(process_name, expected):
    assert foreground._prettify(process_name) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has a real foreground window")
def test_foreground_is_unavailable_off_windows():
    assert foreground.foreground_app_name() is None
    assert foreground.is_fullscreen_foreground() is False


# ---------- SingleInstance ----------


@pytest.fixture
def instance_key(tmp_path):
    """A key nothing else on this machine can collide with."""
    return f"subbyai-test-{os.getpid()}-{tmp_path.name}"


def test_single_instance_acquires_and_releases(instance_key):
    guard = single_instance.SingleInstance(instance_key)
    assert guard.acquire() is True
    assert guard.is_held is True
    guard.release()
    assert guard.is_held is False
    assert guard.acquire() is True  # released cleanly, so it is free again
    guard.release()


def test_single_instance_blocks_a_second_copy(instance_key):
    first = single_instance.SingleInstance(instance_key)
    second = single_instance.SingleInstance(instance_key)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_single_instance_acquire_is_idempotent(instance_key):
    guard = single_instance.SingleInstance(instance_key)
    assert guard.acquire() is True
    assert guard.acquire() is True
    guard.release()
    guard.release()  # double release must not raise


def test_single_instance_works_as_a_context_manager(instance_key):
    with single_instance.SingleInstance(instance_key) as held:
        assert held is True
        assert single_instance.SingleInstance(instance_key).acquire() is False


def test_different_keys_do_not_collide(instance_key):
    first = single_instance.SingleInstance(instance_key)
    second = single_instance.SingleInstance(f"{instance_key}-other")
    assert first.acquire() is True
    assert second.acquire() is True
    first.release()
    second.release()


def test_safe_name_survives_a_path_shaped_key(tmp_path):
    name = single_instance._safe_name(str(tmp_path / "subbyai.lock"))
    assert name
    assert len(name) <= 180
    assert all(char.isalnum() or char in "_.-" for char in name)


def test_abandoned_or_corrupt_file_does_not_claim_kernel_ownership(tmp_path):
    from subbyai import paths

    guard = single_instance.SingleInstance("file-key")
    lock = paths.config_dir() / f"{guard._name}.lock"
    lock.write_text("untrusted stale contents", encoding="utf-8")
    assert guard._acquire_lock_file()
    contender = single_instance.SingleInstance("file-key")
    assert not contender._acquire_lock_file()
    guard.release()
    assert contender._acquire_lock_file()
    contender.release()


@WINDOWS_ONLY
def test_single_instance_uses_a_named_mutex_on_windows(instance_key):
    guard = single_instance.SingleInstance(instance_key)
    assert guard.acquire() is True
    assert guard._handle is not None  # mutex, not a lock file
    assert guard._lock_path is None
    guard.release()

