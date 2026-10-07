"""Caption quality as four cards, plus the one-time download behind them.

The picker knows nothing about settings: it is told which tier is chosen and
reports which one the user picked. It does own the download, because that is a
long job with progress that belongs next to the card that started it — and the
engine's real model name never reaches a label here, only the tier's name.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ...core.settings import TIER_MODELS, QualityTier
from ..motion import confirm_action, reveal
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

    _progressed = Signal(float, str)
    _finished = Signal(bool, str)

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
        self._cancel = threading.Event()

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

        self._progressed.connect(self._on_progress)
        self._finished.connect(self._on_finished)

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
        if pending:
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
        if self._models is None:
            return
        model = model or TIER_MODELS[self._tier][0]
        self._cancel = threading.Event()
        self.download_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.reset()
        self.progress.setTextVisible(False)
        reveal(self.progress, True)
        models = self._models

        def run() -> None:
            def report(progress) -> None:
                self._progressed.emit(progress.fraction, progress.message)

            try:
                ok = models.download(model, report, self._cancel)
            except Exception as exc:
                self._finished.emit(False, str(exc))
                return
            self._finished.emit(bool(ok), "")

        threading.Thread(target=run, name="subbyai-model-download", daemon=True).start()

    def cancel_download(self) -> None:
        self._cancel.set()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setRange(0, 100 if fraction >= 0 else 0)
        self.progress.setValue(int(max(0, fraction) * 100))
        if message:
            self.note.setText(message)
            self.note.setVisible(True)

    def _on_finished(self, ok: bool, message: str) -> None:
        self.download_button.setEnabled(True)
        reveal(self.progress, False)
        if not ok:
            self.note.setText(message or "The download didn't finish. Try again in a moment.")
            self.note.setVisible(True)
            return
        self.refresh_states()
        confirm_action(self.download_button, "Ready ✓")
        self.download_completed.emit()
