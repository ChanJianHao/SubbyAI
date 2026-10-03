"""Colour contrast, measured rather than eyeballed.

Both themes are designed by hand, so a plausible-looking hex can still be
unreadable — light mode shipped with hint text at 3.16:1 and no keyboard focus
indicator at all. These tests compute real WCAG ratios from the tokens, so a
palette edit that harms legibility fails here instead of in front of a user.

Scope is deliberate. WCAG 1.4.11 requires 3:1 only for boundaries that *identify*
a control, so input and button edges are held to it while decorative dividers and
panel fills are not — forcing those to 3:1 would turn every hairline into a rule
and flatten the design for no accessibility gain.
"""

from __future__ import annotations

import pytest

from subbyai.ui import tokens
from subbyai.ui.tokens import DARK, LIGHT, Palette

BODY_TEXT = 4.5
LARGE_TEXT = 3.0
UI_COMPONENT = 3.0

PALETTES = [pytest.param(LIGHT, id="light"), pytest.param(DARK, id="dark")]


def _linear(channel: float) -> float:
    channel /= 255.0
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _resolve(value: str, over: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
    """Token to RGB, compositing rgba() over whatever sits behind it."""
    value = value.strip()
    if not value.startswith("rgba"):
        return tokens.hex_to_rgb(value)
    inner = value[value.index("(") + 1 : value.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    rgb = tuple(int(float(p)) for p in parts[:3])
    alpha = float(parts[3])
    if over is None:
        return rgb  # type: ignore[return-value]
    return tuple(round(c * alpha + o * (1 - alpha)) for c, o in zip(rgb, over, strict=True))  # type: ignore[return-value]


def contrast(foreground: str, background: str, under: str | None = None) -> float:
    base = _resolve(under) if under else None
    bg = _resolve(background, over=base)
    fg = _resolve(foreground, over=bg)
    a, b = _luminance(fg), _luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def surfaces(palette: Palette) -> dict[str, str]:
    return {
        "canvas": palette.canvas,
        "surface": palette.surface,
        "raised": palette.raised,
        "input": palette.input_bg,
        "float": palette.float_,
    }


@pytest.mark.parametrize("palette", PALETTES)
@pytest.mark.parametrize("role", ["text", "text_secondary", "text_tertiary"])
def test_text_is_readable_on_every_surface(palette, role):
    """Including tertiary: it carries hints and timestamps, which people read."""
    colour = getattr(palette, role)
    for name, surface in surfaces(palette).items():
        ratio = contrast(colour, surface)
        assert ratio >= BODY_TEXT, (
            f"{palette.name}: {role} on {name} is {ratio:.2f}:1, needs {BODY_TEXT}:1"
        )


@pytest.mark.parametrize("palette", PALETTES)
def test_disabled_text_is_still_legible(palette):
    """WCAG exempts disabled controls; a disabled label nobody can read is still
    a usability failure, so we hold it to the large-text threshold."""
    for name, surface in (("surface", palette.surface), ("canvas", palette.canvas)):
        ratio = contrast(palette.text_disabled, surface)
        assert ratio >= LARGE_TEXT, (
            f"{palette.name}: disabled text on {name} is {ratio:.2f}:1"
        )


@pytest.mark.parametrize("palette", PALETTES)
def test_accent_is_readable_as_text(palette):
    """The accent is used for links and the emphasised caption line."""
    for name, surface in surfaces(palette).items():
        ratio = contrast(palette.accent, surface)
        assert ratio >= BODY_TEXT, (
            f"{palette.name}: accent on {name} is {ratio:.2f}:1"
        )


@pytest.mark.parametrize("palette", PALETTES)
def test_primary_button_label_is_readable(palette):
    ratio = contrast(palette.accent_on, palette.accent)
    assert ratio >= BODY_TEXT, f"{palette.name}: primary button label {ratio:.2f}:1"


@pytest.mark.parametrize("palette", PALETTES)
@pytest.mark.parametrize("semantic", ["success", "warning", "error", "info"])
def test_semantic_colours_are_readable_as_text(palette, semantic):
    """Banners print these as words, not just as coloured dots."""
    colour = getattr(palette, semantic)
    for name, surface in (("canvas", palette.canvas), ("surface", palette.surface)):
        ratio = contrast(colour, surface)
        assert ratio >= BODY_TEXT, (
            f"{palette.name}: {semantic} text on {name} is {ratio:.2f}:1"
        )


@pytest.mark.parametrize("palette", PALETTES)
@pytest.mark.parametrize("tier", ["on_device", "local_network", "cloud"])
def test_privacy_badges_are_readable(palette, tier):
    """These say whether data leaves the machine. They must never be marginal."""
    style = tokens.privacy_badge(tier, palette)
    ratio = contrast(style.text, style.background, under=palette.surface)
    assert ratio >= BODY_TEXT, f"{palette.name}: {tier} badge is {ratio:.2f}:1"


@pytest.mark.parametrize("palette", PALETTES)
def test_control_borders_identify_the_control(palette):
    """`stroke` draws input and button edges — losing it loses the control."""
    for name, surface in (("surface", palette.surface), ("canvas", palette.canvas)):
        ratio = contrast(palette.stroke, surface)
        assert ratio >= UI_COMPONENT, (
            f"{palette.name}: stroke on {name} is {ratio:.2f}:1, needs {UI_COMPONENT}:1"
        )


@pytest.mark.parametrize("palette", PALETTES)
def test_focus_ring_is_visible_everywhere(palette):
    """Keyboard users need to see where they are, on every surface."""
    for name, surface in surfaces(palette).items():
        ratio = contrast(palette.focus, surface)
        assert ratio >= UI_COMPONENT, (
            f"{palette.name}: focus ring on {name} is {ratio:.2f}:1"
        )


@pytest.mark.parametrize("palette", PALETTES)
def test_hairlines_are_perceptible_even_though_they_are_decorative(palette):
    """Not held to 3:1 — see the module docstring — but must not vanish."""
    for name, surface in (("canvas", palette.canvas), ("surface", palette.surface)):
        ratio = contrast(palette.hairline, surface, under=surface)
        assert ratio >= 1.3, f"{palette.name}: hairline on {name} is {ratio:.2f}:1"


def test_both_themes_define_every_token():
    """A token added to one palette and forgotten in the other is a dark-only fix."""
    light_fields = {f for f in LIGHT.__slots__}
    dark_fields = {f for f in DARK.__slots__}
    assert light_fields == dark_fields
    for field in light_fields:
        if field in ("name", "is_dark"):
            continue
        assert getattr(LIGHT, field), f"light palette is missing {field}"
        assert getattr(DARK, field), f"dark palette is missing {field}"
