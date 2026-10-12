"""Long recognized/translated captions are paged, not silently elided."""

from subbyai.core.events import CaptionSegment, PrivacyTier, TranslationState
from subbyai.core.settings import OriginalPosition, Settings
from subbyai.ui.overlay import CaptionOverlay
from subbyai.ui.overlay_layout import ROLE_ORIGINAL, ROLE_TRANSLATION


def test_overlay_pages_preserve_every_word_at_narrow_width(qt_app):
    settings = Settings()
    settings.overlay.max_lines = 1
    settings.overlay.max_pairs = 1
    settings.captions.target_language = ""
    overlay = CaptionOverlay(settings)
    overlay.resize(320, 100)
    source = " ".join(f"word{index}" for index in range(45))
    segment = CaptionSegment(source)
    overlay.show_segment(segment)
    seen = []
    count = overlay.visible_pairs[0].page_count
    assert count > 1
    for _ in range(count):
        seen.extend(
            line.text for line in overlay.visible_pairs[0].lines if line.role == ROLE_ORIGINAL
        )
        overlay._advance_caption_pages()
    assert " ".join(seen) == source
    assert not any("…" in line for line in seen)
    overlay.close()
    overlay.deleteLater()


def test_translation_revision_resets_pages_and_failed_translation_shows_original(qt_app):
    settings = Settings()
    settings.captions.target_language = "fr"
    settings.overlay.original_position = OriginalPosition.HIDDEN
    settings.overlay.max_lines = 1
    overlay = CaptionOverlay(settings)
    overlay.resize(320, 100)
    original = CaptionSegment(
        "The original stays available.", translation_state=TranslationState.PENDING
    )
    translated = original.with_translation(
        " ".join(["traduction"] * 25), "fr", "test", PrivacyTier.ON_DEVICE
    )
    overlay.show_segment(translated)
    seen = []
    count = overlay.visible_pairs[0].page_count
    for _ in range(count):
        seen.extend(
            line.text for line in overlay.visible_pairs[0].lines if line.role == ROLE_TRANSLATION
        )
        overlay._advance_caption_pages()
    assert " ".join(seen) == translated.translation
    updated = original.with_translation("Revised.", "fr", "test", PrivacyTier.ON_DEVICE)
    overlay.update_segment(updated)
    assert overlay.visible_pairs[0].page_index == 0
    overlay.update_segment(original.with_translation_state(TranslationState.FAILED))
    assert any(line.role == ROLE_ORIGINAL for line in overlay.visible_pairs[0].lines)
    overlay.close()
    overlay.deleteLater()
