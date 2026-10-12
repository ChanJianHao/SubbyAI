"""Caption quality as four cards, plus the one-time download behind them.

The picker knows nothing about settings: it is told which tier is chosen and
reports which one the user picked. Downloads belong to a shared coordinator so
Live and Settings observe the same job and repeated clicks cannot start another.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ...asr.lifecycle import ModelDownloads
from ...core.settings import TIER_MODELS, QualityTier
from ..motion_widgets import SmoothProgressBar as QProgressBar
from ..widgets import TIER_LABELS as TIER_TITLES
from ..widgets import size_text as _size_text
from .common import Group, hint_label
from .controls import CardGrid, ChoiceCard

__all__ = ["TIER_TITLES", "QualityPicker"]


def size_text(size_mb: int) -> str:
    return f"{_size_text(size_mb)} download"


class QualityPicker(QWidget):
    """Four tier cards, their availability, and the download for the chosen one."""

    tier_chosen = Signal(str)
    download_completed = Signal()

    def __init__(
        self,
        model_manager: Any = None,
        capability: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._models = model_manager
        self._capability = capability
        self._tier = QualityTier.BALANCED
        self.downloads = None
        self._download_model = ""

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.group = Group("Caption quality", "Higher quality is slower and downloads more.")
        self.cards = CardGrid(columns=2, parent=self)
        for tier in QualityTier:
            _model, size_mb, blurb = TIER_MODELS[tier]
            card = ChoiceCard(TIER_TITLES[tier], blurb, size_text(size_mb))
            self.cards.add_card(tier.value, card)
        self.cards.chosen.connect(self._on_card)
        self.group.add(self.cards)

        self.note = hint_label("")
        self.note.setVisible(False)
        self.group.add(self.note)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.group.add(self.progress)
        self.download_button = QPushButton("Download now", self)
        self.download_button.setVisible(False)
        self.download_button.clicked.connect(self.start_download)
        self.group.add(self.download_button)
        column.addWidget(self.group)

        if model_manager is not None:
            self.set_downloads(ModelDownloads(model_manager, self))

    def set_downloads(self, downloads):
        if self.downloads is not None:
            self.downloads.changed.disconnect(self._download_changed)
            self.downloads.finished.disconnect(self._download_finished)
        self.downloads = downloads
        downloads.changed.connect(self._download_changed)
        downloads.finished.connect(self._download_finished)

    def _download_changed(self, model, progress):
        self._download_model = model
        from ..model_catalogue import installation_text

        self._on_progress(progress.fraction, installation_text(model, progress))
        self.progress.setVisible(progress.phase in ("checking", "downloading", "verifying"))
        if progress.phase in ("checking", "verifying"):
            self.progress.setRange(0, 0)
        self.download_button.setEnabled(progress.phase in ("error", "cancelled", "done"))
        self.note.show()

    def _download_finished(self, model, ok):
        from ..model_catalogue import installation_text

        self.refresh_states()
        self.note.setText(installation_text(model, self.downloads.progress[model]))
        self.note.show()
        self.download_button.setEnabled(True)
        self.progress.hide()
        if ok:
            self.download_completed.emit()

    # ---------- state ----------

    def set_tier(self, tier: QualityTier) -> None:
        """Show a tier as chosen without reporting it back."""
        self._tier = tier
        self.cards.set_value(tier.value)
        self.refresh_states()

    def refresh_states(self) -> None:
        suggested = self._recommended_tier()
        for tier in QualityTier:
            card = self.cards.card(tier.value)
            if card is None:
                continue
            reason = self._unavailable_reason(tier)
            card.set_unavailable(reason)
            # Display the hardware recommendation without changing the user's choice.
            card.set_badge(
                "Recommended for your computer" if tier is suggested and not reason else ""
            )
            if not reason:
                card.set_meta(
                    "Ready to use" if self._is_downloaded(tier) else size_text(TIER_MODELS[tier][1])
                )
        pending = not self._is_downloaded(self._tier) and self._models is not None
        self.download_button.setVisible(pending)
        self.note.setVisible(pending)
        if pending and not (self.downloads and self.downloads.busy):
            self.note.setText(
                f"{TIER_TITLES[self._tier]} downloads once "
                f"({size_text(TIER_MODELS[self._tier][1])}) and then works with no "
                "internet at all."
            )

    def _recommended_tier(self) -> QualityTier | None:
        if self._capability is None:
            return None
        from ...asr.capability import recommended_tier

        return recommended_tier(self._capability)

    def _unavailable_reason(self, tier: QualityTier) -> str:
        """Why this machine cannot run a tier, or "" when it can.

        With no capability probe on hand nothing is blocked: guessing would be
        worse than letting someone try the hardware they paid for.
        """
        if self._capability is None:
            return ""
        from ...asr.capability import tier_available

        available, reason = tier_available(tier, self._capability)
        return "" if available else reason

    def _is_downloaded(self, tier: QualityTier) -> bool:
        if self._models is None:
            return True
        return bool(self._models.is_downloaded(TIER_MODELS[tier][0]))

    def _on_card(self, value: str) -> None:
        self._tier = QualityTier(value)
        self.refresh_states()
        self.tier_chosen.emit(value)

    # ---------- download ----------

    def start_download(self, model: str = "") -> None:
        """Fetch a model. Defaults to the selected tier's; the catalogue names its own."""
        if self.downloads is None:
            return
        model = model or TIER_MODELS[self._tier][0]
        self._download_model = model
        if not self.downloads.start(model):
            self.note.setText("Another model is being installed. Wait or cancel it first.")
            self.note.show()

    def cancel_download(self) -> None:
        if self.downloads:
            self.downloads.cancel()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setRange(0, 100 if fraction >= 0 else 0)
        self.progress.setValue(int(max(0, fraction) * 100))
        if message:
            self.note.setText(message)
            self.note.setVisible(True)
