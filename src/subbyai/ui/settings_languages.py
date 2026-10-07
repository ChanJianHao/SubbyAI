"""What you hear, what you read, and who translates between them.

The language table comes from ``subbyai.languages``, which onboarding also uses:
one list, so the two screens can never offer different languages. Names are
shown, codes are stored, and no code ever reaches the screen.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import SettingsStore
from ..languages import AUTO_DETECT, AUTO_DETECT_LABEL, sorted_languages
from ..translation import BUILTIN_PROVIDER_ID
from .motion_widgets import MotionToggle as QCheckBox
from .settings_intelligence import builtin_config, provider_tier
from .settings_widgets import Group, LanguagePicker, PrivacyBadge, SettingsSection, show_tier
from .tokens import SPACE
from .widgets import compact_combo, elide_label

__all__ = ["RECONNECT_NOTE", "LanguagesSection"]

HEAR_ENTRIES = ((AUTO_DETECT, AUTO_DETECT_LABEL), *sorted_languages())
SHOW_ENTRIES = ((AUTO_DETECT, "Just show what I hear"), *sorted_languages())

#: Shown beside the few settings the capture loop only reads when it starts.
RECONNECT_NOTE = "Captions reconnect by themselves when you change this."


class LanguagesSection(SettingsSection):
    """Source language, target language, and the active translator."""

    restart_capture_requested = Signal()
    change_provider_requested = Signal()

    def __init__(self, store: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(store, parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        languages = Group("Languages")
        self.hear = LanguagePicker(HEAR_ENTRIES, self)
        self.hear.code_changed.connect(self._on_hear)
        languages.add_row("Language you'll hear", self.hear, RECONNECT_NOTE)
        self.show_in = LanguagePicker(SHOW_ENTRIES, self)
        self.show_in.code_changed.connect(self._on_show_in)
        languages.add_row("Show captions in", self.show_in)
        self.show_original = QCheckBox(self)
        self.show_original.toggled.connect(self._on_show_original)
        self.original_row = languages.add_row("Also show the original language", self.show_original)
        column.addWidget(languages)

        # Two engines do two different jobs, and confusing them is the single
        # most common misunderstanding about this kind of app: speech
        # recognition writes down what was said, translation turns that text
        # into another language. Naming both, with where each runs, means nobody
        # has to guess whether they need to install an AI to get subtitles.
        pipeline = Group(
            "How captions are made",
            "Speech recognition writes down what is said. Translation is a "
            "separate step that turns those words into your language.",
        )
        self.asr_name = elide_label(QLabel("", self), minimum=80)
        self.asr_badge = PrivacyBadge(parent=self)
        pipeline.add_row("Speech to text", _engine_row(self.asr_name, self.asr_badge, self))
        self.mt_name = elide_label(QLabel("", self), minimum=80)
        self.mt_badge = PrivacyBadge(parent=self)
        pipeline.add_row("Translation", _engine_row(self.mt_name, self.mt_badge, self))
        column.addWidget(pipeline)

        translation = Group("Translation")
        provider = QWidget(self)
        row = QHBoxLayout(provider)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["sm"])
        # The provider name gives way before the badge or the button: knowing
        # *where* translation runs matters more than reading its full name, and
        # this row otherwise set a 690px floor under the whole settings window.
        self.provider_name = elide_label(QLabel("", self), minimum=80)
        self.provider_badge = PrivacyBadge(parent=self)
        self.change_button = QPushButton("Change", self)
        self.change_button.setObjectName("quiet")
        self.change_button.clicked.connect(self.change_provider_requested)
        row.addWidget(self.provider_name)
        row.addWidget(self.provider_badge)
        row.addWidget(self.change_button)
        translation.add_row("Translated by", provider)

        self.on_unavailable = compact_combo(QComboBox(self))
        self.on_unavailable.addItem("Keep the original and tell me", "show_original")
        self.on_unavailable.addItem("Pause captions", "pause")
        self.on_unavailable.currentIndexChanged.connect(self._on_unavailable)
        translation.add_row("When the translator is unavailable", self.on_unavailable)
        column.addWidget(translation)
        self.refresh()

    def refresh(self) -> None:
        captions = self.settings.captions
        with self.quiet():
            self.hear.set_code(captions.source_language)
            self.show_in.set_code(captions.target_language)
            self.show_original.setChecked(captions.show_original)
            index = self.on_unavailable.findData(
                self.settings.intelligence.on_translator_unavailable
            )
            self.on_unavailable.setCurrentIndex(max(0, index))
        self.original_row.setEnabled(self.settings.translation_enabled)
        self.sync_provider()

    def sync_engines(self) -> None:
        """Say which engine does each job, and where each one runs."""
        from ..core.events import PrivacyTier
        from .widgets import tier_label

        quality = tier_label(self.settings.captions.quality)
        override = self.settings.captions.model_override
        remote = self.settings.processing.asr_backend == "remote"
        self.asr_name.setText(
            "Selected speech server" if remote else f"Local speech · {override or quality}"
        )
        self.asr_name.setToolTip(
            "Audio is sent to your chosen speech server."
            if remote
            else "Speech recognition runs on this computer. It writes down what is said."
        )
        from ..translation.base import tier_for_url

        show_tier(
            self.asr_badge,
            tier_for_url(self.settings.processing.base_url) if remote else PrivacyTier.ON_DEVICE,
        )

        if not self.settings.translation_enabled:
            self.mt_name.setText("Off")
            self.mt_name.setToolTip("Captions stay in the language that was spoken.")
            show_tier(self.mt_badge, None)
            return
        intelligence = self.settings.intelligence
        by_id = {p.id: p for p in intelligence.providers}
        for provider_id in intelligence.translation_order:
            config = by_id.get(provider_id)
            if provider_id == BUILTIN_PROVIDER_ID and config is None:
                config = builtin_config()
            if config is not None and config.enabled:
                self.mt_name.setText(config.label or config.id)
                show_tier(self.mt_badge, provider_tier(config))
                return
        self.mt_name.setText("Nothing set up yet")
        show_tier(self.mt_badge, None)

    def sync_provider(self) -> None:
        """Name and badge the translator that will actually be tried first."""
        self.sync_engines()
        intelligence = self.settings.intelligence
        by_id = {p.id: p for p in intelligence.providers}
        for provider_id in intelligence.translation_order:
            config = by_id.get(provider_id)
            if provider_id == BUILTIN_PROVIDER_ID and config is None:
                config = builtin_config()
            if config is not None and config.enabled:
                self.provider_name.setText(config.label or config.id)
                show_tier(self.provider_badge, provider_tier(config))
                return
        self.provider_name.setText("Nothing set up yet")
        show_tier(self.provider_badge, None)

    def _on_hear(self, code: str) -> None:
        self.settings.captions.source_language = code
        self.apply("captions")
        if not self._muted:
            self.restart_capture_requested.emit()

    def _on_show_in(self, code: str) -> None:
        self.settings.captions.target_language = code
        self.original_row.setEnabled(self.settings.translation_enabled)
        self.apply("captions")
        if not self._muted:
            self.restart_capture_requested.emit()

    def _on_show_original(self, checked: bool) -> None:
        self.settings.captions.show_original = checked
        self.apply("captions")

    def _on_unavailable(self, index: int) -> None:
        self.settings.intelligence.on_translator_unavailable = str(
            self.on_unavailable.itemData(index) or "show_original"
        )
        self.apply("intelligence")


def _engine_row(name: QLabel, badge: PrivacyBadge, parent: QWidget) -> QWidget:
    """Name of an engine plus where it runs, as one control."""
    holder = QWidget(parent)
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE["sm"])
    row.addWidget(name, 1)
    row.addWidget(badge)
    return holder
