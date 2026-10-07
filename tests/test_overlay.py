"""Overlay behaviour, offscreen.

The assertions here are the UX laws written down: a caption never moves under
the reader, the panel never takes focus, and an empty panel always says why.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication, QPixmap

from subbyai.core.events import CaptionSegment, PrivacyTier, TranslationState
from subbyai.core.health import HealthReport, HealthState
from subbyai.core.settings import OriginalPosition, OverlayPreset, Settings
from subbyai.ui.overlay import CaptionOverlay
from subbyai.ui.overlay_layout import ROLE_ORIGINAL, ROLE_TRANSLATION


@pytest.fixture
def settings():
    settings = Settings()
    settings.captions.target_language = "en"
    settings.captions.source_language = "fr"
    settings.overlay.max_pairs = 2
    return settings


@pytest.fixture
def overlay(qt_app, settings):
    widget = CaptionOverlay(settings)
    yield widget
    widget.close()
    widget.deleteLater()


def _seg(text: str, **kwargs) -> CaptionSegment:
    return CaptionSegment(text=text, language="fr", **kwargs)


def _translated(seg: CaptionSegment, text: str) -> CaptionSegment:
    return seg.with_translation(text, "en", "builtin", PrivacyTier.ON_DEVICE)


def _lines(pair, role):
    return [box for box in pair.lines if box.role == role]


def _text(pair, role) -> str:
    """Wrapped lines rejoined — assertions care about content, not the wrap point."""
    return " ".join(box.text for box in _lines(pair, role) if box.text)


def _roles(pair) -> list[str]:
    """Roles in paint order, collapsing a wrapped line back to one entry."""
    ordered: list[str] = []
    for box in pair.lines:
        if not ordered or ordered[-1] != box.role:
            ordered.append(box.role)
    return ordered


def test_display_changes_reflow_and_keep_the_whole_overlay_visible(overlay, monkeypatch):
    from types import SimpleNamespace

    area = QRect(0, 0, 280, 600)
    overlay.resize(900, 200)
    overlay.show_segment(_seg("A caption that should wrap onto a narrow portrait display."))
    monkeypatch.setattr(overlay, "screen", lambda: SimpleNamespace(availableGeometry=lambda: area))
    overlay._display_metrics_changed()
    assert area.contains(overlay.geometry())
    assert overlay.width() <= area.width()
    assert len(_lines(overlay.visible_pairs[0], ROLE_ORIGINAL)) > 1


# ---------- the caption never moves under the reader ----------


def test_late_translation_fills_a_reserved_slot(overlay):
    seg = _seg("Bonjour, ravi de vous rencontrer.")
    overlay.show_segment(seg)
    pair = overlay.visible_pairs[0]
    before = [(box.text, box.y, box.height) for box in _lines(pair, ROLE_ORIGINAL)]
    reserved = _lines(pair, ROLE_TRANSLATION)
    assert before, "the original must show immediately"
    assert [box.static for box in reserved] == [None], "translation height is reserved, empty"

    overlay.update_segment(_translated(seg, "Hello, nice to meet you."))

    assert len(overlay.visible_pairs) == 1, "an update must not add a pair"
    pair = overlay.visible_pairs[0]
    after = [(box.text, box.y, box.height) for box in _lines(pair, ROLE_ORIGINAL)]
    assert after == before, "the original moved when the translation landed"
    assert _text(pair, ROLE_TRANSLATION) == "Hello, nice to meet you."


def test_update_of_an_unknown_segment_is_ignored(overlay):
    overlay.update_segment(_seg("never shown", translation_state=TranslationState.DONE))
    assert overlay.visible_pairs == []


def test_translation_line_is_gold_and_original_is_dimmed(overlay):
    seg = _seg("Bonjour.")
    overlay.show_segment(seg)
    overlay.update_segment(_translated(seg, "Hello."))
    pair = overlay.visible_pairs[0]
    style = overlay.caption_style
    assert _lines(pair, ROLE_TRANSLATION)[0].color == style.translation_color
    original = _lines(pair, ROLE_ORIGINAL)[0]
    assert original.color == style.text_color
    assert original.opacity == pytest.approx(style.dim_original)


# ---------- stacking ----------


def test_max_pairs_bounds_what_is_shown(overlay, settings):
    for index in range(4):
        overlay.show_segment(_seg(f"line {index}"))
    assert len(overlay.visible_pairs) == settings.overlay.max_pairs
    assert [box.text for pair in overlay.visible_pairs for box in _lines(pair, ROLE_ORIGINAL)] == [
        "line 2",
        "line 3",
    ]


def test_original_position_orders_or_hides_the_pair(overlay, settings):
    seg = _seg("Bonjour.")
    overlay.show_segment(seg)
    overlay.update_segment(_translated(seg, "Hello."))

    assert _roles(overlay.visible_pairs[0]) == [ROLE_ORIGINAL, ROLE_TRANSLATION]

    settings.overlay.original_position = OriginalPosition.BELOW
    overlay.apply_settings(settings)
    assert _roles(overlay.visible_pairs[0]) == [ROLE_TRANSLATION, ROLE_ORIGINAL]

    settings.overlay.original_position = OriginalPosition.HIDDEN
    overlay.apply_settings(settings)
    assert _roles(overlay.visible_pairs[0]) == [ROLE_TRANSLATION]


def test_hidden_original_still_shows_when_there_is_no_translation(qt_app):
    """Never a bare empty box: with translation off, "hide original" cannot win."""
    settings = Settings()
    settings.overlay.original_position = OriginalPosition.HIDDEN
    overlay = CaptionOverlay(settings)
    overlay.show_segment(_seg("Nothing to translate into."))
    assert _roles(overlay.visible_pairs[0]) == [ROLE_ORIGINAL]
    overlay.close()


def test_each_line_carries_the_font_it_was_prepared_with(overlay, settings):
    """``drawStaticText`` re-lays out against the painter's font, so every line
    has to hand its own font to the painter or the panel paints at UI size."""
    seg = _seg("Bonjour.")
    overlay.show_segment(seg)
    overlay.update_segment(_translated(seg, "Hello."))
    pair = overlay.visible_pairs[0]
    style = overlay.caption_style

    translation = _lines(pair, ROLE_TRANSLATION)[0]
    original = _lines(pair, ROLE_ORIGINAL)[0]
    assert translation.font is not None
    assert translation.font.pixelSize() == settings.overlay.font_size
    assert original.font.pixelSize() == round(
        settings.overlay.font_size * style.original_ratio
    )


def test_uncertain_segments_are_italic_and_dimmer(overlay):
    overlay.show_segment(_seg("probably this", confidence=0.2))
    pair = overlay.visible_pairs[0]
    assert pair.uncertain is True
    assert all(box.italic for box in pair.lines)
    original = _lines(pair, ROLE_ORIGINAL)[0]
    assert original.opacity < overlay.caption_style.dim_original


# ---------- presets ----------


def test_preset_change_resolves_a_new_style(overlay, settings):
    glass = overlay.caption_style
    settings.overlay.preset = OverlayPreset.HIGH_CONTRAST
    overlay.apply_settings(settings)
    contrast = overlay.caption_style
    assert contrast != glass
    assert contrast.background[3] == 255
    assert contrast.animate is False, "the accessibility preset must not animate"


@pytest.mark.parametrize("preset", list(OverlayPreset))
def test_paint_runs_for_every_preset(overlay, settings, preset):
    settings.overlay.preset = preset
    overlay.apply_settings(settings)
    seg = _seg("Bonjour, ravi de vous rencontrer.", confidence=0.3)
    overlay.show_segment(seg)
    overlay.show_segment(_translated(_seg("On commence."), "We start now."))
    overlay.set_click_through(True)
    overlay.scrub_back()

    pixmap = QPixmap(overlay.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    overlay.render(pixmap)
    assert not pixmap.isNull()


# ---------- click-through ----------


def test_click_through_sets_the_mouse_attribute(overlay):
    overlay.set_click_through(True)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    overlay.set_click_through(False)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False


# ---------- geometry ----------


def test_geometry_is_saved_and_restored_per_screen(overlay, settings):
    overlay.setGeometry(QRect(40, 40, 480, 120))
    overlay.save_geometry()
    assert len(settings.overlay.geometry) == 1

    overlay.setGeometry(QRect(0, 0, 320, 80))
    overlay.restore_geometry()
    assert overlay.geometry().topLeft() == QPoint(40, 40)
    assert overlay.width() == 480


def test_offscreen_geometry_is_recentred(overlay, settings):
    overlay.setGeometry(QRect(-6000, -6000, 480, 120))
    overlay.save_geometry()
    overlay.restore_geometry()

    area = QGuiApplication.primaryScreen().availableGeometry()
    assert area.contains(overlay.geometry().center())


# ---------- scrubbing ----------


def test_scrub_walks_the_ring_and_live_returns(overlay):
    for index in range(4):
        overlay.show_segment(_seg(f"line {index}"))
    seen = []
    overlay.scrub_changed.connect(seen.append)

    overlay.scrub_back()
    assert overlay.scrub_offset == 1
    assert _lines(overlay.visible_pairs[-1], ROLE_ORIGINAL)[0].text == "line 2"

    overlay.scrub_back()
    assert _lines(overlay.visible_pairs[-1], ROLE_ORIGINAL)[0].text == "line 1"

    overlay.scrub_forward()
    assert overlay.scrub_offset == 1

    overlay.go_live()
    assert overlay.scrub_offset == 0
    assert _lines(overlay.visible_pairs[-1], ROLE_ORIGINAL)[0].text == "line 3"
    assert seen == [1, 2, 1, 0]


def test_scrubbing_back_over_a_fading_pair_shows_it_once(overlay):
    """The pair that just aged out is still fading; scrubbing must not double it."""
    for index in range(3):
        overlay.show_segment(_seg(f"line {index}"))
    overlay.scrub_back()

    painted = [_text(pair, ROLE_ORIGINAL) for pair in overlay._render_pairs()]
    assert painted == ["line 0", "line 1"]


def test_scrub_stops_at_the_ends(overlay):
    overlay.show_segment(_seg("only"))
    overlay.scrub_back()
    assert overlay.scrub_offset == 0
    overlay.scrub_forward()
    assert overlay.scrub_offset == 0


def test_clear_drops_the_ring_and_the_scrub_position(overlay):
    for index in range(3):
        overlay.show_segment(_seg(f"line {index}"))
    overlay.scrub_back()
    overlay.clear()
    assert overlay.visible_pairs == []
    assert overlay.scrub_offset == 0


# ---------- health, auto-hide, focus ----------


def test_empty_panel_names_its_own_state(overlay):
    detail = "We can't hear anything. Check that something is playing and isn't muted."
    overlay.set_health(HealthReport(HealthState.SILENT, detail))
    painted = overlay._render_pairs()
    assert " ".join(box.text for box in painted[0].lines) == detail
    assert overlay.auto_hide_pending is False, "a notice must not fade away unread"


def test_captions_win_over_a_health_notice(overlay):
    overlay.set_health(HealthReport(HealthState.SILENT, "We can't hear anything."))
    overlay.show_segment(_seg("but here is speech"))
    assert _text(overlay._render_pairs()[0], ROLE_ORIGINAL) == "but here is speech"


def test_listening_never_paints_a_notice(overlay):
    overlay.set_health(HealthReport(HealthState.LISTENING, ""))
    assert overlay._render_pairs() == []


def test_auto_hide_starts_on_speech_and_pin_stops_it(overlay, settings):
    overlay.show_segment(_seg("hello"))
    assert overlay.auto_hide_pending is True

    overlay._on_pin(True)
    assert overlay.auto_hide_pending is False

    overlay._on_pin(False)
    settings.overlay.auto_hide = False
    overlay.apply_settings(settings)
    assert overlay.auto_hide_pending is False


def test_the_overlay_never_takes_focus(overlay):
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert overlay.focusPolicy() is Qt.FocusPolicy.NoFocus
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool


def test_hide_button_reports_upward_without_stopping_captions(overlay, settings):
    seen = []
    overlay.hidden_by_user.connect(lambda: seen.append(True))
    overlay._on_hide_pressed()
    assert seen == [True]
    assert settings.overlay.visible is False


# ---------- preview ----------


def test_placement_preview_shows_sample_pairs(overlay):
    overlay.show_placement_preview(True)
    pairs = overlay.visible_pairs
    assert pairs, "the preview must show something to place"
    assert all(_lines(pair, ROLE_TRANSLATION)[0].text for pair in pairs)
    assert overlay.auto_hide_pending is False

    overlay.show_placement_preview(False)
    assert overlay.visible_pairs == []
