"""Everyday choices over the shared configuration; no technical prerequisites."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QVBoxLayout

from ..core.profiles import PROFILES, apply_profile
from ..core.settings import OverlayPreset
from ..languages import sorted_languages
from .motion_widgets import MotionToggle as QCheckBox
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
        listening = Group("What are we listening to?")
        self.audio_source = compact_combo(QComboBox())
        self.audio_source.addItem("System audio", "system")
        self.audio_source.addItem("Microphone", "microphone")
        listening.add_row(
            "Audio source", self.audio_source,
            "System audio for videos and calls; microphone for conversations and lectures.",
        )
        self.device = compact_combo(QComboBox())
        lister = getattr(deps, "audio_devices", None)
        try:
            self.devices = list(lister() if lister else [])
        except Exception:
            self.devices = []
        self.refresh_devices(self.devices)
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
        self.audio_source.activated.connect(self._source_changed)
        self.source.code_changed.connect(lambda code: self._language("source_language", code))
        self.target.code_changed.connect(lambda code: self._language("target_language", code))
        self.profile.activated.connect(self._profile_changed)
        self.preset.currentIndexChanged.connect(self._style_changed)
        self.history.toggled.connect(self._history_changed)
        self.refresh()

    def refresh(self):
        for control in (
            self.device,
            self.audio_source,
            self.source,
            self.target,
            self.profile,
            self.preset,
            self.history,
        ):
            control.blockSignals(True)
        self.device.setCurrentIndex(max(0, self.device.findData(self.settings.audio.device_id)))
        self.audio_source.setCurrentIndex(
            max(0, self.audio_source.findData(self.settings.audio.source))
        )
        self.source.set_code(self.settings.captions.source_language)
        self.target.set_code(self.settings.captions.target_language)
        self.profile.setCurrentIndex(
            max(0, self.profile.findData(self.settings.captions.performance_profile))
        )
        profile = self.settings.captions.performance_profile
        from ..asr.capability import recommended_tier
        from ..core.settings import QualityTier

        description = (
            PROFILES[profile][1] if profile in PROFILES else "Your advanced choices are active."
        )
        if self.capability is not None:
            caps = self.capability
            hardware = (
                f"{caps.gpu_name or 'NVIDIA GPU'} · {caps.vram_gb:.0f} GB graphics memory"
                if caps.has_cuda and caps.vram_gb
                else f"Processor · {caps.cpu_cores} threads · {caps.ram_gb:.0f} GB memory"
            )
            suggested = "Fast" if recommended_tier(caps) is QualityTier.QUICK else "Balanced"
            description += f"\n{hardware}. {suggested} is a good starting point."
        self.recommendation.setText(description)
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
            self.audio_source,
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
        microphone = self.settings.audio.source == "microphone"
        self.device.addItem(
            "Default microphone" if microphone else "Default playback device", None
        )
        for device in self.devices:
            if device.source == self.settings.audio.source:
                self.device.addItem(device.name, device.id)
        if (
            self.settings.audio.device_id
            and self.device.findData(self.settings.audio.device_id) < 0
        ):
            self.device.addItem(
                "Disconnected · " + (self.settings.audio.device_name or "selected device"),
                self.settings.audio.device_id,
            )
        self.device.setCurrentIndex(max(0, self.device.findData(self.settings.audio.device_id)))
        self.device.blockSignals(False)

    def _source_changed(self, index):
        self.settings.audio.source = self.audio_source.itemData(index)
        self.settings.audio.device_id = None
        self.settings.audio.device_name = ""
        self.refresh_devices(self.devices)
        self.apply("audio")
        self.restart_capture_requested.emit()

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
        from .settings_captions import seed_from_preset

        self.settings.overlay.preset = OverlayPreset(self.preset.itemData(index))
        seed_from_preset(self.settings.overlay, self.settings.overlay.preset)
        self.apply("overlay")
        self.style_changed.emit()

    def _history_changed(self, checked):
        self.settings.history.enabled = checked
        self.apply("history")
