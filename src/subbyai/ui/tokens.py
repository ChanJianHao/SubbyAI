"""Design tokens for SubbyAI.

Captions are read against someone else's picture, so the palette is built for
legibility over an image nobody controls. Two consequences:

- Light and dark share a sakura and lavender identity with readable neutral text.
- Sakura, lavender and ocean accents tint actions and translated captions.

Values here feed both the QSS template and the custom painters (overlay,
level meter), so there is a single source of truth for colour and rhythm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..core.settings import OverlayPreset

# ---------- rhythm ----------

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 8, "md": 12, "lg": 18, "xl": 24, "full": 16}

# One motion language: crisp controls, soft arrivals, no delayed actions.
DURATION = {"fast": 110, "base": 180, "gentle": 240, "page": 220, "feedback": 360}
DURATION.update({"activity": 1200, "progress": 1400, "confirmation": 1800})
MOTION_DISTANCE = {"page": 16, "arrival": 6}

TYPE_SCALE = {
    "display": (26, 600),
    "title": (20, 600),
    "heading": (16, 600),
    "body_strong": (13, 600),
    "body": (13, 400),
    "caption": (12, 400),
    "micro": (11, 500),
}

# Qt does not resolve the CSS generic families: "system-ui", "sans-serif" and
# "monospace" all silently become Tahoma on Windows, so a stack must end in a
# real family that is actually installed.
UI_FONT_STACK = ["Inter", "Segoe UI Variable", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Arial"]
CAPTION_FONT_STACK = [
    "Atkinson Hyperlegible Next",
    "Inter",
    "Segoe UI Variable",  # Windows 11
    "Segoe UI",  # Windows 10 — without this the fallback was Yu Gothic UI,
    "SF Pro Text",  # a Japanese UI face, for English captions, on every Win10
    "Helvetica Neue",  # box and every Mac.
    "Noto Sans",
    "Arial",
]
MONO_FONT_STACK = ["JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono", "Menlo", "Courier New"]

#: Families offered in the caption font picker, best-first. Filtered at runtime
#: against what is really installed — an unavailable name in a picker is a lie.
CAPTION_FONT_CHOICES = [
    "Atkinson Hyperlegible Next",
    "Segoe UI",
    "Verdana",
    "Tahoma",
    "Arial",
    "Trebuchet MS",
    "Helvetica Neue",
    "Consolas",
]


@dataclass(frozen=True, slots=True)
class Palette:
    """One theme's colours. Names describe role, never appearance."""

    name: str
    is_dark: bool

    canvas: str
    surface: str
    raised: str
    float_: str
    input_bg: str

    hairline: str
    """Decorative separation — group dividers, card edges. Deliberately quiet.

    WCAG 1.4.11 asks for 3:1 only on boundaries that *identify* a control. A
    divider between settings groups is not one, and forcing it to 3:1 turns a
    hairline into a hard rule. Anything whose edge carries meaning uses
    ``stroke`` instead.
    """

    stroke: str
    """Boundaries that identify a control: input fields, checkboxes, buttons.

    Held at >= 3:1 against the surface behind it, because losing this edge means
    losing the control.
    """

    focus: str
    """The keyboard focus ring. Must be visible against every surface."""

    text: str
    text_secondary: str
    text_tertiary: str
    text_disabled: str

    accent: str
    accent_hover: str
    accent_pressed: str
    accent_on: str
    accent_subtle: str

    success: str
    warning: str
    error: str
    info: str

    shadow_1: str
    shadow_2: str


DARK = Palette(
    name="dark",
    is_dark=True,
    canvas="#181420",
    surface="#211B2C",
    raised="#2A2237",
    float_="#30263D",
    input_bg="#2A2237",
    hairline="rgba(244, 239, 230, 0.14)",
    stroke="#9585A3",
    focus="#F3B7D6",
    text="#F8F0FA",
    text_secondary="#D3C4DC",
    text_tertiary="#B8A8C3",
    text_disabled="#A091AB",
    accent="#F0A7CD",
    accent_hover="#F7C0DD",
    accent_pressed="#DC96BC",
    accent_on="#29182A",
    accent_subtle="#3E2944",
    success="#58BD8B",
    warning="#E08543",
    error="#E25A50",
    info="#6FA9D6",
    shadow_1="rgba(0, 0, 0, 0.40)",
    shadow_2="rgba(0, 0, 0, 0.50)",
)

