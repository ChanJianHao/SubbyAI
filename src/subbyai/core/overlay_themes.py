"""Bounded, portable appearance snapshots without display or session metadata."""

from .settings import OverlaySettings, Settings, from_dict, to_dict

APPEARANCE_FIELDS = (
    "custom_translation_color",
    "custom_border_color",
    "custom_border_width",
    "preset",
    "font_size",
    "font_weight",
    "max_pairs",
    "original_position",
    "dim_original",
    "opacity",
    "click_through",
    "auto_hide",
    "auto_hide_seconds",
    "custom_bg_color",
    "custom_text_color",
    "custom_radius",
    "custom_outline",
    "custom_shadow",
    "font_family",
    "line_spacing",
    "align",
    "padding",
    "animate",
    "outline_width",
    "max_lines",
    "always_on_top",
)
MAX_THEMES = 20


def valid_name(name: str) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name.strip()) <= 60
        and all(ord(char) >= 32 for char in name)
    )


def appearance(overlay: OverlaySettings) -> dict:
    data = to_dict(overlay)
    return {key: data[key] for key in APPEARANCE_FIELDS}


def sanitized(data: dict) -> dict:
    # Use the same coercion, clamps and colour validation as normal settings.
    clean = {key: value for key, value in data.items() if key in APPEARANCE_FIELDS}
    return appearance(from_dict(Settings, {"overlay": clean}).overlay)


def save_theme(overlay: OverlaySettings, name: str) -> None:
    if not valid_name(name):
        raise ValueError("Use a name of 1-60 characters, without control characters.")
    name = name.strip()
    if name not in overlay.saved_themes and len(overlay.saved_themes) >= MAX_THEMES:
        raise ValueError("You can save up to 20 looks. Remove one before adding another.")
    overlay.saved_themes[name] = sanitized(appearance(overlay))


def apply_theme(overlay: OverlaySettings, name: str) -> None:
    values = sanitized(overlay.saved_themes[name])
    validated = from_dict(OverlaySettings, values, "overlay.")
    for key in APPEARANCE_FIELDS:
        setattr(overlay, key, getattr(validated, key))
