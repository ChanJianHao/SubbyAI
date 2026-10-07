"""The Live surface — the app's hero.

It answers four questions at a glance: is it running, can it hear anything,
what is it showing, and what are the two or three things I change mid-session.
Everything else belongs in Settings.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.events import CaptionSegment
from ..core.health import HealthReport, HealthState
from ..core.settings import Settings
from ..languages import language_name
from . import theme
from .motion import MotionStack, reveal
from .motion_widgets import ActivityIndicator, SmoothProgressBar
from .tokens import SPACE
from .widgets import FlowLayout, LevelMeter, StatusBanner, compact_combo, preset_label

#: What the user sees for each health state when no captions are on screen.
_EMPTY_STATES = {
    HealthState.OFF: (
        "Ready when you are",
        "Press Start, then play anything — a video, a call, a game.",
    ),
    HealthState.STARTING: (
        "Warming up…",
        "This takes a moment the first time after a quality change.",
    ),
    HealthState.DOWNLOADING: (
        "Getting things ready…",
        "Your subtitles will be ready as soon as the download finishes.",
    ),
    HealthState.LISTENING: (
        "Listening…",
        "Captions appear here and in the floating window.",
    ),
    HealthState.SILENT: (
        "No sound yet",
        "Check that something is playing and the volume isn't muted.",
    ),
    HealthState.SOUND_NO_SPEECH: (
        "Hearing sound",
        "No speech yet — music and effects are skipped.",
    ),
    HealthState.DELAYED: (
        "Catching up",
        "Captions are running behind. A faster quality setting would help.",
    ),
}


class LiveView(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    style_requested = Signal()
    translation_toggled = Signal(bool)
    show_original_toggled = Signal(bool)
    overlay_toggled = Signal(bool)
    click_through_toggled = Signal(bool)
    banner_action = Signal(str)
    cancel_download_requested = Signal()
    source_changed = Signal(str)

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._running = False
        self._segments: list[CaptionSegment] = []
        self._segment_blocks: dict[int, QWidget] = {}
        self._last_health = None
        self._build()
        self.set_health(HealthReport(HealthState.OFF))
        theme.subscribe(self._theme_changed)

    def _theme_changed(self, palette) -> None:
        self._render_segments()
        self.mascot.update()

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        root.setSpacing(SPACE["md"])

        self.banner = StatusBanner()
        self.banner.action_clicked.connect(self.banner_action)
        root.addWidget(self.banner)
        self.history_warning = StatusBanner()
        self.history_warning.action_clicked.connect(self.banner_action)
        root.addWidget(self.history_warning)
        greeting = QHBoxLayout()
        title = QLabel("Your world, subtitled.")
        title.setObjectName("title")
        title.setWordWrap(True)
        greeting.addWidget(title, 1)
        self.setup_button = QPushButton("Set up my subtitles")
        self.setup_button.setObjectName("chip")
        self.setup_button.clicked.connect(lambda: self.banner_action.emit("setup"))
        greeting.addWidget(self.setup_button)
        root.addLayout(greeting)
        self.privacy_indicator = QLabel()
        self.privacy_indicator.setObjectName("secondary")
        self.privacy_indicator.setWordWrap(True)
        self.privacy_indicator.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.privacy_indicator)
        self.audio_source = compact_combo(QComboBox())
        self.audio_source.setAccessibleName("Audio source")
        self.audio_source.addItem("System audio · videos, games and calls", "system")
        self.audio_source.addItem("Microphone · conversations and lectures", "microphone")
        self.audio_source.setCurrentIndex(
            max(0, self.audio_source.findData(self._settings.audio.source))
        )
        self.audio_source.activated.connect(
            lambda index: self.source_changed.emit(self.audio_source.itemData(index))
        )
        root.addWidget(self.audio_source)

        root.addWidget(self._build_preview(), stretch=1)
        root.addWidget(self._build_controls())
        root.addWidget(self._build_toggles())

    def _build_preview(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["lg"])
        layout.setSpacing(SPACE["sm"])

        top = QHBoxLayout()
        top.addStretch(1)
        self._style_chip = QPushButton(preset_label(self._settings.overlay.preset))
        self._style_chip.setObjectName("chip")
        self._style_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_chip.clicked.connect(self.style_requested)
        top.addWidget(self._style_chip)
        layout.addLayout(top)

        self._preview_stack = MotionStack()
        self._empty_area = QWidget()
        empty_layout = QVBoxLayout(self._empty_area)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.addStretch(1)
        from .mascot import Mochi

        self.mascot = Mochi(card)
        empty_layout.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignCenter)
        self._empty_title = QLabel()
        self._empty_title.setObjectName("heading")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_body = QLabel()
        self._empty_body.setObjectName("secondary")
        self._empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_body.setWordWrap(True)
        empty_layout.addWidget(self._empty_title)
        empty_layout.addWidget(self._empty_body)
        empty_layout.addStretch(1)

        self._caption_area = QWidget()
        self._caption_layout = QVBoxLayout(self._caption_area)
        self._caption_layout.setContentsMargins(0, 0, 0, 0)
        self._caption_layout.setSpacing(SPACE["md"])
        self._caption_layout.addStretch(1)
        self._caption_layout.addStretch(1)
        self._preview_stack.addWidget(self._empty_area)
        self._preview_stack.addWidget(self._caption_area)
        layout.addWidget(self._preview_stack, stretch=1)

        bottom = QHBoxLayout()
        self.level_meter = LevelMeter()
        bottom.addWidget(self.level_meter)
        self._device_label = QLabel()
        self._device_label.setObjectName("tertiary")
        bottom.addWidget(self._device_label)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return card

    def _build_controls(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["lg"])

        self.start_button = QPushButton("Start captions")
        self.start_button.setObjectName("primary")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setMinimumWidth(200)
        self.start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.start_button)

        self.activity = ActivityIndicator()
        layout.addWidget(self.activity)
        self.progress = SmoothProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.cancel_download = QPushButton("Cancel download")
        self.cancel_download.setObjectName("quiet")
        self.cancel_download.clicked.connect(self.cancel_download_requested)
        self.cancel_download.hide()
        layout.addWidget(self.cancel_download)

        self.ticker = QLabel()
        self.ticker.setObjectName("secondary")
        layout.addWidget(self.ticker)
        layout.addStretch(1)
        return row

    def _build_toggles(self) -> QWidget:
        row = QWidget()
        # Wraps onto a second line in a narrow window. In a fixed row these four
        # chips demanded 784px, which is what forced the whole window wide and
        # left the controls compressed until the user resized it.
        layout = FlowLayout(row)

        self.translate_chip = self._chip("", self._on_translate_toggled)
        self.original_chip = self._chip("Show original", self.show_original_toggled)
        self.overlay_chip = self._chip("Overlay", self.overlay_toggled)
        self.click_chip = self._chip("Click-through", self.click_through_toggled)
        self.click_chip.setToolTip("Let the mouse pass through the captions")

        for chip in (self.translate_chip, self.original_chip, self.overlay_chip, self.click_chip):
            layout.addWidget(chip)
        self.sync_from_settings()
        return row

    def _chip(self, text: str, slot) -> QPushButton:
        chip = QPushButton(text)
        chip.setObjectName("chip")
        chip.setCheckable(True)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.toggled.connect(slot)
        return chip

    # ---------- state ----------

    def sync_from_settings(self) -> None:
        self.audio_source.setCurrentIndex(
            max(0, self.audio_source.findData(self._settings.audio.source))
        )
        settings = self._settings
        from ..translation.base import tier_for_url
        from .settings_intelligence import provider_tier

        if settings.processing.asr_backend == "remote":
            place = tier_for_url(settings.processing.base_url).label.lower()
            audio = f"Audio → speech server ({place})"
        else:
            audio = "Audio stays here"
        external = any(
            provider.enabled
            and provider.id in settings.intelligence.translation_order
            and provider_tier(provider).value != "on_device"
            for provider in settings.intelligence.providers
        )
        text = (
            " · Translation may send text to a server"
            if external and settings.translation_enabled
            else ""
        )
        self.privacy_indicator.setText(audio + text)
        translating = bool(settings.captions.target_language)
        target = language_name(settings.captions.target_language)
        source = language_name(settings.captions.source_language) or "Detected"
        self.translate_chip.blockSignals(True)
        self.translate_chip.setChecked(translating)
        self.translate_chip.setText(f"{source} → {target}" if translating else "Translation off")
        self.translate_chip.blockSignals(False)

        for chip, checked in (
            (self.original_chip, settings.captions.show_original),
            (self.overlay_chip, settings.overlay.visible),
            (self.click_chip, settings.overlay.click_through),
        ):
            chip.blockSignals(True)
            chip.setChecked(checked)
            chip.blockSignals(False)
        self.original_chip.setEnabled(translating)
        self._style_chip.setText(preset_label(self._settings.overlay.preset))

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setText("Stop captions" if running else "Start captions")
        if not running:
            self.activity.set_busy(False)
            self.ticker.clear()
            self.level_meter.set_level(0.0)

    def set_health(self, report: HealthReport) -> None:
        title, body = _EMPTY_STATES.get(report.state, ("Something went wrong", report.detail or ""))
        if report.state is HealthState.ERROR:
            title, body = "Captions stopped", report.detail
        elif report.detail and report.state in (
            HealthState.SILENT,
            HealthState.SOUND_NO_SPEECH,
            HealthState.DELAYED,
        ):
            body = report.detail
        self._empty_title.setText(title)
        self._empty_body.setText(body)
        if report.state != self._last_health:
            self._last_health = report.state
            self.mascot.set_mood({
                HealthState.STARTING: "busy", HealthState.DOWNLOADING: "busy",
                HealthState.LISTENING: "listening", HealthState.ERROR: "error",
            }.get(report.state, "ready"))
            self.activity.set_busy(report.state in (HealthState.STARTING, HealthState.DOWNLOADING))
        self.level_meter.set_level(report.level, report.state is HealthState.LISTENING)
        self._device_label.setText(
            f"Listening to {report.device_name}" if report.device_name else ""
        )
        self._update_empty_visibility()

    def set_download_progress(self, fraction: float, message: str) -> None:
        self.progress.setRange(0, 100 if fraction >= 0 else 0)
        reveal(self.progress, True)
        reveal(self.cancel_download, True)
        self.progress.setValue(int(max(0, fraction) * 100))
        self.activity.set_busy(True)
        self.mascot.set_mood("busy")
        self._empty_title.setText("One-time download")
        self._empty_body.setText(message)
        self._update_empty_visibility()

    def hide_download_progress(self) -> None:
        reveal(self.progress, False)
        reveal(self.cancel_download, False)
        self.activity.set_busy(False)

    def add_segment(self, segment: CaptionSegment) -> None:
        self._segments.append(segment)
        self._segments = self._segments[-3:]
        self._render_segments()

    def update_segment(self, segment: CaptionSegment) -> None:
        for index, existing in enumerate(self._segments):
            if existing.id == segment.id:
                self._segments[index] = segment
                self._render_segments()
                return

    def clear_segments(self) -> None:
        self._segments = []
        self._render_segments()

    def set_ticker(self, text: str) -> None:
        self.ticker.setText(text)

    # ---------- rendering ----------

    def _render_segments(self) -> None:
        wanted = {segment.id for segment in self._segments}
        for segment_id in self._segment_blocks.keys() - wanted:
            block = self._segment_blocks.pop(segment_id)
            self._caption_layout.removeWidget(block)
            block.hide()
            block.deleteLater()
        for index, segment in enumerate(self._segments):
            newest = index == len(self._segments) - 1
            block = self._segment_blocks.get(segment.id)
            if block is None:
                block = self._segment_widget(segment, newest)
                self._segment_blocks[segment.id] = block
                self._caption_layout.insertWidget(self._caption_layout.count() - 1, block)
                reveal(block, True)
            else:
                self._update_segment_widget(block, segment, newest)
        self._update_empty_visibility()

    def _segment_widget(self, segment: CaptionSegment, newest: bool) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        original = QLabel()
        original.setTextFormat(Qt.TextFormat.PlainText)
        original.setWordWrap(True)
        original.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(original)
        translated = QLabel()
        translated.setTextFormat(Qt.TextFormat.PlainText)
        translated.setWordWrap(True)
        translated.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(translated)
        block._original = original
        block._translated = translated
        self._update_segment_widget(block, segment, newest)
        return block

    def _update_segment_widget(self, block: QWidget, segment: CaptionSegment, newest: bool) -> None:
        palette = theme.current()
        translation = segment.display_translation
        original, translated = block._original, block._translated
        original.setText(segment.text)
        translated.setText(translation or "")
        translated.setVisible(bool(translation))
        size = 18 if newest else 15
        opacity = "" if newest else f"color: {palette.text_secondary};"
        italic = "font-style: italic;" if segment.is_uncertain else ""
        if translation:
            # Original is the quieter line once a translation exists.
            original.setStyleSheet(
                f"font-size: {int(size * 0.8)}px; color: {palette.text_secondary}; {italic}"
            )
            translated.setStyleSheet(
                f"font-size: {size}px; font-weight: 600; color: {palette.accent};"
            )
        else:
            original.setStyleSheet(f"font-size: {size}px; font-weight: 500; {opacity} {italic}")

    def _update_empty_visibility(self) -> None:
        has_captions = bool(self._segments)
        self._preview_stack.setCurrentIndex(1 if has_captions else 0)

    # ---------- slots ----------

    def _on_start_clicked(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()

    def _on_translate_toggled(self, checked: bool) -> None:
        self.translation_toggled.emit(checked)
