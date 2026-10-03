"""Caption presets: the promises each one makes, measured rather than asserted.

A caption sits on top of someone else's picture, so the only honest test of a
preset is against the worst backdrop it can land on. Every preset that paints a
panel is measured over pure white and pure black; the two that paint no panel
are held to a different promise, because contrast against an unknown image is
undefined rather than merely difficult.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

from subbyai.core.events import CaptionSegment, PrivacyTier, TranslationState
from subbyai.core.settings import OriginalPosition, OverlayPreset, OverlaySettings
from subbyai.ui import theme, tokens
from subbyai.ui.overlay_layout import OverlayFonts, build_pair
from subbyai.ui.settings_captions import (
    installed_caption_fonts,
    seed_from_preset,
    weights_for,
)
from subbyai.ui.tokens import OVERLAY_PRESETS, OverlayStyle, overlay_style
from subbyai.ui.widgets import PRESET_LABELS

#: Presets that draw a background. Only these can promise a contrast ratio.
PANELLED = [
    p
    for p in OverlayPreset
    if p is not OverlayPreset.CUSTOM and OVERLAY_PRESETS[p].background[3] > 0
]
PANEL_FREE = [
    p
    for p in OverlayPreset
    if p is not OverlayPreset.CUSTOM and OVERLAY_PRESETS[p].background[3] == 0
]


# ---------- contrast ----------


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _over(rgba: tuple[int, ...], backdrop: tuple[int, int, int]) -> tuple[int, int, int]:
    """Composite a translucent colour over an opaque one."""
    *rgb, alpha = rgba
    weight = alpha / 255
    return tuple(round(f * weight + b * (1 - weight)) for f, b in zip(rgb, backdrop, strict=True))


def _text_ratios(style: OverlayStyle, backdrop: tuple[int, int, int]) -> dict[str, float]:
    """Every line the overlay can paint, against the panel over this backdrop."""
    panel = _over(style.background, backdrop)
    dim = style.dim_original
    uncertain = dim * style.uncertain_opacity
    return {
        "translation": _ratio(_over((*style.translation_color, 255), panel), panel),
        "original": _ratio(_over((*style.text_color, round(dim * 255)), panel), panel),
        "uncertain original": _ratio(
            _over((*style.text_color, round(uncertain * 255)), panel), panel
        ),
    }


@pytest.fixture
def resolved():
    """Resolve a preset exactly as picking its card would."""

    def build(preset: OverlayPreset) -> OverlayStyle:
        settings = OverlaySettings(preset=preset)
        seed_from_preset(settings, preset)
        return overlay_style(preset, settings)

    return build


@pytest.mark.parametrize("preset", PANELLED, ids=lambda p: p.value)
@pytest.mark.parametrize("backdrop", [(255, 255, 255), (0, 0, 0)], ids=["white", "black"])
def test_panelled_presets_stay_readable_over_anything(preset, backdrop, resolved):
    """4.5:1 for every line, on the worst backdrop the desktop can supply.

    Glass shipped at 148 alpha, which put a dimmed uncertain original at 2.8:1
    over a white document window — below even the 3:1 large-text floor, on what
    is a completely ordinary backdrop for a *system* captioner.
    """
    ratios = _text_ratios(resolved(preset), backdrop)
    failures = {role: round(value, 2) for role, value in ratios.items() if value < 4.5}
    assert not failures, f"{preset.value} over {backdrop}: {failures}"


@pytest.mark.parametrize("preset", PANEL_FREE, ids=lambda p: p.value)
def test_panel_free_presets_carry_their_own_edge(preset, resolved):
    """No panel means no provable ratio, so the glyphs must defend themselves."""
    style = resolved(preset)
    assert style.outline or style.shadow, (
        f"{preset.value} paints no background, so without an outline or a shadow "
        "its text has nothing separating it from the picture behind"
    )
    if style.shadow and not style.outline:
        assert style.shadow_alpha >= 200, (
            "a shadow doing the job alone has to be strong enough to read as an edge"
        )


def test_the_default_preset_paints_a_panel():
    """The out-of-the-box look may not be one whose legibility is undefined."""
    default = OverlaySettings().preset
    assert OVERLAY_PRESETS[default].background[3] > 0


# ---------- what each preset promises ----------


def test_the_accessibility_preset_never_spends_contrast_to_signal_doubt():
    """Fading and italicising uncertain text costs exactly the wrong readers.

    Opacity is the one channel that trades directly against contrast, and
    italics are the type treatment the legibility research is most consistently
    negative about. Neither belongs in the preset built for reading.
    """
    style = OVERLAY_PRESETS[OverlayPreset.HIGH_CONTRAST]
    assert style.uncertain_opacity == 1.0
    assert style.uncertain_italic is False
    assert style.dim_original == 1.0
    assert style.animate is False


def test_uncertain_text_is_marked_everywhere_else():
    """The signal still has to exist on the presets that can afford it."""
    for preset in OverlayPreset:
        style = OVERLAY_PRESETS.get(preset)
        if style is None or preset is OverlayPreset.HIGH_CONTRAST:
            continue
        assert style.uncertain_italic or style.uncertain_opacity < 1.0, (
            f"{preset.value} gives the reader no way to tell a guess from a certainty"
        )


def test_the_accessibility_preset_cannot_be_shrunk_below_its_point(resolved):
    settings = OverlaySettings(preset=OverlayPreset.HIGH_CONTRAST)
    settings.font_size = 15  # the smallest the slider allows
    style = overlay_style(OverlayPreset.HIGH_CONTRAST, settings)
    floor = OVERLAY_PRESETS[OverlayPreset.HIGH_CONTRAST].min_font_size
    assert style.font_size == floor >= 36

    settings.font_size = 52  # larger is always allowed
    assert overlay_style(OverlayPreset.HIGH_CONTRAST, settings).font_size == 52


def test_picking_a_preset_shows_its_real_size_in_the_sliders():
    """Seeding, not overriding: the slider must never disagree with the screen.

    Large text is 40px by design. Resolving it against a settings file holding
    the 26px default would render 26 and show 26 — a preset that silently did
    nothing.
    """
    settings = OverlaySettings(preset=OverlayPreset.GLASS)
    seed_from_preset(settings, OverlayPreset.LARGE_TEXT)
    assert settings.font_size == OVERLAY_PRESETS[OverlayPreset.LARGE_TEXT].font_size == 40
    assert overlay_style(OverlayPreset.LARGE_TEXT, settings).font_size == 40


def test_picking_gaming_also_seeds_how_it_behaves():
    """A look alone cannot express "good over a game"."""
    settings = OverlaySettings(preset=OverlayPreset.GLASS)
    seed_from_preset(settings, OverlayPreset.SOLID)
    assert settings.max_pairs == 1, "a stack of captions mid-firefight is unreadable"
    assert settings.click_through is True


def test_custom_keeps_what_the_user_chose():
    settings = OverlaySettings(preset=OverlayPreset.CUSTOM, font_size=19, font_weight=400)
    seed_from_preset(settings, OverlayPreset.CUSTOM)
    assert (settings.font_size, settings.font_weight) == (19, 400)


def test_thickness_reaches_every_preset_not_just_custom():
    """The control was silently dropped for four of five presets."""
    for preset in OverlayPreset:
        settings = OverlaySettings(preset=preset, font_weight=400)
        assert overlay_style(preset, settings).font_weight == 400, (
            f"the Thickness control does nothing on {preset.value}"
        )


def test_every_preset_has_a_name_and_a_reason():
    from subbyai.ui.settings_captions import PRESET_BLURBS

    for preset in OverlayPreset:
        assert PRESET_LABELS.get(preset), f"{preset.value} has no display name"
        assert PRESET_BLURBS.get(preset), f"{preset.value} has no blurb"


def test_no_preset_is_named_after_a_company():
    """The look is a broadcast convention, not any one service's invention."""
    marks = ("netflix", "youtube", "disney", "hbo", "prime video", "hulu")
    for preset, label in PRESET_LABELS.items():
        assert not any(m in label.lower() for m in marks), f"{preset.value} borrows a trademark"


