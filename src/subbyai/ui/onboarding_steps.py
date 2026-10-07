"""The wizard's ``Step`` contract and the pages that only record a preference.

Each page owns exactly one decision, writes it into the ``Settings`` object it
was handed, and knows nothing about the pipeline, the store, or the wizard that
contains it. Every page also answers ``apply_defaults``, because Skip has to
leave something usable behind rather than a half-configured app.

The two pages that must interrogate the machine before they can offer anything
live in ``onboarding_hardware``.
"""

from __future__ import annotations

import logging
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..branding import APP_NAME
from ..core.settings import OriginalPosition, OverlayPreset, Settings
from ..languages import (
    AUTO_DETECT,
    AUTO_DETECT_LABEL,
    default_target_language,
    language_name,
    sorted_languages,
)
from . import theme
from .motion_widgets import MotionToggle as QCheckBox
from .onboarding_widgets import DualPreview, KeyCapRow, SelectCard, StyleCard
from .tokens import RADIUS, SPACE
from .widgets import (
    PRESET_LABELS,
    TIER_LABELS,
    LanguagePicker,
    preset_label,
    wrap_label,
)

#: The wizard asks what you will hear (auto-detect allowed) and what to read it
#: in (a real language, since "no translation" is expressed by leaving it equal).
_HEAR_ENTRIES = ((AUTO_DETECT, AUTO_DETECT_LABEL), *sorted_languages())
_READ_ENTRIES = tuple(sorted_languages())

log = logging.getLogger(__name__)

INTENT_CARDS: tuple[tuple[str, str, str], ...] = (
    ("video", "Videos & streams", "Foreign films, lectures, live streams"),
    ("games", "Games", "Story-heavy games without subtitles"),
    ("calls", "Calls & meetings", "Follow along in real time"),
    ("everything", "A bit of everything", "Start with something that suits most things"),
)

ACCESSIBILITY_LABEL = "I'm deaf or hard of hearing — set up for accessibility"
ACCESSIBLE_FONT_SIZE = 32


def chip(text: str) -> QLabel:
    colors = theme.current()
    label = QLabel(text)
    label.setStyleSheet(
        f"background:{colors.raised};border:1px solid {colors.hairline};"
        f"border-radius:{RADIUS['full']}px;padding:5px 12px;color:{colors.text_secondary};"
    )
    return label