LIGHT = Palette(
    name="light",
    is_dark=False,
    canvas="#FCF6FC",
    surface="#FFFFFF",
    raised="#F3EAF6",
    float_="#FFFFFF",
    input_bg="#F3EAF6",
    hairline="rgba(32, 28, 23, 0.16)",
    stroke="#8C7897",
    focus="#87305C",
    text="#302338",
    text_secondary="#60506D",
    text_tertiary="#74627F",
    text_disabled="#8B7895",
    accent="#963461",
    accent_hover="#7E2854",
    accent_pressed="#672045",
    accent_on="#FFFFFF",
    accent_subtle="#F8E5F0",
    success="#1E7A4F",
    warning="#A54E13",
    error="#BE3F36",
    info="#2D6E9E",
    shadow_1="rgba(32, 28, 23, 0.06)",
    shadow_2="rgba(32, 28, 23, 0.12)",
)


# ---------- privacy badges ----------


@dataclass(frozen=True, slots=True)
class BadgeStyle:
    text: str
    background: str


def privacy_badge(tier_value: str, palette: Palette) -> BadgeStyle:
    """Colour for an 'On this device' / 'Local network' / 'Cloud' chip.

    On-device is the quiet default; cloud is the one that must catch the eye.
    """
    if tier_value == "on_device":
        return BadgeStyle(palette.success, _tint(palette.success, palette.is_dark))
    if tier_value == "local_network":
        return BadgeStyle(palette.info, _tint(palette.info, palette.is_dark))
    return BadgeStyle(palette.warning, _tint(palette.warning, palette.is_dark))


def _tint(hex_color: str, is_dark: bool) -> str:
    r, g, b = hex_to_rgb(hex_color)
    alpha = 0.16 if is_dark else 0.10
    return f"rgba({r}, {g}, {b}, {alpha})"


# ---------- overlay presets ----------

#: Where each caption line sits inside the panel. Centre is the subtitle
#: convention; left is easier to track with the eye when captions run long.
CaptionAlign = Literal["left", "center", "right"]


@dataclass(frozen=True, slots=True)
class OverlayStyle:
    """Everything the caption painter needs. Presets are data, not branches."""

    background: tuple[int, int, int, int]  # RGBA
    border: tuple[int, int, int, int] | None
    radius: int
    text_color: tuple[int, int, int]
    translation_color: tuple[int, int, int]
    outline: bool
    shadow: bool
    font_size: int
    font_weight: int
    original_ratio: float  # original size relative to the translation line
    dim_original: float  # opacity of the original line
    animate: bool
    border_width: float = 1.0

    # Typography and spacing. These were module constants in overlay_layout
    # until presets needed to disagree about them; defaults reproduce exactly
    # what those constants were, so a preset that says nothing looks unchanged.
    font_family: str = ""  # "" = first available in CAPTION_FONT_STACK
    line_spacing: float = 1.3  # multiple of the font's pixel size
    pad_h: int = 18
    pad_v: int = 12
    align: CaptionAlign = "center"

    min_font_size: int = 0
    """A floor the user may go above but not below. 0 = no floor.

    Replaces a ``preset is HIGH_CONTRAST`` branch in ``overlay_style``: an
    accessibility preset that silently kept a 16px size would defeat itself,
    but that is a property of the preset, not a special case in the resolver.
    """

    shadow_alpha: int = 153
    uncertain_opacity: float = 0.85
    """How far low-confidence text fades. 1.0 disables the signal.

    Opacity is the one channel that trades directly against contrast, so the
    presets built for reading do not spend it: a dimmed uncertain line on the
    old Glass panel measured 2.8:1 over a white window.
    """

    uncertain_italic: bool = True
    outline_width: float = 1.5
    max_lines: int = 4


#: The signature gold-cream translation line: speech in white, understanding in gold.
_GOLD_CREAM = (243, 211, 238)

