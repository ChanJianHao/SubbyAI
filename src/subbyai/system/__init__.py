"""Operating-system integration: hotkeys, window effects, focus, launch guard.

``hotkeys`` is the one module in the package that imports Qt, so it is resolved
lazily. Anything else here can be used from the audio or engine layers without
dragging a GUI toolkit into a worker thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .foreground import foreground_app_name, is_fullscreen_foreground
from .single_instance import SingleInstance
from .window_effects import (
    apply_backdrop,
    apply_dark_titlebar,
    apply_rounded_corners,
    exclude_from_capture,
    set_topmost,
)

if TYPE_CHECKING:
    from .hotkeys import (
        HotkeyManager,
        find_conflicts,
        is_valid_global,
        normalize_sequence,
        parse_sequence,
    )

_LAZY = frozenset(
    {
        "HotkeyManager",
        "find_conflicts",
        "is_valid_global",
        "normalize_sequence",
        "parse_sequence",
    }
)

__all__ = [
    "HotkeyManager",
    "SingleInstance",
    "apply_backdrop",
    "apply_dark_titlebar",
    "apply_rounded_corners",
    "exclude_from_capture",
    "find_conflicts",
    "foreground_app_name",
    "is_fullscreen_foreground",
    "is_valid_global",
    "normalize_sequence",
    "parse_sequence",
    "set_topmost",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from . import hotkeys

        return getattr(hotkeys, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
