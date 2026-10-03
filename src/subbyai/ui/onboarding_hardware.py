"""The two wizard pages that have to ask this computer about itself.

Quality needs to know what the machine can run before it can offer anything, and
the audio check needs to know whether sound is actually arriving. Both therefore
have a waiting state and a failure state that the preference pages never do, and
both must say what they found in words the reader can act on — a disabled option
with no reason reads as a missing feature rather than a limit of the hardware.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..asr.capability import MachineCapability, recommended_tier, tier_available, tier_download_mb
from ..branding import APP_NAME
from ..core.settings import TIER_MODELS, QualityTier, Settings
from .onboarding_steps import Step
from .onboarding_widgets import SOUND_LEVEL, LevelMeter, SelectCard
from .tokens import SPACE
from .widgets import TIER_LABELS, compact_combo, size_text

log = logging.getLogger(__name__)

#: How long the audio check waits before admitting it has heard nothing.
SILENCE_TIMEOUT_MS = 10_000
LEVEL_POLL_MS = 33

# Engines reachable only through the advanced sheet. Shown verbatim on purpose:
# this is the one place in the app where the real name is the useful name.
ADVANCED_MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "large-v3-turbo",
    "distil-small.en",
    "distil-medium.en",
    "distil-large-v3",
)
COMPUTE_CHOICES = (
    ("auto", "Choose automatically"),
    ("gpu", "Graphics card"),
    ("cpu", "Processor"),
)


class QualityStep(Step):
    title = "Checking what this computer can do…"

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._body = self.add_body("This takes a moment, and nothing leaves your computer.")
        self._capability: MachineCapability | None = None

        self._profile = compact_combo(QComboBox())
        from ..core.profiles import PROFILES

        for key, (label, *_rest) in PROFILES.items():
            self._profile.addItem(label + (" · Recommended" if key == "balanced" else ""), key)
        self._profile.addItem("My custom settings", "custom")
        self._profile.setCurrentIndex(
            max(0, self._profile.findData(settings.captions.performance_profile))
        )
        self._profile.activated.connect(self._choose_profile)
        self._root.addWidget(self._profile)
        self.add_body("Fast feels snappy. Balanced suits most media. Accurate takes more time.")

        self._details = QPushButton("Show model choices")
        self._details.setCheckable(True)
        self._details.setObjectName("quiet")
        self._details.toggled.connect(self._show_details)
        self._root.addWidget(self._details, 0, Qt.AlignmentFlag.AlignLeft)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._cards: dict[QualityTier, SelectCard] = {}
        for tier in QualityTier:
            blurb = TIER_MODELS[tier][2]
            card = SelectCard(TIER_LABELS[tier], blurb, meta=size_text(tier_download_mb(tier)))
            card.setEnabled(False)
            card.clicked.connect(partial(self._choose, tier))
            self._group.addButton(card)
            self._cards[tier] = card
            self._root.addWidget(card)
            card.setVisible(settings.general.advanced_mode)

        note = self.add_body(
            "Nothing downloads now — the caption engine arrives the first time you press Start."
        )
        note.setObjectName("tertiary")
        self._advanced = QPushButton("Advanced: choose the exact engine")
        self._advanced.setObjectName("quiet")
        self._advanced.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced.clicked.connect(self._open_advanced)
        self._root.addWidget(self._advanced, 0, Qt.AlignmentFlag.AlignLeft)

    def _show_details(self, shown: bool) -> None:
        for card in self._cards.values():
            card.setVisible(shown)

    def _choose_profile(self, index: int) -> None:
        from ..core.profiles import apply_profile

        profile = self._profile.itemData(index)
        if profile != "custom":
            apply_profile(self.settings, profile, self._capability)
            self._select(self.settings.captions.quality)

    def set_capability(self, capability: MachineCapability | None) -> None:
        """Fill the page in once detection lands; safe to call more than once."""
        self._capability = capability or MachineCapability()
        recommended = recommended_tier(self._capability)
        self._title.setText("Let's find your rhythm")
        self._body.setText(
            "We found a graphics card for faster local subtitles."
            if self._capability.has_cuda
            else "Local subtitles are available. A lighter model suits smaller computers."
        )
        for tier, card in self._cards.items():
            usable, reason = tier_available(tier, self._capability)
            card.set_unavailable("" if usable else reason)
            card.set_badge("Recommended" if tier is recommended else "")

        stored = self.settings.captions.quality
        keep, _ = tier_available(stored, self._capability)
        # A first run has nothing worth preserving, so take the recommendation.
        # A re-run keeps what the user already settled on, as long as it still fits.
        if not self.settings.general.onboarding_complete:
            from ..core.profiles import PROFILES, apply_profile

            profile = self.settings.captions.performance_profile
            if profile in PROFILES and not self.settings.captions.model_override:
                apply_profile(self.settings, profile, self._capability)
                self._select(self.settings.captions.quality)
                return
        self._select(stored if keep else recommended)

    def apply_defaults(self) -> None:
        if self._capability is None:
            return
        usable, _ = tier_available(self.settings.captions.quality, self._capability)
        if not usable:
            self._select(recommended_tier(self._capability))

    def _select(self, tier: QualityTier) -> None:
        self.settings.captions.quality = tier
        for candidate, card in self._cards.items():
            card.blockSignals(True)
            card.setChecked(candidate is tier)
            card.blockSignals(False)

    def _choose(self, tier: QualityTier, _checked: bool = False) -> None:
        self._select(tier)
        self.settings.captions.performance_profile = "custom"
        self._profile.setCurrentIndex(self._profile.findData("custom"))

    def _open_advanced(self) -> None:
        AdvancedEngineSheet(self.settings, self).exec()


class AdvancedEngineSheet(QDialog):
    """Optional exact engine choices during first-run setup."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Choose the exact engine")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        root.setSpacing(SPACE["md"])
        intro = QLabel(
            "Choose an exact engine if you already have a preference. "
            "More controls are available in Settings → Advanced Mode."
        )
        intro.setObjectName("secondary")
        intro.setWordWrap(True)
        root.addWidget(intro)

        root.addWidget(QLabel("Engine"))
        self._model = compact_combo(QComboBox())
        self._model.setEditable(True)
        self._model.addItem("Use the quality setting", "")
        for name in ADVANCED_MODELS:
            self._model.addItem(name, name)
        override = settings.captions.model_override
        index = self._model.findData(override)
        if index < 0 and override:
            self._model.addItem(override, override)
            index = self._model.count() - 1
        self._model.setCurrentIndex(max(0, index))
        root.addWidget(self._model)

        root.addWidget(QLabel("Run on"))
        self._device = compact_combo(QComboBox())
        for value, label in COMPUTE_CHOICES:
            self._device.addItem(label, value)
        device_index = self._device.findData(settings.captions.compute_device)
        self._device.setCurrentIndex(max(0, device_index))
        root.addWidget(self._device)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _save(self) -> None:
        data = self._model.currentData()
        typed = self._model.currentText().strip()
        self.settings.captions.model_override = (
            str(data) if data else ("" if typed == self._model.itemText(0) else typed)
        )
        self.settings.captions.compute_device = str(self._device.currentData() or "auto")
        self.accept()