# ---------- user overrides ----------


def test_typography_overrides_apply_and_empty_means_the_preset_decides():
    preset = OverlayPreset.GLASS
    base = OVERLAY_PRESETS[preset]

    untouched = overlay_style(preset, OverlaySettings(preset=preset))
    assert untouched.line_spacing == base.line_spacing
    assert untouched.align == base.align
    assert (untouched.pad_h, untouched.pad_v) == (base.pad_h, base.pad_v)

    chosen = overlay_style(
        preset,
        OverlaySettings(
            preset=preset,
            font_family="Arial",
            line_spacing=1.8,
            align="left",
            padding="roomy",
            animate=False,
        ),
    )
    assert chosen.font_family == "Arial"
    assert chosen.line_spacing == 1.8
    assert chosen.align == "left"
    assert (chosen.pad_h, chosen.pad_v) == tokens.PADDING_STEPS["roomy"]
    assert chosen.animate is False


def test_a_preset_may_refuse_motion_but_never_force_it():
    """2.3.3: turning animation off has to win everywhere."""
    for preset in OverlayPreset:
        settings = OverlaySettings(preset=preset, animate=False)
        assert overlay_style(preset, settings).animate is False


def test_line_spacing_is_clamped_to_something_paintable():
    for value, expected in ((0.2, 1.0), (9.0, 2.5)):
        style = overlay_style(
            OverlayPreset.GLASS, OverlaySettings(preset=OverlayPreset.GLASS, line_spacing=value)
        )
        assert style.line_spacing == expected


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment_actually_moves_the_text(qt_app, align):
    settings = OverlaySettings(preset=OverlayPreset.GLASS, align=align)
    style = overlay_style(OverlayPreset.GLASS, settings)
    fonts = OverlayFonts(style)
    seg = CaptionSegment(
        text="short",
        translation="a considerably longer translated line",
        translation_state=TranslationState.DONE,
        translation_tier=PrivacyTier.ON_DEVICE,
    )
    pair = build_pair(seg, style, fonts, 600.0, OriginalPosition.ABOVE, True)
    xs = [box.x for box in pair.lines if box.static is not None]
    if align == "left":
        assert all(x == 0.0 for x in xs)
    else:
        assert any(x > 0.0 for x in xs), "nothing was offset, so alignment did nothing"


