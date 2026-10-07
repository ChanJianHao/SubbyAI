"""Persistence choices applied before text reaches the writer queue."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.events import CaptionSegment, TranslationState
from ..core.settings import HistorySettings


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    enabled: bool = False
    original: bool = True
    translation: bool = True

    @classmethod
    def from_settings(cls, settings: HistorySettings) -> HistoryPolicy:
        return cls(
            settings.enabled and settings.retention_days != -1,
            settings.save_original,
            settings.save_translation,
        )

    @property
    def metadata_only(self) -> bool:
        return not self.original and not self.translation

    def for_storage(self, segment: CaptionSegment) -> CaptionSegment | None:
        if not self.enabled or self.metadata_only:
            return None
        translated = self.translation and segment.translation_state is TranslationState.DONE
        if not self.original and not translated:
            return None
        return replace(
            segment,
            text=segment.text if self.original else "",
            translation=segment.translation if translated else None,
            translation_state=TranslationState.DONE if translated else TranslationState.NONE,
            target_language=segment.target_language if translated else None,
            translation_provider=None,
            translation_tier=None,
        )
