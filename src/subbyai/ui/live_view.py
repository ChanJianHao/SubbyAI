"""The Live surface — the app's hero.

It answers four questions at a glance: is it running, can it hear anything,
what is it showing, and what are the two or three things I change mid-session.
Everything else belongs in Settings.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.events import CaptionSegment
from ..core.health import HealthReport, HealthState
from ..core.settings import Settings
from ..languages import language_name
from . import theme
from .tokens import SPACE
from .widgets import FlowLayout, LevelMeter, StatusBanner, preset_label

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

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._running = False
        self._segments: list[CaptionSegment] = []
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

        layout.addStretch(1)
        from .mascot import Mochi

        self.mascot = Mochi(card)
        layout.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignCenter)
        self._empty_title = QLabel()
        self._empty_title.setObjectName("heading")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_body = QLabel()
        self._empty_body.setObjectName("secondary")
        self._empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_body.setWordWrap(True)
        layout.addWidget(self._empty_title)
        layout.addWidget(self._empty_body)

        self._caption_area = QWidget()
        self._caption_layout = QVBoxLayout(self._caption_area)
        self._caption_layout.setContentsMargins(0, 0, 0, 0)
        self._caption_layout.setSpacing(SPACE["md"])
        self._caption_layout.addStretch(1)
        self._caption_area.hide()
        layout.addWidget(self._caption_area, stretch=1)
        layout.addStretch(1)

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

        self.progress = QProgressBar()
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
        self.level_meter.set_level(report.level, report.state is HealthState.LISTENING)
        self._device_label.setText(
            f"Listening to {report.device_name}" if report.device_name else ""
        )
        self._update_empty_visibility()

    def set_download_progress(self, fraction: float, message: str) -> None:
        self.progress.setVisible(fraction >= 0)
        self.cancel_download.setVisible(fraction >= 0)
        self.progress.setValue(int(fraction * 100))
        self._empty_title.setText("One-time download")
        self._empty_body.setText(message)
        self._update_empty_visibility()

    def hide_download_progress(self) -> None:
        self.progress.hide()
        self.cancel_download.hide()

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
        while self._caption_layout.count() > 1:
            item = self._caption_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for index, segment in enumerate(self._segments):
            newest = index == len(self._segments) - 1
            self._caption_layout.insertWidget(
                self._caption_layout.count() - 1, self._segment_widget(segment, newest)
            )
        self._update_empty_visibility()

    def _segment_widget(self, segment: CaptionSegment, newest: bool) -> QWidget:
        palette = theme.current()
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        translation = segment.display_translation
        original = QLabel(segment.text)
        original.setTextFormat(Qt.TextFormat.PlainText)
        original.setWordWrap(True)
        original.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size = 18 if newest else 15
        opacity = "" if newest else f"color: {palette.text_secondary};"
        italic = "font-style: italic;" if segment.is_uncertain else ""
        if translation:
            # Original is the quieter line once a translation exists.
            original.setStyleSheet(
                f"font-size: {int(size * 0.8)}px; color: {palette.text_secondary}; {italic}"
            )
            layout.addWidget(original)
            translated = QLabel(translation)
            translated.setTextFormat(Qt.TextFormat.PlainText)
            translated.setWordWrap(True)
            translated.setAlignment(Qt.AlignmentFlag.AlignCenter)
            translated.setStyleSheet(
                f"font-size: {size}px; font-weight: 600; color: {palette.accent};"
            )
            layout.addWidget(translated)
        else:
            original.setStyleSheet(f"font-size: {size}px; font-weight: 500; {opacity} {italic}")
            layout.addWidget(original)
        return block

    def _update_empty_visibility(self) -> None:
        has_captions = bool(self._segments)
        self._caption_area.setVisible(has_captions)
        self._empty_title.setVisible(not has_captions)
        self._empty_body.setVisible(not has_captions)
        self.mascot.setVisible(not has_captions)

    # ---------- slots ----------

    def _on_start_clicked(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()

    def _on_translate_toggled(self, checked: bool) -> None:
        self.translation_toggled.emit(checked)
