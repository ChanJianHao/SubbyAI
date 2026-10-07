"""Appearance, startup, transcripts, and the About page.

Deleting every transcript is the one irreversible thing this screen can do, so
it asks first — inline, on the same row, with the danger word on the button that
actually does it. Nothing is handed to the store until that second press.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..branding import (
    APP_FULL_NAME,
    APP_NAME,
    APP_TAGLINE,
    HOMEPAGE_URL,
    SUPPORT_URL,
    VERSION,
)
from ..core.settings import SettingsStore
from .motion import Expandable, policy, reveal
from .motion_widgets import MotionToggle as QCheckBox
from .settings_widgets import Group, SettingsSection, hint_label
from .tokens import SPACE
from .widgets import compact_combo

__all__ = ["AboutSection", "GeneralSection", "human_size"]

THEMES: tuple[tuple[str, str], ...] = (
    ("Match my system", "system"),
    ("Light", "light"),
    ("Dark", "dark"),
)

RETENTIONS: tuple[tuple[str, int], ...] = (
    ("90 days", 90),
    ("30 days", 30),
    ("For as long as I keep them", 0),
    ("Until I stop captioning", -1),
)

CREDITS = (
    "Speech recognition uses OpenAI Whisper models. On-device translation uses "
    "Argos Translate language packs, which include OPUS-MT models — both under "
    "open licences requiring attribution."
)

PRIVACY_STATEMENT = (
    "Local processing keeps audio on this computer, in memory. Remote speech processing "
    "uploads audio only after you allow the selected server. Optional translation and "
    "assistant providers receive text. Saving transcripts is your choice; saved text "
    "stays on this computer until you delete it or its retention period ends."
)

LICENCES = (
    "SubbyAI is open source. It is built on PySide6 (LGPL v3), faster-whisper and "
    "CTranslate2 (MIT), ONNX Runtime and onnx-asr (MIT), and SentencePiece (Apache-2.0). "
    "Downloaded language packs have their own licences; many include OPUS translation "
    "models (CC BY 4.0). Mochi and the icons are original SubbyAI artwork. "
    "This build uses your installed fonts. Full notices accompany the application."
)


def human_size(byte_count: int) -> str:
    if byte_count < 1024 * 1024:
        return f"{max(byte_count, 0) / 1024:.0f} KB"
    if byte_count < 1024**3:
        return f"{byte_count / 1024 / 1024:.0f} MB"
    return f"{byte_count / 1024**3:.1f} GB"


class GeneralSection(SettingsSection):
    """Appearance, startup behaviour, transcript storage, and setup."""

    rerun_onboarding_requested = Signal()

    def __init__(self, store: SettingsStore, deps: Any = None, parent: QWidget | None = None):
        super().__init__(store, parent)
        self._deps = deps
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_appearance())
        column.addWidget(self._build_running())
        column.addWidget(self._build_history())
        column.addWidget(self._build_setup())
        self.refresh()

    # ---------- building ----------

    def _build_appearance(self) -> Group:
        group = Group("Appearance")
        self.text_scale = compact_combo(QComboBox(self))
        for label, scale in (("Standard", 1.0), ("Larger · 125%", 1.25), ("Largest · 150%", 1.5)):
            self.text_scale.addItem(label, scale)
        self.text_scale.activated.connect(self._on_text_scale)
        group.add_row(
            "App text size",
            self.text_scale,
            "Scales the app's controls in addition to your display scaling. "
            "Subtitle size is set separately in Captions.",
        )
        self.theme_choice = compact_combo(QComboBox(self))
        for label, value in THEMES:
            self.theme_choice.addItem(label, value)
        self.theme_choice.currentIndexChanged.connect(self._on_theme)
        group.add_row("Colours", self.theme_choice)
        self.accent_choice = compact_combo(QComboBox(self))
        for accent in ("sakura", "lavender", "ocean"):
            self.accent_choice.addItem(accent.title(), accent)
        self.accent_choice.currentIndexChanged.connect(self._on_accent)
        group.add_row("A little colour", self.accent_choice)
        self.reduce_motion = QCheckBox(self)
        self.reduce_motion.toggled.connect(self._on_reduce_motion)
        group.add_row(
            "Reduce motion", self.reduce_motion,
            "Keep transitions still. Your system's reduced-motion preference is always respected.",
        )
        self.app_language = compact_combo(QComboBox(self))
        self.app_language.addItem("English", "en")
        self.app_language.setEnabled(False)
        group.add_row("Language of the app", self.app_language, "More languages are on the way.")
        return group

    def _build_running(self) -> Group:
        group = Group("Starting and closing")
        self.close_to_tray = QCheckBox(self)
        self.close_to_tray.toggled.connect(self._on_tray)
        group.add_row(
            "Keep running in the tray when I close the window",
            self.close_to_tray,
            "Shortcuts and captions keep working.",
        )
        self.start_with_os = QCheckBox(self)
        self.start_with_os.toggled.connect(self._on_start_with_os)
        group.add_row(f"Start {APP_NAME} when this computer starts", self.start_with_os)
        self.autostart_captions = QCheckBox(self)
        self.autostart_captions.toggled.connect(self._on_autostart)
        self.autostart_row = group.add_row("Start captioning right away", self.autostart_captions)
        return group

    def _build_history(self) -> Group:
        group = Group("Transcripts")
        self.keep_history = QCheckBox(self)
        self.keep_history.toggled.connect(self._on_keep_history)
        group.add_row(
            "Save what you caption",
            self.keep_history,
            "Searchable transcripts, kept on this computer only. Sound is never saved.",
        )
        self.retention = compact_combo(QComboBox(self))
        for label, days in RETENTIONS:
            self.retention.addItem(label, days)
        self.retention.currentIndexChanged.connect(self._on_retention)
        self.retention_row = group.add_row("Keep them for", self.retention)
        self.save_original = QCheckBox("Save original words", self)
        self.save_translation = QCheckBox("Save translated subtitles", self)
        self.clear_live_on_stop = QCheckBox("Clear the Live transcript when I stop", self)
        for name, control in (
            ("save_original", self.save_original),
            ("save_translation", self.save_translation),
            ("clear_live_on_stop", self.clear_live_on_stop),
        ):
            control.toggled.connect(
                lambda checked, name=name: self._on_storage_policy(name, checked)
            )
            group.add(control)
        group.add(
            hint_label(
                "Untick both text choices to keep only session dates and languages. "
                "Until I stop captioning keeps everything in memory. "
                "Changing these choices affects new captions; earlier saved sessions remain "
                "until you delete them. Audio and temporary speech files are never saved."
            )
        )
        self.storage_label = QLabel("", self)
        self.storage_label.setObjectName("tertiary")
        group.add(self.storage_label)

        self.delete_button = QPushButton("Delete all transcripts…", self)
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self.request_delete)
        group.add(self.delete_button)

        self.confirm_box = QWidget(self)
        confirm = QHBoxLayout(self.confirm_box)
        confirm.setContentsMargins(0, 0, 0, 0)
        confirm.setSpacing(SPACE["sm"])
        confirm.addWidget(
            hint_label("Delete every transcript on this computer? This can't be undone."), 1
        )
        self.confirm_button = QPushButton("Delete", self)
        self.confirm_button.setObjectName("danger")
        self.confirm_button.clicked.connect(self.confirm_delete)
        self.cancel_button = QPushButton("Keep them", self)
        self.cancel_button.setObjectName("quiet")
        self.cancel_button.clicked.connect(self.cancel_delete)
        confirm.addWidget(self.confirm_button)
        confirm.addWidget(self.cancel_button)
        self.confirm_box.setVisible(False)
        group.add(self.confirm_box)
        return group

    def _build_setup(self) -> Group:
        group = Group("Setup")
        self.rerun_button = QPushButton("Run setup again", self)
        self.rerun_button.clicked.connect(self._rerun)
        group.add_row("Walk through the first-run questions again", self.rerun_button)
        return group

    # ---------- state ----------

    def refresh(self) -> None:
        general = self.settings.general
        with self.quiet():
            self.text_scale.setCurrentIndex(max(0, self.text_scale.findData(general.text_scale)))
            self.theme_choice.setCurrentIndex(max(0, self.theme_choice.findData(general.theme)))
            self.accent_choice.setCurrentIndex(max(0, self.accent_choice.findData(general.accent)))
            self.reduce_motion.setChecked(general.reduce_motion)
            self.close_to_tray.setChecked(general.close_to_tray)
            self.start_with_os.setChecked(general.start_with_os)
            self.autostart_captions.setChecked(general.autostart_captions)
            self.keep_history.setChecked(self.settings.history.enabled)
            self.save_original.setChecked(self.settings.history.save_original)
            self.save_translation.setChecked(self.settings.history.save_translation)
            self.clear_live_on_stop.setChecked(self.settings.history.clear_live_on_stop)
            index = self.retention.findData(self.settings.history.retention_days)
            self.retention.setCurrentIndex(max(0, index))
        self._sync_enabled()
        self.refresh_storage()

    def refresh_storage(self) -> None:
        store = getattr(self._deps, "session_store", None)
        if store is None:
            self.storage_label.setText("")
            return
        try:
            used = int(store.storage_bytes())
        except Exception:
            self.storage_label.setText("")
            return
        self.storage_label.setText(f"Transcripts are using {human_size(used)} on this computer.")

    def _sync_enabled(self) -> None:
        self.autostart_row.setEnabled(self.settings.general.start_with_os)
        self.retention_row.setEnabled(self.settings.history.enabled)
        storing = self.settings.history.enabled and self.settings.history.retention_days != -1
        self.save_original.setEnabled(storing)
        self.save_translation.setEnabled(storing)

    def _on_storage_policy(self, name: str, checked: bool) -> None:
        setattr(self.settings.history, name, checked)
        self.apply("history")

    def _on_accent(self, index: int) -> None:
        self.settings.general.accent = self.accent_choice.itemData(index)
        self.apply("general")

    # ---------- handlers ----------

    def _on_theme(self, index: int) -> None:
        self.settings.general.theme = str(self.theme_choice.itemData(index) or "system")
        self.apply("general")

    def _on_reduce_motion(self, checked: bool) -> None:
        self.settings.general.reduce_motion = checked
        policy().configure(checked)
        self.apply("general")

    def _on_text_scale(self, index: int) -> None:
        self.settings.general.text_scale = self.text_scale.itemData(index)
        self.apply("general")

    def _on_tray(self, checked: bool) -> None:
        self.settings.general.close_to_tray = checked
        self.apply("general")

    def _on_start_with_os(self, checked: bool) -> None:
        self.settings.general.start_with_os = checked
        self._sync_enabled()
        self.apply("general")

    def _on_autostart(self, checked: bool) -> None:
        self.settings.general.autostart_captions = checked
        self.apply("general")

    def _on_keep_history(self, checked: bool) -> None:
        self.settings.history.enabled = checked
        self._sync_enabled()
        self.apply("history")

    def _on_retention(self, index: int) -> None:
        self.settings.history.retention_days = int(self.retention.itemData(index))
        self._sync_enabled()
        self.apply("history")

    def request_delete(self) -> None:
        """Asking is a separate act from doing; the store is not touched yet."""
        reveal(self.confirm_box, True)
        self.delete_button.setVisible(False)

    def cancel_delete(self) -> None:
        reveal(self.confirm_box, False)
        self.delete_button.setVisible(True)

    def confirm_delete(self) -> None:
        store = getattr(self._deps, "session_store", None)
        self.cancel_delete()
        if store is None:
            return
        store.delete_all()
        self.refresh_storage()

    def _rerun(self) -> None:
        self.rerun_onboarding_requested.emit()
        callback = getattr(self._deps, "on_rerun_onboarding", None)
        if callback is not None:
            callback()


class AboutSection(QWidget):
    """Version, updates, licences, and the privacy promise in three sentences."""

    check_updates_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        about = Group(APP_FULL_NAME)
        tagline = QLabel(APP_TAGLINE, self)
        tagline.setObjectName("heading")
        tagline.setWordWrap(True)
        about.add(tagline)
        about.add(hint_label(f"Version {VERSION}"))
        self.update_button = QPushButton("Check for updates", self)
        self.update_button.clicked.connect(self.check_updates_requested)
        about.add(self.update_button)
        # Argos language packs are CC-BY; some are OPUS-MT, some are Argos-trained.
        # Attribution is a licence condition, not a courtesy.
        about.add(hint_label(CREDITS))
        column.addWidget(about)

        privacy = Group("Privacy")
        privacy.add(hint_label(PRIVACY_STATEMENT))
        column.addWidget(privacy)

        credits = Group("Licences")
        self.licences_button = QPushButton("Show licences", self)
        self.licences_button.setObjectName("quiet")
        self.licences_button.clicked.connect(self.toggle_licences)
        credits.add(self.licences_button)
        self.licences = hint_label(LICENCES)
        self.licences_reveal = Expandable(self.licences)
        self.licences_reveal.set_expanded(False)
        credits.add(self.licences_reveal)
        self.source_button = QPushButton("Open the source code", self)
        self.source_button.setObjectName("quiet")
        self.source_button.clicked.connect(lambda: _open(HOMEPAGE_URL))
        self.support_button = QPushButton("Report a problem", self)
        self.support_button.setObjectName("quiet")
        self.support_button.clicked.connect(lambda: _open(SUPPORT_URL))
        credits.add(self.source_button)
        credits.add(self.support_button)
        column.addWidget(credits)

    def toggle_licences(self) -> None:
        showing = not self.licences_reveal.expanded
        self.licences_reveal.set_expanded(showing)
        self.licences_button.setText("Hide licences" if showing else "Show licences")


def _open(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))
