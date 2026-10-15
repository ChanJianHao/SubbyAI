"""Shortcut definitions shared by settings, help and runtime registration. No Qt."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShortcutAction:
    id: str
    label: str
    default: str


GLOBAL_ACTIONS = (
    ShortcutAction("toggle_captions", "Captions on or off", "Ctrl+Alt+C"),
    ShortcutAction("toggle_overlay", "Show or hide captions", "Ctrl+Alt+O"),
    ShortcutAction("toggle_click_through", "Let clicks pass through", "Ctrl+Alt+T"),
    ShortcutAction("toggle_translation", "Translation on or off", "Ctrl+Alt+L"),
    ShortcutAction("caption_bigger", "Bigger caption text", "Ctrl+Alt+Up"),
    ShortcutAction("caption_smaller", "Smaller caption text", "Ctrl+Alt+Down"),
    ShortcutAction("scrub_back", "Go back through captions", "Ctrl+Alt+Left"),
    ShortcutAction("scrub_forward", "Go forward through captions", "Ctrl+Alt+Right"),
)

SHELL_ACTIONS = (
    ShortcutAction("live", "Go to Live", "Ctrl+1"),
    ShortcutAction("history", "Go to History", "Ctrl+2"),
    ShortcutAction("settings", "Go to Settings", "Ctrl+3"),
    ShortcutAction("settings_comma", "Go to Settings", "Ctrl+,"),
    ShortcutAction("close", "Close the window", "Ctrl+W"),
    ShortcutAction("quit", "Quit", "Ctrl+Q"),
)

TRANSCRIPT_ACTIONS = (
    ShortcutAction("find", "Find in transcript", "Ctrl+F"),
    ShortcutAction("export", "Export transcript", "Ctrl+E"),
    ShortcutAction("copy", "Copy selected transcript lines", "Ctrl+C"),
    ShortcutAction("rename", "Rename transcript", "F2"),
    ShortcutAction("back", "Back to History", "Alt+Left"),
    ShortcutAction("previous", "Previous search match", "Shift+Return"),
    ShortcutAction("escape", "Close transcript search or selection", "Escape"),
)


def default_bindings() -> dict[str, str]:
    return bindings_with_defaults({})


def bindings_with_defaults(bindings: dict[str, str]) -> dict[str, str]:
    """Missing actions use their defaults; an explicit empty string stays cleared."""
    return {action.id: bindings.get(action.id, action.default) for action in GLOBAL_ACTIONS}
