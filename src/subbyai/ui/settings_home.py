"""Everyday choices over the shared configuration; no technical prerequisites."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QVBoxLayout

from ..core.profiles import PROFILES, apply_profile
from ..core.settings import OverlayPreset
from ..languages import sorted_languages
from .settings_widgets import Group, LanguagePicker, SettingsSection, hint_label
from .widgets import compact_combo, preset_label


class EverydaySection(SettingsSection):
    restart_capture_requested = Signal()
    style_changed = Signal()
    preview_requested = Signal()

    def __init__(self, store, deps=None, parent=None):
        super().__init__(store, parent)
        self._deps = deps
        self.capability = getattr(deps, "capability", None)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Make yourself at home.")
        title.setObjectName("title")
        title.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(
            hint_label(
                "Pick your sound, your language, and a look you love. Then press Start on Live."
            )
        )
        listening = Group("What are we watching?")
        self.device = compact_combo(QComboBox())
        self.device.addItem("Follow my default output", None)
        lister = getattr(deps, "audio_devices", None)
        self.devices = list(lister() if lister else [])
        for device in self.devices:
            self.device.addItem(device.name, device.id)
        listening.add_row("Listen to", self.device)
        self.source = LanguagePicker((("", "Auto detect"), *sorted_languages()))
        self.target = LanguagePicker((("", "Off · original words"), *sorted_languages()))
        listening.add_row(
            "Spoken language", self.source, "Auto detect works best after a few clear words."
        )
        listening.add_row(
            "Subtitle language", self.target, "Choose Off to keep only the original words."
        )
        root.addWidget(listening)
        performance = Group("Find your rhythm")
        self.profile = compact_combo(QComboBox())
        for value, (label, *_rest) in PROFILES.items():
            self.profile.addItem(label + (" · Recommended" if value == "balanced" else ""), value)
        self.profile.addItem("My custom settings", "custom")
        performance.add_row("Performance", self.profile)
        self.recommendation = hint_label(
            "Balanced adapts to your computer. You can change it at any time."
        )
        performance.add(self.recommendation)
        root.addWidget(performance)
        look = Group("A little movie magic")
        self.preset = compact_combo(QComboBox())
        for preset in OverlayPreset:
            self.preset.addItem(preset_label(preset), preset.value)
        look.add_row("Subtitle style", self.preset)
        self.preview = QPushButton("Try the floating subtitles")
        self.preview.clicked.connect(self.preview_requested)
        look.add(self.preview)
        root.addWidget(look)
        privacy = Group("Your words are yours")
        self.history = QCheckBox("Save my transcripts")
        self.history.setToolTip("Keep searchable transcripts on this computer. Off by default.")
        privacy.add(self.history)
        self.privacy_note = hint_label("")
        privacy.add(self.privacy_note)
        root.addWidget(privacy)
        self.device.currentIndexChanged.connect(self._device_changed)
        self.source.code_changed.connect(lambda code: self._language("source_language", code))
        self.target.code_changed.connect(lambda code: self._language("target_language", code))
        self.profile.activated.connect(self._profile_changed)
        self.preset.currentIndexChanged.connect(self._style_changed)
        self.history.toggled.connect(self._history_changed)
        self.refresh()

    def refresh(self):
        for control in (
            self.device,
            self.source,
            self.target,
            self.profile,
            self.preset,
            self.history,
        ):
            control.blockSignals(True)
        self.device.setCurrentIndex(max(0, self.device.findData(self.settings.audio.device_id)))
        self.source.set_code(self.settings.captions.source_language)
        self.target.set_code(self.settings.captions.target_language)
        self.profile.setCurrentIndex(
            max(0, self.profile.findData(self.settings.captions.performance_profile))
        )
        self.preset.setCurrentIndex(
            max(0, self.preset.findData(self.settings.overlay.preset.value))
        )
        self.history.setChecked(self.settings.history.enabled)
        remote = self.settings.processing.asr_backend == "remote"
        self.privacy_note.setText(
            "Audio goes to your selected speech server. "
            "Open Advanced Mode → Remote Processing to review it."
            if remote
            else "Speech recognition runs here. Audio stays in memory and is never saved. "
            "Remote translation is optional in Advanced Mode."
        )
        for control in (
            self.device,
            self.source,
            self.target,
            self.profile,
            self.preset,
            self.history,
        ):
            control.blockSignals(False)

    def refresh_devices(self, devices):
        self.devices = list(devices)
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Follow my default output", None)
        for device in self.devices:
            self.device.addItem(device.name, device.id)
        self.device.setCurrentIndex(max(0, self.device.findData(self.settings.audio.device_id)))
        self.device.blockSignals(False)

    def _device_changed(self, index):
        self.settings.audio.device_id = self.device.itemData(index)
        self.settings.audio.device_name = "" if index == 0 else self.device.itemText(index)
        self.apply("audio")
        self.restart_capture_requested.emit()

    def _language(self, name, code):
        setattr(self.settings.captions, name, code)
        self.apply("captions")
        self.restart_capture_requested.emit()

    def _profile_changed(self, index):
        profile = self.profile.itemData(index)
        if profile != "custom":
            apply_profile(self.settings, profile, self.capability)
            self.apply("captions")
            self.restart_capture_requested.emit()
            self.refresh()

    def _style_changed(self, index):
        self.settings.overlay.preset = OverlayPreset(self.preset.itemData(index))
        self.apply("overlay")
        self.style_changed.emit()

    def _history_changed(self, checked):
        self.settings.history.enabled = checked
        self.apply("history")
