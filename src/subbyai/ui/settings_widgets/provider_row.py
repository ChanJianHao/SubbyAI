"""One row of the translation provider chain.

The row reports intent and never acts: it says "the user asked to use me" and
the section decides whether that needs consent first. That split is what makes
the cloud consent line impossible to skip.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...core.events import PrivacyTier
from ...core.settings import ProviderSettings
from ...translation import BUILTIN_PROVIDER_ID
from ..motion import confirm_action, reveal
from ..motion_widgets import ActivityIndicator
from ..motion_widgets import MotionToggle as QCheckBox
from ..tokens import SPACE
from ..widgets import PrivacyBadge, elide_label
from .common import hint_label, show_tier
from .controls import StatusDot

__all__ = ["ProviderRow"]

_MOVES = (("↑", -1, "Try this one earlier"), ("↓", 1, "Try this one later"))


class ProviderRow(QWidget):
    """Name, status, computed privacy badge, and the actions for one provider."""

    use_toggled = Signal(str, bool)
    test_requested = Signal(str)
    edit_requested = Signal(str)
    remove_requested = Signal(str)
    move_requested = Signal(str, int)
    consent_given = Signal(str)

    def __init__(
        self,
        config: ProviderSettings,
        tier: PrivacyTier,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider_id = config.id
        column = QVBoxLayout(self)
        column.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        column.setSpacing(SPACE["xs"])

        line = QHBoxLayout()
        line.setSpacing(SPACE["sm"])
        self.dot = StatusDot("ok" if config.enabled else "idle", self)
        self.badge = PrivacyBadge(parent=self)
        show_tier(self.badge, tier)
        self.use = QCheckBox("Use", self)
        self.use.setChecked(config.enabled)
        self.use.toggled.connect(lambda on: self.use_toggled.emit(self.provider_id, on))
        self.test_button = QPushButton("Test", self)
        self.test_button.setObjectName("quiet")
        self.test_button.clicked.connect(lambda: self.test_requested.emit(self.provider_id))
        self.activity = ActivityIndicator(self, size=16)
        line.addWidget(self.dot)
        # The name is the only elastic thing in this row: badge, toggle and
        # buttons all need their full width to stay usable, so the name gives
        # way first rather than the row forcing the window wider.
        self.name_label = elide_label(QLabel(config.label or config.id, self), minimum=90)
        line.addWidget(self.name_label, 1)
        line.addWidget(self.badge)
        line.addWidget(self.use)
        line.addWidget(self.test_button)
        line.addWidget(self.activity)
        # Keyboard reordering, because dragging cannot be the only way to set an order.
        for glyph, step, tip in _MOVES:
            button = QPushButton(glyph, self)
            button.setObjectName("quiet")
            button.setFixedWidth(28)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _c=False, by=step: self.move_requested.emit(self.provider_id, by)
            )
            line.addWidget(button)
        if config.id != BUILTIN_PROVIDER_ID:
            for label, signal in (("Edit", self.edit_requested), ("Remove", self.remove_requested)):
                button = QPushButton(label, self)
                button.setObjectName("quiet")
                button.clicked.connect(lambda _c=False, s=signal: s.emit(self.provider_id))
                line.addWidget(button)
        column.addLayout(line)

        self.note = hint_label("")
        self.note.setVisible(False)
        column.addWidget(self.note)
        self.consent_button = QPushButton("Use it anyway", self)
        self.consent_button.setObjectName("quiet")
        self.consent_button.setVisible(False)
        self.consent_button.clicked.connect(lambda: self.consent_given.emit(self.provider_id))
        column.addWidget(self.consent_button)

    def show_note(self, text: str, state: str = "idle") -> None:
        self.note.setText(text)
        reveal(self.note, bool(text))
        self.dot.set_state(state)
        self.activity.set_busy(state == "busy")
        self.test_button.setEnabled(state != "busy")
        self.test_button.setText("Checking…" if state == "busy" else "Test")
        if state == "ok":
            confirm_action(self.test_button, "Connected ✓")

    def ask_consent(self, text: str) -> None:
        self.show_note(text)
        reveal(self.consent_button, True)

    def set_use(self, on: bool) -> None:
        """Set the tick without reporting it, for reverting a refused change."""
        self.use.blockSignals(True)
        self.use.setChecked(on)
        self.use.blockSignals(False)
        self.consent_button.setVisible(False)