# ---------- fonts ----------
#
# CI runs the offscreen platform, which has no font database at all: every one
# of these would iterate an empty list and report green without testing
# anything. An explicit skip says so out loud rather than banking a false pass.


def _needs_real_fonts() -> None:
    if not QFontDatabase.families():
        pytest.skip("no font database on this platform (offscreen); run locally to exercise")



def test_the_font_picker_only_offers_fonts_that_exist(qt_app):
    """QFont.family() echoes back whatever you asked for, installed or not."""
    _needs_real_fonts()
    for family in installed_caption_fonts():
        assert QFontDatabase.hasFamily(family)
        assert QFontInfo(QFont(family)).family().lower() == family.lower(), (
            f"{family} is offered but Qt silently substitutes something else"
        )


def test_the_caption_stack_falls_back_to_a_latin_ui_face(qt_app):
    """Latin captions resolve to an installed Latin UI typeface."""
    _needs_real_fonts()
    cjk_only = {"Yu Gothic UI", "Hiragino Sans", "MS Gothic", "SimSun", "Malgun Gothic"}
    resolved = theme._first_available(tokens.CAPTION_FONT_STACK)
    assert resolved not in cjk_only, f"Latin captions would render in {resolved}"


def test_font_stacks_end_in_a_real_family(qt_app):
    """Qt does not resolve the CSS generics: all three become Tahoma."""
    for name, stack in (
        ("caption", tokens.CAPTION_FONT_STACK),
        ("ui", tokens.UI_FONT_STACK),
        ("mono", tokens.MONO_FONT_STACK),
    ):
        for generic in ("system-ui", "sans-serif", "monospace", "serif"):
            assert generic not in stack, f"{name} stack ends in {generic}, which Qt cannot resolve"


