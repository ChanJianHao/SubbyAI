"""Layout scaffolding shared by the settings surfaces.

Qt has no cascading custom properties, so any widget that colours itself must be
told when the palette flips. Self-painting controls expose ``refresh_theme`` and
``restyle`` walks a subtree calling it — cheaper, and far less leak-prone, than
every widget subscribing to the theme module for the life of the process.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...core.events import PrivacyTier
from ...core.settings import Settings, SettingsStore
from ..tokens import SPACE
from ..widgets import PrivacyBadge

__all__ = [
    "Group",
    "SettingRow",
    "SettingsSection",
    "hairline",
    "hint_label",
    "micro_label",
    "restyle",
    "show_tier",
]

_TIER_PROPERTY = "tier_value"


def show_tier(badge: PrivacyBadge, tier: PrivacyTier | None) -> None:
    """Point a shared privacy badge at a computed tier, hiding it when unknown.

    The tier is remembered on the widget so ``restyle`` can recolour it after a
    theme change without the caller having to recompute anything.
    """
    badge.setVisible(tier is not None)
    badge.setProperty(_TIER_PROPERTY, tier.value if tier is not None else "")
    if tier is not None:
        badge.set_tier(tier.value)


def restyle(root: QWidget) -> None:
    """Re-apply palette-derived colour to every self-painting widget *under* root.

    Root itself is skipped: the widget calling this is normally doing so from
    its own ``refresh_theme``, and including it would recurse forever.
    """
    for widget in root.findChildren(QWidget):
        hook = getattr(widget, "refresh_theme", None)
        if callable(hook):
            hook()
        elif isinstance(widget, PrivacyBadge):
            stored = widget.property(_TIER_PROPERTY)
            if stored:
                widget.set_tier(str(stored))


def hairline() -> QFrame:
    line = QFrame()
    line.setObjectName("hairline")
    line.setFixedHeight(1)
    return line


def micro_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("micro")
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("secondary")
    label.setWordWrap(True)
    return label


class Group(QWidget):
    """One flat settings group: uppercase label, hairline, then rows.

    No nested cards anywhere — the airiness comes from rhythm and a single
    rule, not from boxes inside boxes.
    """

    def __init__(self, title: str, note: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, SPACE["xl"])
        column.setSpacing(SPACE["sm"])
        if title:
            column.addWidget(micro_label(title))
        column.addWidget(hairline())
        if note:
            column.addWidget(hint_label(note))
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, SPACE["xs"], 0, 0)
        self._body.setSpacing(SPACE["md"])
        column.addLayout(self._body)

    def add(self, widget: QWidget) -> QWidget:
        self._body.addWidget(widget)
        return widget

    def add_row(self, label: str, control: QWidget, hint: str = "") -> QWidget:
        """A label and its control, side by side when there is room."""
        return self.add(SettingRow(label, control, hint, self))


class SettingRow(QWidget):
    """One setting: label beside its control, stacked when the pane is narrow.

    A fixed side-by-side row is only comfortable while the window is wide. Below
    that it either squeezes the control to uselessness or refuses to shrink and
    drags a horizontal scrollbar across the settings pane — which is how these
    controls became unreachable in the first place. Stacking is the ordinary
    responsive answer and costs nothing when the window is wide.
    """

    #: Below this the label and control stop sharing a line.
    STACK_BELOW = 380

    def __init__(
        self, label: str, control: QWidget, hint: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.control = control
        self._stacked: bool | None = None

        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(SPACE["xs"])

        self._line = QHBoxLayout()
        self._line.setSpacing(SPACE["md"])
        self._label = QLabel(label, self)
        self._label.setWordWrap(True)
        self._column.addLayout(self._line)
        if hint:
            self._column.addWidget(hint_label(hint))
        self._apply(stacked=False)

    def _apply(self, stacked: bool) -> None:
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self._line.removeWidget(self._label)
        self._line.removeWidget(self.control)
        self._line.setDirection(
            QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        )
        if stacked:
            self._line.addWidget(self._label)
            self._line.addWidget(self.control)
        else:
            self._line.addWidget(self._label, 1)
            self._line.addWidget(self.control, 0, Qt.AlignmentFlag.AlignRight)
        self._label.setVisible(True)
        self.control.setVisible(True)

    def resizeEvent(self, event) -> None:  # Qt API casing
        self._apply(stacked=event.size().width() < self.STACK_BELOW)
        super().resizeEvent(event)

    def minimumSizeHint(self):  # Qt API casing
        """Report the stacked minimum: this row can always reach that width."""
        from PySide6.QtCore import QSize

        hint = super().minimumSizeHint()
        widest = max(
            self._label.minimumSizeHint().width(), self.control.minimumSizeHint().width()
        )
        return QSize(min(hint.width(), widest), hint.height())


class SettingsSection(QWidget):
    """Base for a settings page.

    Owns the one rule every control obeys: write the value, then announce the
    section — except while we are the ones filling the controls in, which would
    otherwise save and broadcast a change nobody made.
    """

    def __init__(self, store: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._muted = False

    @property
    def store(self) -> SettingsStore:
        return self._store

    @property
    def settings(self) -> Settings:
        return self._store.settings

    def apply(self, section: str) -> None:
        if not self._muted:
            self._store.notify(section)

    @contextmanager
    def quiet(self) -> Iterator[None]:
        previous = self._muted
        self._muted = True
        try:
            yield
        finally:
            self._muted = previous

    def refresh(self) -> None:
        """Re-read the store into the controls."""