class Step(QWidget):
    """One page. Subclasses add content under the title and override the hooks."""

    title = ""
    primary_label = "Continue"

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(SPACE["md"])
        self._title = QLabel(self.title)
        self._title.setObjectName("display")
        self._title.setWordWrap(True)
        self._root.addWidget(self._title)

    def add_body(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("secondary")
        label.setWordWrap(True)
        self._root.addWidget(label)
        return label

    def on_enter(self) -> None:
        """Called every time this page becomes the visible one."""

    def commit(self) -> None:
        """Write this page's choices; called when the user moves forward."""

    def apply_defaults(self) -> None:
        """Write what Skip promises for this page."""


class WelcomeStep(Step):
    title = "Captions for everything you play"
    primary_label = "Get started"

    restore_requested = Signal()

    def __init__(
        self, settings: Settings, show_restore: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(settings, parent)
        from .mascot import Mochi

        glyph = Mochi(self, size=88)
        self._root.insertWidget(0, glyph)
        self.add_body(
            f"{APP_NAME} listens to your computer's sound and shows live captions — and "
            "translations — over any app. Local processing keeps audio on this device."
        )
        self.save_history = QCheckBox("Save transcripts on this computer")
        self.save_history.setChecked(settings.history.enabled)
        self.save_history.toggled.connect(
            lambda enabled: setattr(settings.history, "enabled", enabled)
        )
        self._root.addWidget(self.save_history)
        self._root.addStretch(1)
        self._note = QLabel("")
        self._note.setObjectName("secondary")
        self._note.setWordWrap(True)
        self._note.hide()
        self._root.addWidget(self._note)
        # Short enough not to set a floor under the wizard's width; the step
        # above already explains what restoring means.
        self._restore = QPushButton("Restore my previous settings")
        self._restore.setObjectName("quiet")
        self._restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore.clicked.connect(self.restore_requested)
        self._restore.setVisible(show_restore)
        self._root.addWidget(self._restore, 0, Qt.AlignmentFlag.AlignLeft)

    def show_restore_result(self, restored: bool) -> None:
        """Say what happened; a link that does nothing visible is a broken link."""
        self._note.setText(
            "Your earlier settings are back."
            if restored
            else f"We couldn't find earlier {APP_NAME} settings on this computer."
        )
        self._note.show()


class UsageStep(Step):
    title = "What will you use captions for?"

    games_changed = Signal(bool)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self.add_body("Pick as many as you like. This only sets your starting point.")
        # Remembered so unticking a card returns to what the user already had,
        # rather than to whatever this class thinks the factory default is.
        overlay = settings.overlay
        self._base = (overlay.preset, overlay.auto_hide, overlay.font_size)

        grid = QGridLayout()
        grid.setSpacing(SPACE["md"])
        self._cards: dict[str, SelectCard] = {}
        for index, (key, title, subtitle) in enumerate(INTENT_CARDS):
            card = SelectCard(title, subtitle)
            card.toggled.connect(self._apply)
            grid.addWidget(card, index // 2, index % 2)
            self._cards[key] = card
        self._root.addLayout(grid)

        self._accessibility = SelectCard(
            ACCESSIBILITY_LABEL,
            "Bigger, opaque captions that never fade away, with less motion.",
        )
        self._accessibility.toggled.connect(self._apply)
        self._root.addWidget(self._accessibility)
        self._root.addStretch(1)
        self._restore_selection()

    @property
    def games_selected(self) -> bool:
        return self._cards["games"].isChecked()

    def select(self, key: str, on: bool = True) -> None:
        card = self._cards.get(key) if key != "accessibility" else self._accessibility
        if card is not None:
            card.setChecked(on)

    def apply_defaults(self) -> None:
        if not self.settings.general.usage_intents:
            self.settings.general.usage_intents = ["everything"]

    def _restore_selection(self) -> None:
        for key, card in self._cards.items():
            card.blockSignals(True)
            card.setChecked(key in self.settings.general.usage_intents)
            card.blockSignals(False)
        self._accessibility.blockSignals(True)
        self._accessibility.setChecked(self.settings.general.accessibility_mode)
        self._accessibility.blockSignals(False)

    def _apply(self) -> None:
        """Recompute the whole bundle so unticking undoes what ticking did."""
        intents = [key for key, card in self._cards.items() if card.isChecked()]
        preset, auto_hide, font_size = self._base
        if "games" in intents:
            preset = OverlayPreset.SOLID
        if "calls" in intents:
            auto_hide = False
        accessible = self._accessibility.isChecked()
        if accessible:
            preset = OverlayPreset.HIGH_CONTRAST
            auto_hide = False
            font_size = max(font_size, ACCESSIBLE_FONT_SIZE)
        self.settings.general.usage_intents = intents
        self.settings.general.accessibility_mode = accessible
        self.settings.overlay.preset = preset
        self.settings.overlay.auto_hide = auto_hide
        self.settings.overlay.font_size = font_size
        self.games_changed.emit(self.games_selected)


class LanguageStep(Step):
    title = "What will you be listening to?"

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self.add_body("Both of these can be changed at any time.")
        grid = QGridLayout()
        grid.setSpacing(SPACE["md"])
        grid.addWidget(QLabel("Language you'll hear"), 0, 0)
        self._source = LanguagePicker(_HEAR_ENTRIES)
        self._source.set_code(settings.captions.source_language)
        grid.addWidget(self._source, 0, 1)
        grid.addWidget(QLabel("Show captions in"), 1, 0)
        self._target = LanguagePicker(_READ_ENTRIES)
        self._target.set_code(settings.captions.target_language or default_target_language())
        grid.addWidget(self._target, 1, 1)
        grid.setColumnStretch(1, 1)
        self._root.addLayout(grid)

        self._same = wrap_label(QLabel("Captions will simply transcribe what you hear."))
        self._same.setObjectName("secondary")
        self._root.addWidget(self._same)
        self._show_original = QCheckBox("Also show the original language")
        self._show_original.setChecked(settings.captions.show_original)
        self._root.addWidget(self._show_original)
        self._preview = DualPreview()
        self._root.addWidget(self._preview)
        self._root.addStretch(1)

        self._source.code_changed.connect(self._changed)
        self._target.code_changed.connect(self._changed)
        self._show_original.toggled.connect(self._changed)
        self._refresh()

    def set_languages(self, source: str, target: str) -> None:
        self._source.set_code(source)
        self._target.set_code(target)
        self._changed()

    def commit(self) -> None:
        captions = self.settings.captions
        captions.source_language = self._source.current_code()
        captions.target_language = self._target.current_code()
        captions.show_original = self._show_original.isChecked()
        if not captions.show_original:
            self.settings.overlay.original_position = OriginalPosition.HIDDEN
        elif self.settings.overlay.original_position is OriginalPosition.HIDDEN:
            self.settings.overlay.original_position = OriginalPosition.ABOVE

    def on_enter(self) -> None:
        self._refresh()

    def _changed(self) -> None:
        self._refresh()
        self.commit()

    def _refresh(self) -> None:
        source, target = self._source.current_code(), self._target.current_code()
        same = bool(source) and source == target
        self._same.setVisible(same)
        self._show_original.setVisible(not same)
        self._preview.setVisible(not same)
        if same:
            return
        self._preview.set_preset(self.settings.overlay.preset)
        self._preview.set_lines(
            "The words as they were spoken", f"The same words in {language_name(target)}"
        )
        self._preview.set_show_original(self._show_original.isChecked())


class StyleStep(Step):
    title = "How should captions look?"

    preset_chosen = Signal(object)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self.add_body("Pick the one that reads best over what you watch.")
        grid = QGridLayout()
        grid.setSpacing(SPACE["md"])
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._cards: dict[OverlayPreset, StyleCard] = {}
        # Four of the seven: the widest spread of looks that still fits a 2x2
        # grid. Everything else is one click away in Settings.
        presets = (
            OverlayPreset.GLASS,
            OverlayPreset.CINEMA,
            OverlayPreset.SOLID,
            OverlayPreset.HIGH_CONTRAST,
        )
        for index, preset in enumerate(presets):
            card = StyleCard(preset, PRESET_LABELS[preset])
            card.clicked.connect(partial(self._choose, preset))
            self._group.addButton(card)
            grid.addWidget(card, index // 2, index % 2)
            self._cards[preset] = card
        self._root.addLayout(grid)
        note = self.add_body("Colours, size and position can all be fine-tuned later.")
        note.setObjectName("tertiary")
        self._root.addStretch(1)

    def on_enter(self) -> None:
        current = self.settings.overlay.preset
        for preset, card in self._cards.items():
            card.blockSignals(True)
            card.setChecked(preset is current)
            card.blockSignals(False)

    def select(self, preset: OverlayPreset) -> None:
        self._choose(preset)

    def _choose(self, preset: OverlayPreset, _checked: bool = False) -> None:
        self.settings.overlay.preset = preset
        self.on_enter()
        self.preset_chosen.emit(preset)


class ReadyStep(Step):
    title = "You're all set!"
    primary_label = "Start captions"

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self.add_body("Press Start, then play anything.")
        chips = QHBoxLayout()
        chips.setSpacing(SPACE["sm"])
        self._chips = [chip(""), chip(""), chip("")]
        for item in self._chips:
            chips.addWidget(item)
        chips.addStretch(1)
        self._root.addLayout(chips)

        shortcuts = settings.shortcuts.globals
        keys = QVBoxLayout()
        keys.setSpacing(SPACE["sm"])
        for action, description in (
            ("toggle_captions", "captions on/off"),
            ("toggle_overlay", "show/hide captions"),
            ("toggle_click_through", "click-through"),
        ):
            sequence = shortcuts.get(action, "")
            if sequence:
                keys.addWidget(KeyCapRow(sequence, description))
        self._root.addLayout(keys)

        self._games_tip = QLabel(
            "Fullscreen games can cover the captions. In the game's video settings, choose "
            '"Borderless" or "Windowed fullscreen" — it looks identical and lets captions show. '
            f"{APP_NAME} never touches the game itself."
        )
        self._games_tip.setObjectName("secondary")
        self._games_tip.setWordWrap(True)
        self._games_tip.hide()
        self._root.addWidget(self._games_tip)
        self._root.addStretch(1)

    def set_games_tip(self, visible: bool) -> None:
        self._games_tip.setVisible(visible)

    def on_enter(self) -> None:
        captions = self.settings.captions
        self._chips[0].setText(f"{TIER_LABELS[captions.quality]} quality")
        self._chips[1].setText(self._language_summary())
        self._chips[2].setText(f"{preset_label(self.settings.overlay.preset)} captions")

    def _language_summary(self) -> str:
        if not self.settings.translation_enabled:
            return "Transcribing what you hear"
        captions = self.settings.captions
        return (
            f"{language_name(captions.source_language, AUTO_DETECT_LABEL)} → "
            f"{language_name(captions.target_language)}"
        )