OVERLAY_PRESETS: dict[OverlayPreset, OverlayStyle] = {
    OverlayPreset.ANIME: OverlayStyle(
        background=(0, 0, 0, 0),
        border=None,
        radius=0,
        text_color=(255, 255, 255),
        translation_color=(231, 211, 255),
        outline=True,
        shadow=True,
        font_size=30,
        font_weight=600,
        original_ratio=0.78,
        dim_original=0.85,
        animate=True,
        outline_width=2.0,
    ),
    OverlayPreset.CUTE: OverlayStyle(
        background=(37, 25, 45, 224),
        border=(240, 167, 205, 100),
        radius=20,
        text_color=(255, 250, 255),
        translation_color=(255, 193, 222),
        outline=False,
        shadow=True,
        font_size=28,
        font_weight=600,
        original_ratio=0.80,
        dim_original=0.85,
        animate=True,
        pad_h=24,
        pad_v=14,
    ),
    OverlayPreset.MINIMAL: OverlayStyle(
        background=(0, 0, 0, 0),
        border=None,
        radius=0,
        text_color=(255, 255, 255),
        translation_color=_GOLD_CREAM,
        outline=True,
        shadow=True,
        font_size=26,
        font_weight=600,
        original_ratio=0.78,
        dim_original=0.80,
        animate=True,
    ),
    OverlayPreset.GLASS: OverlayStyle(
        background=(18, 16, 14, 184),
        border=(244, 239, 230, 26),
        radius=14,
        text_color=(255, 255, 255),
        translation_color=_GOLD_CREAM,
        outline=False,
        shadow=True,
        font_size=26,
        font_weight=600,
        original_ratio=0.78,
        dim_original=0.80,
        animate=True,
    ),
    OverlayPreset.SOLID: OverlayStyle(
        background=(15, 14, 13, 240),
        border=(244, 239, 230, 16),
        radius=12,
        text_color=(242, 244, 247),
        translation_color=_GOLD_CREAM,
        outline=False,
        shadow=True,
        font_size=26,
        font_weight=500,
        original_ratio=0.78,
        dim_original=0.75,
        animate=True,
    ),
    # The familiar film/streaming look: white text, a strong shadow, no box.
    # Deliberately not named after any streaming service — the treatment is the
    # CEA-708 default, not one company's invention, and a mark in a feature name
    # would be both a legal risk and a lie the day they restyle.
    OverlayPreset.CINEMA: OverlayStyle(
        background=(0, 0, 0, 0),
        border=None,
        radius=0,
        text_color=(255, 255, 255),
        translation_color=_GOLD_CREAM,
        outline=False,
        shadow=True,
        font_size=30,
        font_weight=600,
        original_ratio=0.78,
        dim_original=0.80,
        animate=True,
        pad_h=12,
        pad_v=8,
        shadow_alpha=230,
    ),
    # Bigger text without the heavy panel of the accessibility preset: for
    # someone sitting further back, not someone who needs maximum contrast.
    OverlayPreset.LARGE_TEXT: OverlayStyle(
        background=(18, 16, 14, 184),
        border=(244, 239, 230, 26),
        radius=14,
        text_color=(255, 255, 255),
        translation_color=_GOLD_CREAM,
        outline=False,
        shadow=True,
        font_size=40,
        font_weight=600,
        original_ratio=0.80,
        dim_original=0.80,
        animate=True,
        line_spacing=1.4,
        pad_h=22,
        pad_v=14,
        min_font_size=34,
    ),
    # A genuine accessibility mode, not a styling variant: opaque, big, bold,
    # full-contrast original, and no motion.
    OverlayPreset.HIGH_CONTRAST: OverlayStyle(
        background=(0, 0, 0, 255),
        border=None,
        radius=6,
        text_color=(255, 255, 255),
        translation_color=(255, 225, 0),
        outline=False,
        shadow=False,
        font_size=36,
        font_weight=700,
        original_ratio=0.85,
        dim_original=1.0,
        animate=False,
        line_spacing=1.5,
        pad_h=24,
        pad_v=16,
        align="left",
        min_font_size=36,
        uncertain_opacity=1.0,
        uncertain_italic=False,
    ),
}


