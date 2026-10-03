"""The add-or-edit form for a translation provider.

Two things here are deliberate. The privacy badge updates as the address is
typed, so the consequence of the address is visible before the provider is
saved. And the API key field is write-only: a stored key is reported as "Saved"
and never read back into the widget, so no screen share, screenshot or
accessibility tree can leak it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.network import validate_endpoint
from ...core.secrets import endpoint_key_id
from ...core.settings import ProviderSettings
from ...translation import DEFAULT_BASE_URLS, provider_from_settings, tier_for_url
from ..tokens import SPACE
from ..widgets import PrivacyBadge, compact_combo
from .common import Group, hint_label, show_tier

__all__ = ["KINDS", "ProviderForm"]

KINDS: tuple[tuple[str, str], ...] = (
    ("ollama", "Ollama"),
    ("lmstudio", "LM Studio"),
    ("openai", "Something else that speaks the OpenAI format"),
)

_SHORT_NAMES: dict[str, str] = {
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "openai": "My provider",
}


class ProviderForm(QWidget):
    """Collects one provider's details and hands them up as a plain dict."""

    submitted = Signal(dict)

    _models_fetched = Signal(int, list, str)

    def __init__(
        self,
        key_reader: Callable[[str], str | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_reader = key_reader
        self._editing_id = ""
        self._fetch_busy = False
        self._generation = 0
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.group = Group("Add a provider")

        self.kind_combo = compact_combo(QComboBox(self))
        for kind, label in KINDS:
            self.kind_combo.addItem(label, kind)
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self.group.add_row("Kind", self.kind_combo)

        self.name_edit = QLineEdit(self)
        self.group.add_row("Name", self.name_edit)

        self.base_url_edit = QLineEdit(self)
        self.base_url_edit.setMinimumWidth(220)
        self.base_url_edit.textChanged.connect(self._sync_badge)
        self.badge = PrivacyBadge(parent=self)
        self.group.add_row("Server address", _pair(self.base_url_edit, self.badge, self))

        self.model_combo = compact_combo(QComboBox(self))
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(180)
        self.fetch_button = QPushButton("Fetch models", self)
        self.fetch_button.setObjectName("quiet")
        self.fetch_button.clicked.connect(self.fetch_models)
        self.group.add_row("Model", _pair(self.model_combo, self.fetch_button, self))

        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setMinimumWidth(180)
        self.api_key_state = QLabel("", self)
        self.api_key_state.setObjectName("tertiary")
        self.replace_key_button = QPushButton("Replace", self)
        self.replace_key_button.setObjectName("quiet")
        self.replace_key_button.setVisible(False)
        self.replace_key_button.clicked.connect(self.replace_key)
        keys = _pair(self.api_key_edit, self.api_key_state, self)
        keys.layout().addWidget(self.replace_key_button)
        self.group.add_row(
            "API key",
            keys,
            "Kept in your computer's credential store, and never shown again.",
        )

        self.note = hint_label("")
        self.note.setVisible(False)
        self.group.add(self.note)
        self.save_button = QPushButton("Add provider", self)
        self.save_button.clicked.connect(self.submit)
        self.group.add(self.save_button)
        column.addWidget(self.group)

        self._models_fetched.connect(self._on_models_fetched)
        self._on_kind(0)

    # ---------- state ----------

    @property
    def editing_id(self) -> str:
        return self._editing_id

    def load(self, config: ProviderSettings) -> None:
        """Show an existing provider for editing. The stored key stays hidden."""
        self._generation += 1
        self._editing_id = config.id
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(config.kind)))
        self.name_edit.setText(config.label)
        self.base_url_edit.setText(config.base_url)
        self.model_combo.setEditText(config.model)
        self.save_button.setText("Save changes")
        self._sync_key_state(config.id)

    def reset(self) -> None:
        self._generation += 1
        self._editing_id = ""
        self.save_button.setText("Add provider")
        self.name_edit.clear()
        self.model_combo.setEditText("")
        self.api_key_edit.clear()
        self.show_note("")
        self._on_kind(self.kind_combo.currentIndex())
        self._sync_key_state("")

    def payload(self) -> dict:
        kind = str(self.kind_combo.currentData() or "openai")
        return {
            "id": self._editing_id,
            "kind": kind,
            "label": self.name_edit.text().strip() or _SHORT_NAMES.get(kind, "My provider"),
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_combo.currentText().strip(),
            "api_key": self.api_key_edit.text(),
        }

    def submit(self) -> None:
        payload = self.payload()
        try:
            payload["base_url"] = validate_endpoint(payload["base_url"])
            if not payload["model"] or len(payload["model"]) > 256:
                raise ValueError("Choose a model name of up to 256 characters.")
        except ValueError as exc:
            self.show_note(str(exc))
            return
        self.api_key_edit.clear()
        self.show_note("")
        self.submitted.emit(payload)

    def show_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def replace_key(self) -> None:
        self.api_key_edit.clear()
        self.api_key_edit.setVisible(True)
        self.api_key_state.setText("")
        self.replace_key_button.setVisible(False)

    def refresh_theme(self) -> None:
        self._sync_badge()

    # ---------- internals ----------

    def _sync_key_state(self, provider_id: str) -> None:
        saved = bool(self._stored_key(provider_id))
        self.api_key_edit.clear()
        self.api_key_edit.setVisible(not saved)
        self.api_key_state.setText("Saved" if saved else "")
        self.replace_key_button.setVisible(saved)

    def _stored_key(self, provider_id: str) -> str | None:
        if self._key_reader is None or not provider_id:
            return None
        try:
            return self._key_reader(endpoint_key_id(provider_id, self.base_url_edit.text()))
        except Exception:
            return None

    def _on_kind(self, _index: int) -> None:
        kind = str(self.kind_combo.currentData() or "openai")
        if not self._editing_id:
            self.base_url_edit.setText(DEFAULT_BASE_URLS.get(kind, ""))
            self.name_edit.setText(_SHORT_NAMES.get(kind, "My provider"))
        self._sync_badge()

    def _sync_badge(self) -> None:
        self._generation += 1
        show_tier(self.badge, tier_for_url(self.base_url_edit.text().strip()))

    def fetch_models(self) -> None:
        if self._fetch_busy:
            return
        payload = self.payload()
        try:
            payload["base_url"] = validate_endpoint(payload["base_url"])
        except ValueError as exc:
            self.show_note(str(exc))
            return
        self._fetch_busy = True
        self.fetch_button.setEnabled(False)
        generation = self._generation
        self.show_note("Looking for models…")
        config = ProviderSettings(
            id=payload["id"] or "draft",
            kind=payload["kind"],
            label=payload["label"],
            base_url=payload["base_url"],
        )
        key = payload["api_key"] or self._stored_key(payload["id"]) or None

        def run() -> None:
            try:
                provider = provider_from_settings(config, key)
                try:
                    names = provider.list_models()
                finally:
                    provider.close()
            except Exception as exc:
                self._models_fetched.emit(generation, [], str(exc))
                return
            self._models_fetched.emit(generation, list(names), "")

        threading.Thread(target=run, name="subbyai-model-list", daemon=True).start()

    def _on_models_fetched(self, generation: int, names: list, error: str) -> None:
        self._fetch_busy = False
        self.fetch_button.setEnabled(True)
        if generation != self._generation:
            self.show_note("")
            return
        if error:
            self.show_note(error)
            return
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems([str(name) for name in names])
        if current:
            self.model_combo.setEditText(current)
        self.show_note(f"Found {len(names)} to choose from.")


def _pair(first: QWidget, second: QWidget, parent: QWidget) -> QWidget:
    holder = QWidget(parent)
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE["sm"])
    row.addWidget(first)
    row.addWidget(second)
    return holder
