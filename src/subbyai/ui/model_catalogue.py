"""The speech-model catalogue: every engine, with install and remove.

The four quality tiers stay the front door and stay the whole default path.
This is the room behind it, for someone who wants a specific engine or wants
their disk space back. Two rules hold it together:

**Nothing here is required.** Every model listed downloads from a public host
and runs on this machine. No key, no account, no second runtime to install.
The catalogue widens the choice; it never becomes a setup step.

**The tier is recoverable in one click.** Choosing a specific engine is an
override, and an override you cannot undo is a trap — so the anchor line at the
top always says which tier you left and offers it back.

Removal is the one genuinely dangerous action, because the engine holds the
active model open. ``release`` is called before every delete; without it
Windows refuses the unlink and the user is told to "stop captions", which does
not help since stopping deliberately keeps the model warm.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..asr.catalogue import ModelSpec, all_specs, spec
from ..core.settings import TIER_MODELS, QualityTier
from . import theme
from .exports import human_bytes
from .settings_widgets import Group
from .tokens import RADIUS, SPACE
from .widgets import tier_label, wrap_label

__all__ = ["ModelCatalogue", "ModelRow"]


#: How many languages each coverage class really means. "multilingual" is
#: Whisper's 99; Parakeet's 25 European ones are not the same thing, and saying
#: 99 for both would send a Japanese user to an engine that cannot hear them.
_LANGUAGE_TEXT = {
    "en": "English only",
    "european": "25 European languages",
    "multilingual": "99 languages",
}


def language_text(model: ModelSpec) -> str:
    return _LANGUAGE_TEXT.get(model.languages, model.languages)


class ModelRow(QFrame):
    """One model: what it is good at, what it costs, and what you can do to it."""

    use_requested = Signal(str)
    install_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, model: ModelSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("choicecard")
        self.model = model
        self._installed = False
        self._in_use = False

        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        row.setSpacing(SPACE["md"])

        text = QVBoxLayout()
        text.setSpacing(SPACE["xs"])
        self._name = QLabel(model.display, self)
        self._name.setFont(theme.ui_font("body_strong"))
        self._blurb = wrap_label(QLabel(model.blurb, self))
        self._blurb.setObjectName("secondary")
        self._facts = wrap_label(QLabel("", self))
        self._facts.setObjectName("tertiary")
        self._credit = wrap_label(QLabel("", self))
        self._credit.setObjectName("tertiary")
        self._credit.setVisible(False)
        text.addWidget(self._name)
        text.addWidget(self._blurb)
        text.addWidget(self._facts)
        text.addWidget(self._credit)
        row.addLayout(text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(SPACE["xs"])
        actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.use_button = QPushButton("Use", self)
        self.use_button.clicked.connect(lambda: self.use_requested.emit(model.id))
        self.install_button = QPushButton("Install", self)
        self.install_button.clicked.connect(lambda: self.install_requested.emit(model.id))
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(model.id))
        for button in (self.use_button, self.install_button, self.remove_button):
            button.setMinimumWidth(96)
            actions.addWidget(button)
        row.addLayout(actions)

        self.refresh_theme()

    def set_state(self, installed: bool, in_use: bool, disk_bytes: int = 0) -> None:
        self._installed, self._in_use = installed, in_use
        facts = [language_text(self.model)]
        if installed:
            facts.append(f"{human_bytes(disk_bytes or self.model.size_bytes)} on disk")
        else:
            facts.append(f"{human_bytes(self.model.size_bytes)} download")
        if self.model.licence:
            facts.append(f"{self.model.publisher} · {self.model.licence}")
        if not self.model.reports_confidence:
            facts.append("does not flag uncertain words")
        if not self.model.detects_language:
            facts.append("needs you to pick the language")
        self._facts.setText("  ·  ".join(facts))
        # CC-BY is a condition of use, not a footnote: the credit belongs on
        # the row itself rather than only in a file nobody opens.
        self._credit.setText(self.model.attribution)
        self._credit.setVisible(bool(self.model.attribution))

        self.install_button.setVisible(not installed)
        self.remove_button.setVisible(installed and not in_use)
        self.use_button.setVisible(installed and not in_use)
        # The model in use has no Remove: the engine holds it open, and an
        # offer that always fails is worse than no offer.
        self._name.setText(f"{self.model.display} — in use" if in_use else self.model.display)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        palette = theme.current()
        edge = palette.accent if self._in_use else palette.hairline
        self.setStyleSheet(
            f"QFrame#choicecard {{ background: {palette.raised};"
            f" border: 1px solid {edge};"
            f" border-radius: {RADIUS['md']}px; }}"
        )


class ModelCatalogue(QWidget):
    """The whole sheet: the anchor line, then a row per model."""

    model_chosen = Signal(str)
    tier_restored = Signal()
    install_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(
        self,
        models: object | None = None,
        active_model: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._models = models
        self._active_model = active_model or (lambda: "")

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.group = Group(
            "Choose a specific engine",
            "Every engine here runs on your computer. None of them needs an "
            "account or an internet connection once installed.",
        )
        self.anchor = wrap_label(QLabel("", self))
        self.anchor.setObjectName("secondary")
        self.back_button = QPushButton("Back to the recommended setting", self)
        self.back_button.clicked.connect(self.tier_restored.emit)
        self.group.add(self.anchor)
        self.group.add(self.back_button)

        self.rows: dict[str, ModelRow] = {}
        for model in all_specs():
            row = ModelRow(model, self)
            row.use_requested.connect(self.model_chosen.emit)
            row.install_requested.connect(self.install_requested.emit)
            row.remove_requested.connect(self.remove_requested.emit)
            self.rows[model.id] = row
            self.group.add(row)

        self.storage = wrap_label(QLabel("", self))
        self.storage.setObjectName("tertiary")
        self.group.add(self.storage)
        column.addWidget(self.group)

    # ---------- state ----------

    def refresh(self, override: str = "", quality: QualityTier | None = None) -> None:
        active = self._active_model()
        total = 0
        for model_id, row in self.rows.items():
            installed = self._is_downloaded(model_id)
            on_disk = self._disk_bytes(model_id) if installed else 0
            total += on_disk
            row.set_state(installed, model_id == active, on_disk)
        self._show_anchor(override, quality)
        self.storage.setText(
            f"Speech engines are using {human_bytes(total)}."
            if total
            else "No speech engines are installed yet."
        )

    def _show_anchor(self, override: str, quality: QualityTier | None) -> None:
        tier = quality or QualityTier.BALANCED
        name = tier_label(tier)
        if not override:
            model = spec(TIER_MODELS[tier][0])
            engine = model.display if model else TIER_MODELS[tier][0]
            self.anchor.setText(f"Currently using {name} — {engine}.")
            self.back_button.setVisible(False)
            return
        chosen = spec(override)
        self.anchor.setText(
            f"Currently using {chosen.display if chosen else override}, "
            f"chosen here instead of {name}."
        )
        self.back_button.setText(f"Back to {name}")
        self.back_button.setVisible(True)

    def _is_downloaded(self, model_id: str) -> bool:
        if self._models is None:
            return False
        return bool(self._models.is_downloaded(model_id))

    def _disk_bytes(self, model_id: str) -> int:
        if self._models is None:
            return 0
        return int(self._models.disk_bytes(model_id))

    def refresh_theme(self) -> None:
        for row in self.rows.values():
            row.refresh_theme()