def test_thickness_offers_only_weights_the_family_really_has(qt_app):
    """Verdana and Tahoma ship Regular and Bold and nothing between."""
    _needs_real_fonts()
    for family in installed_caption_fonts():
        offered = weights_for(family)
        assert offered, f"{family} offers no weights at all"
        styles = {s.lower() for s in QFontDatabase.styles(family)}
        for label, _ in offered:
            assert label.lower() in styles or label in ("Regular", "Bold")


def test_a_chosen_font_survives_into_the_caption(qt_app):
    _needs_real_fonts()
    family = next(iter(installed_caption_fonts()), "")
    if not family:
        pytest.skip("none of the offered caption fonts are installed here")
    settings = OverlaySettings(preset=OverlayPreset.GLASS, font_family=family)
    style = overlay_style(OverlayPreset.GLASS, settings)
    assert OverlayFonts(style).translation.family() == family


def test_an_uninstalled_font_degrades_to_the_stack(qt_app):
    """A settings file copied between machines must not render arbitrarily."""
    _needs_real_fonts()
    settings = OverlaySettings(preset=OverlayPreset.GLASS, font_family="NoSuchFace 9000")
    fonts = OverlayFonts(overlay_style(OverlayPreset.GLASS, settings))
    assert fonts.translation.family() == theme._first_available(tokens.CAPTION_FONT_STACK)


def test_no_dead_style_fields():
    """blur_behind was set by five presets and read by nothing for a release.

    The Glass card told users it blurred what was behind it. It never did.
    """
    for field in OverlayStyle.__dataclass_fields__:
        assert field != "blur_behind"


# ---------- the settings panel ----------
#
# These drive CaptionsSection itself. Both bugs below were found by clicking
# through the real widget, not by reading it: resolving a style correctly is
# not the same as the panel that edits it behaving.


@pytest.fixture
def captions_panel(qt_app):
    """The real section over an in-memory store, so no settings file is touched."""
    from subbyai.core.settings import Settings
    from subbyai.ui.settings_captions import CaptionsSection

    class Store:
        def __init__(self) -> None:
            self.settings = Settings()

        def save(self, *args, **kwargs) -> None: ...
        def apply(self, *args, **kwargs) -> None: ...
        def notify(self, *args, **kwargs) -> None: ...

    store = Store()
    return CaptionsSection(store), store.settings


def test_a_still_preset_does_not_switch_off_animation_for_the_others(captions_panel):
    """Accessibility never animates, but that is the preset's business.

    Writing the checkbox state back to settings meant one visit to Accessibility
    silently turned fades off for every other preset, permanently, without the
    user ever touching the control.
    """
    panel, settings = captions_panel
    assert settings.overlay.animate is True

    panel._on_preset(OverlayPreset.HIGH_CONTRAST.value)
    assert panel.animate.isEnabled() is False
    assert panel.animate.isChecked() is False, "the preset does not animate; say so"
    assert settings.overlay.animate is True, "but the stored preference is not the UI's to change"

    panel._on_preset(OverlayPreset.GLASS.value)
    assert panel.animate.isChecked() is True
    assert settings.overlay.animate is True


def test_the_user_can_still_turn_animation_off(captions_panel):
    panel, settings = captions_panel
    panel.animate.setChecked(False)
    assert settings.overlay.animate is False


def test_changing_font_does_not_corrupt_the_weight(captions_panel):
    """clear() emits currentIndexChanged(-1), and itemData(-1) is None."""
    panel, settings = captions_panel
    before = settings.overlay.font_weight
    for index in range(panel.font.count()):
        panel.font.setCurrentIndex(index)
        assert isinstance(settings.overlay.font_weight, int)
        assert 100 <= settings.overlay.font_weight <= 900
    panel.font.setCurrentIndex(0)
    assert settings.overlay.font_weight == before


def test_every_preset_card_can_be_picked_without_error(captions_panel):
    panel, settings = captions_panel
    for preset in OverlayPreset:
        panel._on_preset(preset.value)
        assert settings.overlay.preset is preset
        assert panel.custom_group.isEnabled() is (preset is OverlayPreset.CUSTOM)