def overlay_style(preset: OverlayPreset, settings) -> OverlayStyle:
    """Resolve the style for a preset, applying the user's own overrides.

    Overrides are opt-in: an empty string or a zero means "keep whatever the
    preset decided", so someone who never opens the typography controls always
    gets the preset's own judgement rather than a default that overwrites it.
    """
    from dataclasses import replace

    if preset is OverlayPreset.CUSTOM:
        base = OVERLAY_PRESETS[OverlayPreset.GLASS]
        style = replace(
            base,
            background=parse_rgba(settings.custom_bg_color, base.background),
            text_color=hex_to_rgb(settings.custom_text_color),
            translation_color=hex_to_rgb(settings.custom_translation_color),
            border=parse_rgba(settings.custom_border_color, (255, 255, 255, 255))
            if settings.custom_border_width > 0
            else None,
            border_width=settings.custom_border_width,
            radius=settings.custom_radius,
            outline=settings.custom_outline,
            shadow=settings.custom_shadow,
            font_size=settings.font_size,
            font_weight=settings.font_weight,
            dim_original=base.dim_original if settings.dim_original else 1.0,
        )
        return _apply_overrides(style, settings)

    base = OVERLAY_PRESETS[preset]
    style = replace(
        base,
        # The floor is the preset's own, not a branch here: an accessibility
        # preset may not be silently shrunk below the size that makes it one.
        font_size=max(settings.font_size, base.min_font_size),
        # Thickness remains adjustable for every preset.
        font_weight=settings.font_weight,
        dim_original=base.dim_original if settings.dim_original else 1.0,
    )
    return _apply_overrides(style, settings)


#: Panel padding by name, so one small combo replaces two sliders.
PADDING_STEPS: dict[str, tuple[int, int]] = {
    "tight": (10, 7),
    "normal": (18, 12),
    "roomy": (28, 18),
}


def _apply_overrides(style: OverlayStyle, settings) -> OverlayStyle:
    """Layer the user's typography choices over a resolved preset."""
    from dataclasses import replace

    changes: dict[str, object] = {}
    changes["outline_width"] = settings.outline_width
    changes["max_lines"] = settings.max_lines
    if settings.font_family:
        changes["font_family"] = settings.font_family
    if settings.line_spacing:
        changes["line_spacing"] = max(1.0, min(2.5, settings.line_spacing))
    if settings.align in ("left", "center", "right"):
        changes["align"] = settings.align
    if settings.padding in PADDING_STEPS:
        changes["pad_h"], changes["pad_v"] = PADDING_STEPS[settings.padding]
    if not settings.animate:
        # A preset may refuse to animate; none may force it on a user who has
        # asked for stillness.
        changes["animate"] = False
    return replace(style, **changes) if changes else style


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def parse_rgba(value: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Accept #RRGGBB or #RRGGBBAA; fall back rather than raise on junk."""
    try:
        raw = value.lstrip("#")
        if len(raw) == 6:
            r, g, b = hex_to_rgb(raw)
            return (r, g, b, 255)
        if len(raw) == 8:
            r, g, b = hex_to_rgb(raw[:6])
            return (r, g, b, int(raw[6:8], 16))
    except ValueError:
        pass
    return fallback


def rgba_css(color: tuple[int, int, int, int]) -> str:
    r, g, b, a = color
    return f"rgba({r}, {g}, {b}, {a / 255:.3f})"


def palette_for(theme: str, system_is_dark: bool, accent: str = "sakura") -> Palette:
    from dataclasses import replace

    if theme == "dark":
        palette = DARK
    elif theme == "light":
        palette = LIGHT
    else:
        palette = DARK if system_is_dark else LIGHT
    variants = {
        "lavender": (
            ("#CCB7FF", "#D8C8FF", "#B39BE8", "#352A48"),
            ("#68469D", "#553785", "#462D6D", "#EEE6FA"),
        ),
        "ocean": (
            ("#A2D9EE", "#BCE7F6", "#83BCD2", "#203A48"),
            ("#22627C", "#1B5068", "#164253", "#E3F1F7"),
        ),
    }
    if accent in variants:
        color, hover, pressed, subtle = variants[accent][0 if palette.is_dark else 1]
        return replace(
            palette,
            accent=color,
            accent_hover=hover,
            accent_pressed=pressed,
            accent_subtle=subtle,
            focus=color,
        )
    return palette
