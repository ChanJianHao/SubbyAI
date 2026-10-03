"""Settings: a section list, a narrow content pane, and no Apply button.

Every control writes its value and announces its section the moment it changes.
The handful of settings the capture loop only reads at start emit
``restart_capture_requested`` instead of asking the user to restart anything —
they are told it reconnects itself, and then it does.

The view never touches the pipeline, the overlay or the database directly. It
writes settings, emits signals, and calls only the collaborators it was handed
in :class:`SettingsDeps`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import SettingsStore
from . import theme
from .settings_audio import AudioSection
from .settings_captions import CaptionsSection
from .settings_diagnostics import DiagnosticsSection
from .settings_engine import EngineSection, RemoteSection
from .settings_general import AboutSection, GeneralSection
from .settings_home import EverydaySection
from .settings_intelligence import IntelligenceSection
from .settings_languages import LanguagesSection
from .settings_widgets import restyle
from .shortcut_editor import ShortcutEditor
from .tokens import SPACE

__all__ = ["SettingsDeps", "SettingsView"]

SECTIONS: tuple[tuple[str, str], ...] = (
    ("everyday", "Everyday"),
    ("languages", "Languages & translation"),
    ("captions", "Captions"),
    ("audio", "Audio & sources"),
    ("intelligence", "Models & providers"),
    ("engine", "Transcription & performance"),
    ("processing", "Remote Processing"),
    ("diagnostics", "Diagnostics"),
    ("shortcuts", "Shortcuts"),
    ("general", "General"),
    ("about", "About"),
)

CONTENT_WIDTH = 560
NAV_WIDTH = 200


@dataclass(frozen=True, slots=True)
class SettingsDeps:
    """Collaborators the settings view cannot build for itself.

    Every field is optional so a test — or an early boot — can hand over a bare
    object and still get a working screen.
    """

    audio_devices: Callable[[], list] | None = None
    model_manager: Any = None
    capability: Any = None
    hotkeys: Any = None
    session_store: Any = None
    on_rerun_onboarding: Callable[[], None] | None = None
    api_key_get: Callable[[str], str | None] | None = None
    api_key_set: Callable[[str, str], None] | None = None
    api_key_delete: Callable[[str], None] | None = None
    release_models: Callable[[], None] | None = None
    diagnostics: Callable[[], dict] | None = None
    """Unloads the active model so its files can be removed. Without this a
    delete fails on Windows, because stopping captions keeps the model warm."""


class SettingsView(QWidget):
    """The seven settings sections, and the signals the shell acts on."""

    restart_capture_requested = Signal()
    preview_requested = Signal(bool)
    style_changed = Signal()
    check_updates_requested = Signal()
    shortcuts_changed = Signal(dict)

    def __init__(
        self, store: SettingsStore, deps: SettingsDeps | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._deps = deps if deps is not None else SettingsDeps()

        self.everyday = EverydaySection(store, self._deps, self)
        self.engine = EngineSection(store, self)
        self.processing = RemoteSection(store, self._deps, self)
        self.diagnostics = DiagnosticsSection(self._deps, self)

        self.languages = LanguagesSection(store, self)
        self.captions = CaptionsSection(store, self)
        self.audio = AudioSection(store, self._deps, self)
        self.intelligence = IntelligenceSection(store, self._deps, self)
        self.shortcuts = ShortcutEditor(store.settings.shortcuts.globals, self)
        self.general = GeneralSection(store, self._deps, self)
        self.about = AboutSection(self)
        self._pages: dict[str, QWidget] = {
            "everyday": self.everyday,
            "engine": self.engine,
            "processing": self.processing,
            "diagnostics": self.diagnostics,
            "languages": self.languages,
            "captions": self.captions,
            "audio": self.audio,
            "intelligence": self.intelligence,
            "shortcuts": self.shortcuts,
            "general": self.general,
            "about": self.about,
        }

        self.nav = QListWidget(self)
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(NAV_WIDTH)
        # "Languages & translation" is wider than the rail. Elide it rather than
        # let the list scroll sideways — a horizontal scrollbar in a navigation
        # rail is how settings became unreachable in the first place.
        self.nav.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setWordWrap(False)
        self.stack = QStackedWidget(self)
        self._keys: list[str] = []
        for key, label in SECTIONS:
            self.nav.addItem(label)
            self.stack.addWidget(_page(self._pages[key]))
            self._keys.append(key)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        rail = QWidget()
        rail.setFixedWidth(NAV_WIDTH)
        rail_layout = QVBoxLayout(rail)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a setting…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_nav)
        rail_layout.addWidget(self.search)
        self.advanced_toggle = QCheckBox("Advanced Mode")
        self.advanced_toggle.setChecked(store.settings.general.advanced_mode)
        self.advanced_toggle.setToolTip(
            "Show engine, model and server controls. Your settings are preserved when hidden."
        )
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        rail_layout.addWidget(self.advanced_toggle)
        self.nav.setFixedWidth(NAV_WIDTH - 18)
        rail_layout.addWidget(self.nav, 1)
        row.addWidget(rail)
        divider = QFrame(self)
        divider.setObjectName("hairline")
        divider.setFixedWidth(1)
        row.addWidget(divider)
        row.addWidget(self.stack, 1)

        self.nav.currentRowChanged.connect(self._on_section)
        self.nav.setCurrentRow(0)
        self._connect()
        self.refresh_theme()
        self._filter_nav()

    def _toggle_advanced(self, enabled: bool) -> None:
        self._store.settings.general.advanced_mode = enabled
        self._store.notify("general")
        self._filter_nav()

    def _filter_nav(self, query: str = "") -> None:
        query = self.search.text().strip().casefold()
        advanced = self.advanced_toggle.isChecked()
        advanced_keys = {"intelligence", "engine", "processing", "diagnostics", "shortcuts"}
        for index, (key, label) in enumerate(SECTIONS):
            page_text = " ".join(child.text() for child in self._pages[key].findChildren(QLabel))
            matches = query in (label + " " + page_text).casefold()
            self.nav.item(index).setHidden((key in advanced_keys and not advanced) or not matches)
        current = self.nav.currentRow()
        if current >= 0 and self.nav.item(current).isHidden():
            first = next(
                (i for i in range(self.nav.count()) if not self.nav.item(i).isHidden()), -1
            )
            if first >= 0:
                self.nav.setCurrentRow(first)

    # ---------- wiring ----------

    def _connect(self) -> None:
        self.everyday.restart_capture_requested.connect(self.restart_capture_requested)
        self.everyday.style_changed.connect(self.style_changed)
        self.everyday.preview_requested.connect(lambda: self.preview_requested.emit(True))
        self.engine.restart_capture_requested.connect(self.restart_capture_requested)
        self.processing.restart_capture_requested.connect(self.restart_capture_requested)
        self.languages.restart_capture_requested.connect(self.restart_capture_requested)
        self.languages.change_provider_requested.connect(lambda: self.show_section("intelligence"))
        self.audio.restart_capture_requested.connect(self.restart_capture_requested)
        self.intelligence.restart_capture_requested.connect(self.restart_capture_requested)
        self.intelligence.providers_changed.connect(self.languages.sync_provider)
        self.captions.style_changed.connect(self.style_changed)
        self.about.check_updates_requested.connect(self.check_updates_requested)
        self.shortcuts.bindings_changed.connect(self._on_shortcuts)

    def _on_shortcuts(self, bindings: dict) -> None:
        """Save, re-register, and report per row what the system refused."""
        self._store.settings.shortcuts.globals = dict(bindings)
        self._store.notify("shortcuts")
        manager = getattr(self._deps, "hotkeys", None)
        if manager is not None:
            self.shortcuts.set_failures(manager.register_all(dict(bindings)))
        self.shortcuts_changed.emit(dict(bindings))

    def _on_section(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.preview_requested.emit(self._current_key() == "captions")

    def _current_key(self) -> str:
        index = self.nav.currentRow()
        return self._keys[index] if 0 <= index < len(self._keys) else ""

    # ---------- public API ----------

    def show_section(self, key: str) -> None:
        if key in self._keys:
            if key in {"intelligence", "engine", "processing", "diagnostics", "shortcuts"}:
                self.advanced_toggle.setChecked(True)
            self.search.clear()
            self.nav.setCurrentRow(self._keys.index(key))

    def set_level(self, level: float) -> None:
        """Feed the audio meter. The shell owns the capture, so it owns the numbers."""
        self.audio.set_level(level)

    def refresh(self) -> None:
        """Re-read the store, for when something else changed a setting."""
        self.languages.refresh()
        self.captions.refresh()
        self.audio.refresh()
        self.intelligence.refresh()
        self.general.refresh()
        self.shortcuts.set_bindings(self._store.settings.shortcuts.globals)
        self.everyday.refresh()
        self.engine.refresh()
        self.processing.refresh()

    def refresh_theme(self) -> None:
        palette = theme.current()
        self.nav.setStyleSheet(
            "QListWidget#nav { background: transparent; border: none;"
            f" padding: {SPACE['md']}px; }}"
            "QListWidget#nav::item { padding: 8px 10px; border-radius: 8px;"
            f" color: {palette.text_secondary}; }}"
            f"QListWidget#nav::item:selected {{ background: {palette.accent_subtle};"
            f" color: {palette.text}; }}"
        )
        restyle(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.preview_requested.emit(self._current_key() == "captions")

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.preview_requested.emit(False)


def _page(content: QWidget) -> QScrollArea:
    """One scrollable page, its text kept to a readable measure."""
    holder = QWidget()
    column = QVBoxLayout(holder)
    column.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
    column.setSpacing(0)
    content.setMaximumWidth(CONTENT_WIDTH)
    column.addWidget(content)
    column.addStretch(1)
    area = QScrollArea()
    area.setWidget(holder)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    return area
