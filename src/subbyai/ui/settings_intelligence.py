"""Caption quality, translation providers, and the optional assistant.

The privacy badge on every row is computed from the address the provider will
actually be called on, never from its name or its kind. A provider called
"Local AI" pointed at a hosted address wears a Cloud badge, and the consent line
that appears before it is ever used says exactly where the text would go.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.events import PrivacyTier
from ..core.network import validate_endpoint
from ..core.secrets import endpoint_key_id
from ..core.settings import ProviderSettings, QualityTier, SettingsStore
from ..translation import (
    BUILTIN_PROVIDER_ID,
    DEFAULT_BASE_URLS,
    ArgosProvider,
    provider_from_settings,
    tier_for_url,
)
from .model_catalogue import ModelCatalogue
from .settings_widgets import (
    Group,
    PrivacyBadge,
    SettingsSection,
    hint_label,
    show_tier,
)
from .settings_widgets.provider_form import ProviderForm
from .settings_widgets.provider_row import ProviderRow
from .settings_widgets.quality_picker import QualityPicker
from .tokens import SPACE
from .widgets import compact_combo

log = logging.getLogger(__name__)

__all__ = ["IntelligenceSection", "builtin_config", "provider_tier"]

HEADER_NOTE = "AI features are optional. Each one shows exactly where it runs."


def provider_tier(config: ProviderSettings) -> PrivacyTier:
    """Where this provider runs, judged only by the address it is called on."""
    if config.kind == "builtin" or config.id == BUILTIN_PROVIDER_ID:
        return PrivacyTier.ON_DEVICE
    return tier_for_url(config.base_url or DEFAULT_BASE_URLS.get(config.kind, ""))


def builtin_config() -> ProviderSettings:
    """The bundled translator, which is always present and never configured."""
    return ProviderSettings(
        id=BUILTIN_PROVIDER_ID, kind="builtin", label="Built-in translator", enabled=True
    )


class IntelligenceSection(SettingsSection):
    """Quality tiers, the provider chain, and the optional assistant."""

    restart_capture_requested = Signal()
    providers_changed = Signal()

    _test_finished = Signal(str, str, bool, str)

    def __init__(
        self, store: SettingsStore, deps: Any = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(store, parent)
        self._deps = deps
        self._rows: dict[str, ProviderRow] = {}
        self._tests_running: set[str] = set()

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(hint_label(HEADER_NOTE))
        column.addSpacing(SPACE["lg"])
        self.quality = QualityPicker(
            getattr(deps, "model_manager", None), getattr(deps, "capability", None), self
        )
        self.quality.tier_chosen.connect(self._on_tier)
        self.quality.download_completed.connect(self.restart_capture_requested)
        column.addWidget(self.quality)

        self.catalogue = ModelCatalogue(
            getattr(deps, "model_manager", None),
            active_model=lambda: self.settings.model_name,
            parent=self,
        )
        self.catalogue.model_chosen.connect(self._on_model_chosen)
        self.catalogue.tier_restored.connect(self._on_tier_restored)
        self.catalogue.install_requested.connect(self._on_install)
        self.catalogue.remove_requested.connect(self._on_remove)
        column.addWidget(self.catalogue)
        column.addWidget(self._build_vocabulary())
        column.addWidget(self._build_providers())
        self.form = ProviderForm(getattr(deps, "api_key_get", None), self)
        self.form.submitted.connect(self._on_submitted)
        column.addWidget(self.form)
        column.addWidget(self._build_assistant())

        self._test_finished.connect(self._on_test_finished)
        self.refresh()

    # ---------- quality ----------

    def _on_tier(self, value: str) -> None:
        self.settings.captions.quality = QualityTier(value)
        # Picking a tier clears any specific engine, otherwise the override
        # silently wins and the tier cards become decoration.
        self.settings.captions.model_override = ""
        self.apply("captions")
        self._refresh_catalogue()
        if not self._muted:
            self.restart_capture_requested.emit()

    # ---------- the catalogue ----------

    def _refresh_catalogue(self) -> None:
        self.catalogue.refresh(
            self.settings.captions.model_override, self.settings.captions.quality
        )
        self.quality.refresh_states()

    def _on_model_chosen(self, model_id: str) -> None:
        self.settings.captions.model_override = model_id
        self.apply("captions")
        self._refresh_catalogue()
        if not self._muted:
            self.restart_capture_requested.emit()

    def _on_tier_restored(self) -> None:
        self.settings.captions.model_override = ""
        self.apply("captions")
        self._refresh_catalogue()
        if not self._muted:
            self.restart_capture_requested.emit()

    def _on_install(self, model_id: str) -> None:
        self.quality.start_download(model_id)

    def _on_remove(self, model_id: str) -> None:
        from ..asr.models import ModelInUse

        manager = getattr(self._deps, "model_manager", None)
        if manager is None:
            return
        # Release first. The engine cache holds the active model open on
        # purpose, so without this the unlink fails on Windows and the user is
        # told to stop captions — which is not what frees it.
        release = getattr(self._deps, "release_models", None)
        try:
            if callable(release):
                release()
            freed = manager.delete(model_id)
        except (ModelInUse, RuntimeError, ValueError) as exc:
            self.catalogue.storage.setText(str(exc))
            return
        log.info("Removed %s, reclaiming %d bytes", model_id, freed)
        if self.settings.captions.model_override == model_id:
            self.settings.captions.model_override = ""
            self.apply("captions")
        self._refresh_catalogue()

    # ---------- vocabulary ----------

    def _build_vocabulary(self) -> Group:
        group = Group(
            "Names and terms",
            "Words recognition keeps getting wrong, one per line or comma-separated: "
            "character names, places, jargon. Captions lean toward them; nothing is "
            "added to the text itself. Applies the next time captions start.",
        )
        self.vocabulary_edit = QPlainTextEdit(self)
        self.vocabulary_edit.setObjectName("vocabulary")
        self.vocabulary_edit.setPlaceholderText("Midgar, Nibelheim, Chocobo")
        self.vocabulary_edit.setFixedHeight(76)
        self.vocabulary_edit.setTabChangesFocus(True)
        # Debounced: saving per keystroke would rewrite the settings file and
        # re-broadcast the captions section on every letter.
        self._vocabulary_save = QTimer(self)
        self._vocabulary_save.setSingleShot(True)
        self._vocabulary_save.setInterval(600)
        self._vocabulary_save.timeout.connect(self._save_vocabulary)
        self.vocabulary_edit.textChanged.connect(self._vocabulary_save.start)
        group.add(self.vocabulary_edit)
        return group

    def _save_vocabulary(self) -> None:
        text = self.vocabulary_edit.toPlainText()
        if text != self.settings.captions.vocabulary:
            self.settings.captions.vocabulary = text
            self.apply("captions")

    # ---------- providers ----------

    def _build_providers(self) -> Group:
        group = Group(
            "Translation providers",
            "SubbyAI tries these in order and moves down the list when one is busy.",
        )
        self.provider_list = QListWidget(self)
        self.provider_list.setObjectName("providers")
        self.provider_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.provider_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.provider_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Rows shrink and elide instead of widening the window; without this
        # a provider row demanded 701px and the whole settings pane scrolled
        # sideways.
        self.provider_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.provider_list.model().rowsMoved.connect(self._on_rows_moved)
        group.add(self.provider_list)
        return group

    def provider_ids(self) -> list[str]:
        """The chain, in the order it will actually be tried."""
        known = [BUILTIN_PROVIDER_ID, *[p.id for p in self.settings.intelligence.providers]]
        ordered = [i for i in self.settings.intelligence.translation_order if i in known]
        return ordered + [i for i in known if i not in ordered]

    def config_for(self, provider_id: str) -> ProviderSettings | None:
        stored = next(
            (p for p in self.settings.intelligence.providers if p.id == provider_id), None
        )
        if stored is None and provider_id == BUILTIN_PROVIDER_ID:
            return builtin_config()
        return stored

    def _rebuild_providers(self) -> None:
        self.provider_list.clear()
        self._rows = {}
        for provider_id in self.provider_ids():
            config = self.config_for(provider_id)
            if config is None:
                continue
            row = ProviderRow(config, provider_tier(config), self)
            row.use_toggled.connect(self._on_use)
            row.test_requested.connect(self.test_provider)
            row.edit_requested.connect(self.edit_provider)
            row.remove_requested.connect(self.remove_provider)
            row.move_requested.connect(self._on_move_requested)
            row.consent_given.connect(self._on_consent)
            item = QListWidgetItem(self.provider_list)
            item.setData(Qt.ItemDataRole.UserRole, provider_id)
            item.setSizeHint(row.sizeHint())
            self.provider_list.setItemWidget(item, row)
            self._rows[provider_id] = row
        height = sum(
            self.provider_list.sizeHintForRow(i) for i in range(self.provider_list.count())
        )
        self.provider_list.setFixedHeight(max(64, height + 12))

    def rows(self) -> dict[str, ProviderRow]:
        return dict(self._rows)

    def move_provider(self, source: int, destination: int) -> None:
        """Reorder the chain, from a drag or from the keyboard."""
        ids = self.provider_ids()
        if not 0 <= source < len(ids) or not 0 <= destination < len(ids):
            return
        if source == destination:
            return
        ids.insert(destination, ids.pop(source))
        self.settings.intelligence.translation_order = ids
        self._rebuild_providers()
        self.apply("intelligence")
        self.providers_changed.emit()

    def _on_move_requested(self, provider_id: str, step: int) -> None:
        ids = self.provider_ids()
        if provider_id in ids:
            self.move_provider(ids.index(provider_id), ids.index(provider_id) + step)

    def _on_rows_moved(self, *_args) -> None:
        # Deferred: the view is still tearing down the dragged row's widget.
        QTimer.singleShot(0, self._commit_drag)

    def _commit_drag(self) -> None:
        ids = [
            str(self.provider_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.provider_list.count())
        ]
        if ids and ids != self.settings.intelligence.translation_order:
            self.settings.intelligence.translation_order = ids
            self._rebuild_providers()
            self.apply("intelligence")
            self.providers_changed.emit()

    def _on_use(self, provider_id: str, checked: bool) -> None:
        config = self.config_for(provider_id)
        row = self._rows.get(provider_id)
        if config is None or row is None:
            return
        if (
            checked
            and provider_tier(config) is PrivacyTier.CLOUD
            and config.consent_url != validate_endpoint(config.base_url)
        ):
            row.set_use(False)
            row.ask_consent(
                f"Turning this on sends what you caption to {config.label}. "
                "Everything else stays on this computer."
            )
            return
        config.enabled = checked
        row.show_note("", "ok" if checked else "idle")
        self.apply("intelligence")
        self.providers_changed.emit()

    def _on_consent(self, provider_id: str) -> None:
        config = self.config_for(provider_id)
        row = self._rows.get(provider_id)
        if config is None or row is None:
            return
        config.consent_url = validate_endpoint(config.base_url)
        config.enabled = True
        row.set_use(True)
        row.show_note("", "ok")
        self.apply("intelligence")
        self.providers_changed.emit()

    def test_provider(self, provider_id: str) -> None:
        if provider_id in self._tests_running:
            return
        config = self.config_for(provider_id)
        row = self._rows.get(provider_id)
        if config is None or row is None:
            return
        row.show_note("Checking…", "busy")
        self._tests_running.add(provider_id)
        config = replace(config)
        key = self._stored_key(provider_id)

        def run() -> None:
            ok, message = _check(config, key)
            self._test_finished.emit(provider_id, config.base_url, ok, message)

        threading.Thread(target=run, name="subbyai-provider-test", daemon=True).start()

    def _on_test_finished(self, provider_id: str, endpoint: str, ok: bool, message: str) -> None:
        self._tests_running.discard(provider_id)
        config = self.config_for(provider_id)
        row = self._rows.get(provider_id)
        if row is not None and config is not None and config.base_url == endpoint:
            row.show_note(message, "ok" if ok else "bad")

    def edit_provider(self, provider_id: str) -> None:
        config = self.config_for(provider_id)
        if config is not None and provider_id != BUILTIN_PROVIDER_ID:
            self.form.load(config)

    def remove_provider(self, provider_id: str) -> None:
        config = self.config_for(provider_id)
        remover = getattr(self._deps, "api_key_delete", None)
        if config is not None and callable(remover):
            try:
                remover(endpoint_key_id(provider_id, config.base_url))
            except Exception:
                row = self._rows.get(provider_id)
                if row is not None:
                    row.show_note(
                        "Could not remove its stored key. Check your OS vault and retry.", "bad"
                    )
                return
        intelligence = self.settings.intelligence
        intelligence.providers = [p for p in intelligence.providers if p.id != provider_id]
        intelligence.translation_order = [
            i for i in intelligence.translation_order if i != provider_id
        ]
        if intelligence.assistant_provider == provider_id:
            intelligence.assistant_provider = ""
        if self.form.editing_id == provider_id:
            self.form.reset()
        self._rebuild_providers()
        self._sync_assistant_choices()
        self.apply("intelligence")
        self.providers_changed.emit()

    def _on_submitted(self, payload: dict) -> None:
        intelligence = self.settings.intelligence
        provider_id = payload["id"] or _unique_id(
            payload["label"], {p.id for p in intelligence.providers}
        )
        existing = self.config_for(provider_id)
        config = ProviderSettings(
            id=provider_id, kind=payload["kind"], label=payload["label"],
            base_url=payload["base_url"], model=payload["model"],
        )
        if existing is not None and existing.base_url == config.base_url:
            config.enabled = existing.enabled
            config.consent_url = existing.consent_url
        writer = getattr(self._deps, "api_key_set", None)
        if payload["api_key"]:
            if writer is None:
                self.form.show_note(
                    "The credential store is unavailable; the provider was not saved."
                )
                return
            try:
                writer(endpoint_key_id(provider_id, config.base_url), payload["api_key"])
            except Exception:
                self.form.show_note(
                    "The key could not be saved securely. Check your OS credential store and retry."
                )
                return
        if existing is None or existing.id == BUILTIN_PROVIDER_ID:
            # Not enabled on creation: a cloud provider that is live the
            # moment it is added would receive caption text before the
            # consent gate has ever been shown.
            intelligence.providers.append(config)
            intelligence.translation_order.append(provider_id)
        else:
            intelligence.providers[intelligence.providers.index(existing)] = config

        self.form.reset()
        self._rebuild_providers()
        self._sync_assistant_choices()
        self.apply("intelligence")
        self.providers_changed.emit()

    def _stored_key(self, provider_id: str) -> str | None:
        reader = getattr(self._deps, "api_key_get", None)
        if reader is None or not provider_id:
            return None
        try:
            config = self.config_for(provider_id)
            return reader(endpoint_key_id(provider_id, config.base_url)) if config else None
        except Exception:
            return None

    # ---------- assistant ----------

    def _build_assistant(self) -> Group:
        group = Group("Explain and summarise")
        self.assistant_toggle = QCheckBox(self)
        self.assistant_toggle.toggled.connect(self._on_assistant)
        group.add_row(
            "Ask questions about your transcripts",
            self.assistant_toggle,
            "Summaries and explanations, only when you ask for them.",
        )
        chooser = QWidget(self)
        row = QHBoxLayout(chooser)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["sm"])
        self.assistant_provider = compact_combo(QComboBox(self))
        self.assistant_provider.setMinimumWidth(180)
        self.assistant_provider.currentIndexChanged.connect(self._on_assistant_provider)
        self.assistant_badge = PrivacyBadge(parent=self)
        row.addWidget(self.assistant_provider)
        row.addWidget(self.assistant_badge)
        self.assistant_row = group.add_row("Answers come from", chooser)
        self.assistant_note = hint_label("")
        self.assistant_note.setVisible(False)
        group.add(self.assistant_note)
        return group

    def _sync_assistant_choices(self) -> None:
        with self.quiet():
            chosen = self.settings.intelligence.assistant_provider
            self.assistant_provider.clear()
            for provider_id in self.provider_ids():
                config = self.config_for(provider_id)
                if config is not None and config.kind != "builtin":
                    self.assistant_provider.addItem(config.label or provider_id, provider_id)
            available = self.assistant_provider.count() > 0
            self.assistant_toggle.setEnabled(available)
            if not available:
                self.assistant_provider.addItem("Add a chat provider first", "")
            self.assistant_provider.setCurrentIndex(
                max(0, self.assistant_provider.findData(chosen))
            )
        self._sync_assistant_badge()

    def _sync_assistant_badge(self) -> None:
        config = self.config_for(str(self.assistant_provider.currentData() or ""))
        tier = provider_tier(config) if config is not None else None
        show_tier(self.assistant_badge, tier)
        cloud = tier is PrivacyTier.CLOUD
        self.assistant_note.setVisible(cloud)
        if cloud:
            self.assistant_note.setText(
                "Only the text you send is uploaded. Translation providers "
                "can also receive live caption text when enabled."
            )
        self.assistant_row.setEnabled(self.settings.intelligence.assistant_enabled)

    def _on_assistant(self, checked: bool) -> None:
        self.settings.intelligence.assistant_enabled = checked
        self._sync_assistant_badge()
        self.apply("intelligence")

    def _on_assistant_provider(self, index: int) -> None:
        self.settings.intelligence.assistant_provider = str(
            self.assistant_provider.itemData(index) or ""
        )
        self._sync_assistant_badge()
        self.apply("intelligence")

    # ---------- state ----------

    def refresh(self) -> None:
        with self.quiet():
            self.quality.set_tier(self.settings.captions.quality)
            self.vocabulary_edit.setPlainText(self.settings.captions.vocabulary)
            self.assistant_toggle.setChecked(self.settings.intelligence.assistant_enabled)
        self._rebuild_providers()
        self._sync_assistant_choices()
        self._refresh_catalogue()


def _unique_id(label: str, taken: set[str]) -> str:
    base = "".join(c if c.isalnum() else "-" for c in label.strip().lower()).strip("-")
    base = base or "provider"
    candidate, index = base, 2
    while candidate in taken or candidate == BUILTIN_PROVIDER_ID:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _check(config: ProviderSettings, api_key: str | None) -> tuple[bool, str]:
    """Ask a provider whether it is really there. Runs off the UI thread."""
    if config.kind == "builtin" or config.id == BUILTIN_PROVIDER_ID:
        try:
            pairs = ArgosProvider().installed_pairs()
        except Exception as exc:
            return False, str(exc)
        if not pairs:
            return False, "No language pairs are installed yet. Pick a language to get one."
        return True, f"Ready. {len(pairs)} language pairs, all on this device."
    try:
        provider = provider_from_settings(config, api_key)
        try:
            return provider.test_connection()
        finally:
            provider.close()
    except Exception as exc:
        return False, str(exc)