class AudioCheckStep(Step):
    title = "Let's make sure we can hear something."
    device_chosen = Signal()

    def __init__(
        self, settings: Settings, devices: list | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(settings, parent)
        self.add_body("Play anything — a video, some music — and watch the meter.")
        self._devices = list(devices or [])
        self._heard = False
        self._source: Callable[[], float] | None = None

        meter_row = QHBoxLayout()
        meter_row.setSpacing(SPACE["lg"])
        self._meter = LevelMeter(height=34)
        meter_row.addWidget(self._meter)
        self._status = QLabel("Listening for sound…")
        self._status.setObjectName("secondary")
        self._status.setWordWrap(True)
        meter_row.addWidget(self._status, 1)
        self._root.addLayout(meter_row)

        self._picker_label = QLabel("Listen to")
        self._root.addWidget(self._picker_label)
        self._picker = compact_combo(QComboBox())
        for device in self._devices:
            suffix = " — current" if getattr(device, "is_default", False) else ""
            self._picker.addItem(f"{device.name}{suffix}", device.id)
        self._picker.activated.connect(self._pick_device)
        self._root.addWidget(self._picker)
        self._set_picker_visible(True)
        self._root.addStretch(1)

        self._silence = QTimer(self)
        self._silence.setSingleShot(True)
        self._silence.setInterval(SILENCE_TIMEOUT_MS)
        self._silence.timeout.connect(self._report_silence)
        self._poll = QTimer(self)
        self._poll.setInterval(LEVEL_POLL_MS)
        self._poll.timeout.connect(self._pull_level)

    def set_level_source(self, source: Callable[[], float] | None) -> None:
        """Poll ``source()`` for a 0..1 loudness while this page is visible."""
        self._source = source
        if source is None:
            self._poll.stop()
        elif self.isVisible():
            self._poll.start()

    def set_level(self, level: float) -> None:
        """Slot for a shell that would rather push levels than be polled."""
        self._meter.set_level(level, speech=level >= SOUND_LEVEL)
        if level >= SOUND_LEVEL and not self._heard:
            self._heard = True
            self._silence.stop()
            self._status.setText("We can hear it. You're set.")
            self._set_picker_visible(False)

    def on_enter(self) -> None:
        if not self._heard:
            self._silence.start()
        if self._source is not None:
            self._poll.start()

    def stop(self) -> None:
        """Release the timers; the wizard calls this on the way out."""
        self._silence.stop()
        self._poll.stop()

    def _pull_level(self) -> None:
        if self._source is None:
            return
        try:
            self.set_level(float(self._source()))
        except Exception:
            log.exception("Level source failed; the audio check will stay quiet")
            self.set_level_source(None)

    def _report_silence(self) -> None:
        """Name what is wrong and offer the one control that can fix it."""
        if self._heard:
            return
        if sys.platform == "darwin":
            self._status.setText(
                "macOS asks for permission before any app can hear system audio. "
                "Nothing is recorded or uploaded."
            )
            return
        if not self._devices:
            self._status.setText(
                f"Nothing yet, and we couldn't list your audio devices. Restarting {APP_NAME} "
                "usually clears this."
            )
            return
        self._status.setText(
            "Nothing yet. Is something playing? You can also pick a different output below."
        )
        self._set_picker_visible(True)

    def _set_picker_visible(self, visible: bool) -> None:
        show = visible and bool(self._devices)
        self._picker_label.setVisible(show)
        self._picker.setVisible(show)

    def _pick_device(self, index: int) -> None:
        if not 0 <= index < len(self._devices):
            return
        device = self._devices[index]
        self.settings.audio.device_id = device.id
        self.settings.audio.device_name = device.name
        self._heard = False
        self._status.setText("Listening to this output…")
        self.device_chosen.emit()
