"""Small shared widgets and the display names every surface shares.

These exist so the same idea always looks the same — a level meter in the Live
view, the settings device picker and the onboarding audio check are one widget,
not three drifting copies. The same goes for the words: a caption style is
called "High contrast" in the tray, in settings and on the Live chip because
all three read the one table below.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import OverlayPreset, QualityTier
from . import theme, tokens
from .motion import Tween, interactive, reveal
from .tokens import RADIUS, SPACE

#: Loud enough to count as "we can hear something", quiet enough that room tone
#: through a loopback device does not trip it.
SOUND_LEVEL = 0.02

#: Caption style names, sentence case. The single source for tray, settings,
#: onboarding and the Live chip.
#: Display names. Deliberately not the persisted enum values: "glass" reads as
#: "Clean" and "solid" as "Gaming" because those describe what someone is
#: choosing, and wording may be reconsidered without migrating settings files.
PRESET_LABELS: dict[OverlayPreset, str] = {
    OverlayPreset.GLASS: "Clean",
    OverlayPreset.CINEMA: "Cinema",
    OverlayPreset.ANIME: "Anime",
    OverlayPreset.CUTE: "Cute",
    OverlayPreset.MINIMAL: "Minimal",
    OverlayPreset.SOLID: "Gaming",
    OverlayPreset.LARGE_TEXT: "Large text",
    OverlayPreset.HIGH_CONTRAST: "Accessibility",
    OverlayPreset.CUSTOM: "Custom",
}

#: Caption quality names. Never a model name — the engine's identity is an
#: implementation detail the user did not ask about.
TIER_LABELS: dict[QualityTier, str] = {
    QualityTier.QUICK: "Quick",
    QualityTier.BALANCED: "Balanced",
    QualityTier.DETAILED: "Detailed",
    QualityTier.MAXIMUM: "Maximum",
}


def preset_label(preset: OverlayPreset) -> str:
    return PRESET_LABELS.get(preset, PRESET_LABELS[OverlayPreset.CUSTOM])


def tier_label(tier: QualityTier) -> str:
    return TIER_LABELS.get(tier, TIER_LABELS[QualityTier.BALANCED])


def size_text(megabytes: int) -> str:
    """A download size a person can picture. Callers add their own noun."""
    if megabytes < 1024:
        return f"{megabytes} MB"
    return f"{megabytes / 1024:.1f} GB"


class LevelMeter(QWidget):
    """Five bars that move when the computer is making sound.

    This is a diagnostic first and decoration second: a user who cannot hear
    needs to distinguish "nothing is playing" from "captions are broken", and
    this is the widget that tells them.
    """

    BARS = 5
    #: Level at which every bar is lit.
    FULL_SCALE = 0.3

    def __init__(self, parent: QWidget | None = None, *, height: int = 14):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMinimumWidth(48)
        self._raw = 0.0
        self._level = 0.0
        self._speech = False
        self._motion = Tween(self, self._display_level)

    @property
    def level(self) -> float:
        """The last level supplied, before display scaling."""
        return self._raw

    def set_level(self, level: float, speech: bool = False) -> None:
        self._raw = max(0.0, float(level))
        scaled = max(0.0, min(1.0, self._raw / self.FULL_SCALE))
        if abs(scaled - self._level) < 0.02 and speech == self._speech:
            return
        self._speech = speech
        self._motion.to(scaled, "fast")

    def _display_level(self, level: float) -> None:
        self._level = level
        self.update()

    def paintEvent(self, event) -> None:  # Qt API casing
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current()
        active = QColor(palette.accent if self._speech else palette.text_secondary)
        idle = QColor(palette.text_tertiary)
        idle.setAlphaF(0.35)

        bar_width = 3
        gap = 3
        total = self.BARS * bar_width + (self.BARS - 1) * gap
        x = (self.width() - total) / 2
        lit = round(self._level * self.BARS)
        for index in range(self.BARS):
            # Middle bars are tallest, so the meter reads as a level, not a bar chart.
            scale = 1.0 - abs(index - (self.BARS - 1) / 2) / self.BARS
            height = max(3.0, self.height() * scale * (0.25 + 0.75 * self._level))
            y = (self.height() - height) / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(active if index < lit else idle)
            painter.drawRoundedRect(x, y, bar_width, height, 1.5, 1.5)
            x += bar_width + gap


class StatusBanner(QFrame):
    """Inline, dismissible, never modal.

    Pipeline problems appear with an actionable recovery control.
    """

    action_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("banner")
        self._severity = "info"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])

        self._text = QLabel()
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        self._text.setWordWrap(True)
        layout.addWidget(self._text, stretch=1)

        self._action = QPushButton()
        self._action.setObjectName("quiet")
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.clicked.connect(self._on_action)
        layout.addWidget(self._action)

        self._dismiss = QPushButton("✕")
        self._dismiss.setAccessibleName("Dismiss notification")
        self._dismiss.setObjectName("quiet")
        self._dismiss.setFixedWidth(28)
        self._dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss.clicked.connect(lambda: reveal(self, False))
        layout.addWidget(self._dismiss)

        self._action_key = ""
        self.hide()

    def show_message(
        self, text: str, severity: str = "info", action: str = "", action_key: str = ""
    ) -> None:
        self._severity = severity
        self._text.setText(text)
        self._action_key = action_key
        self._action.setText(action)
        self._action.setVisible(bool(action))
        self._restyle()
        reveal(self, True)

    def _restyle(self) -> None:
        palette = theme.current()
        colour = {
            "info": palette.accent,
            "warning": palette.warning,
            "error": palette.error,
        }.get(self._severity, palette.accent)
        r, g, b = tokens.hex_to_rgb(colour)
        self.setStyleSheet(
            f"QFrame#banner {{"
            f" background: rgba({r}, {g}, {b}, 0.12);"
            f" border: 1px solid rgba({r}, {g}, {b}, 0.35);"
            f" border-radius: {RADIUS['md']}px; }}"
        )

    def refresh_theme(self) -> None:
        self._restyle()

    def _on_action(self) -> None:
        self.action_clicked.emit(self._action_key)
        reveal(self, False)


class PrivacyBadge(QLabel):
    """Says where a feature runs. Computed from the endpoint, never declared."""

    def __init__(self, tier_value: str = "on_device", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("micro")
        self.set_tier(tier_value)

    def set_tier(self, tier_value: str, label: str | None = None) -> None:
        style = tokens.privacy_badge(tier_value, theme.current())
        text = label or {
            "on_device": "On this device",
            "local_network": "Local network",
            "cloud": "Cloud",
        }.get(tier_value, tier_value)
        self.setText(text)
        self.setStyleSheet(
            f"color: {style.text}; background: {style.background};"
            f" border-radius: {RADIUS['sm']}px; padding: 2px 8px;"
        )


class LanguagePicker(QComboBox):
    """A searchable list of language names. Codes never reach the screen.

    Entries come from the caller because each surface offers a different first
    row — "Detect automatically" when choosing what you will hear, "Just show
    what I hear" when choosing what to read.
    """

    code_changed = Signal(str)

    def __init__(self, entries: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Wide enough to read a language name, narrow enough that two of these
        # in one settings pane do not set the window's minimum width.
        self.setMinimumWidth(160)
        compact_combo(self, chars=14)
        for code, name in entries:
            self.addItem(name, code)
        completer = QCompleter([name for _, name in entries], self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)
        self.lineEdit().setPlaceholderText("Type to search")
        self.currentIndexChanged.connect(self._announce)
        self.lineEdit().editingFinished.connect(self._settle)

    def current_code(self) -> str:
        return str(self.currentData() or "")

    def set_code(self, code: str) -> None:
        self.setCurrentIndex(max(self.findData(code), 0))

    def choose(self, code: str) -> None:
        """Select as though the user had picked it."""
        self.set_code(code)

    def _announce(self, index: int) -> None:
        self.code_changed.emit(str(self.itemData(index) or ""))

    def _settle(self) -> None:
        """Half-typed text must never leave the field claiming another language."""
        index = self.findText(self.lineEdit().text(), Qt.MatchFlag.MatchFixedString)
        if index < 0:
            self.setEditText(self.itemText(self.currentIndex()))
        elif index != self.currentIndex():
            self.setCurrentIndex(index)


class Card(QFrame):
    """A selectable card. Used for quality tiers, styles and intents."""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        badge: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = False
        self._enabled_reason = ""
        self._pressed = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        interactive(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(SPACE["xs"])

        header = QHBoxLayout()
        self._title = QLabel(title)
        self._title.setObjectName("heading")
        header.addWidget(self._title)
        header.addStretch(1)
        self._badge = QLabel(badge)
        self._badge.setObjectName("micro")
        self._badge.setVisible(bool(badge))
        header.addWidget(self._badge)
        layout.addLayout(header)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("secondary")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._subtitle)

    @property
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        palette = theme.current()
        border = palette.accent if selected else palette.hairline
        background = palette.accent_subtle if selected else palette.surface
        self.setStyleSheet(
            f"QFrame#card {{ background: {background}; border: 1px solid {border};"
            f" border-radius: {RADIUS['lg']}px; }}"
        )

    def set_unavailable(self, reason: str) -> None:
        """Disabled cards stay visible and say why — a dead control is worse."""
        self._enabled_reason = reason
        self.setEnabled(False)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if reason:
            self._subtitle.setText(reason)
            self._subtitle.setVisible(True)

    def mousePressEvent(self, event) -> None:  # Qt API casing
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (self._pressed and self.isEnabled() and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        self._pressed = False
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        keys = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if self.isEnabled() and event.key() in keys and not event.isAutoRepeat():
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text.upper(), parent)
        self.setObjectName("micro")


class Toast(QFrame):
    """An inline, self-dismissing message with at most one action.

    Inline rather than modal on purpose: nothing in this app may take the
    keyboard away from whatever the user is actually watching.
    """

    action_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        row.setSpacing(SPACE["sm"])
        self._label = QLabel("", self)
        self._label.setWordWrap(True)
        self._action = QPushButton("", self)
        self._action.setObjectName("quiet")
        self._action.clicked.connect(self._fire)
        row.addWidget(self._label, 1)
        row.addWidget(self._action, 0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.setVisible(False)
        self.refresh_theme()

    @property
    def message(self) -> str:
        return self._label.text()

    def show_message(self, text: str, action: str = "", seconds: float = 8.0) -> None:
        self._timer.stop()
        self._label.setText(text)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._action.setText(action)
        self._action.setVisible(bool(action))
        reveal(self, True)
        if seconds > 0:
            self._timer.start(int(seconds * 1000))

    def dismiss(self) -> None:
        self._timer.stop()
        reveal(self, False)

    def refresh_theme(self) -> None:
        palette = theme.current()
        self.setStyleSheet(
            f"QFrame#toast {{ background: {palette.raised};"
            f" border: 1px solid {palette.hairline};"
            f" border-radius: {RADIUS['md']}px; }}"
        )

    def _fire(self) -> None:
        self.dismiss()
        self.action_clicked.emit()


def rounded_clip(widget: QWidget, radius: int) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(widget.rect(), radius, radius)
    return path


def build_row(label: str, control: QWidget, hint: str = "") -> QWidget:
    """One settings row: label left, control right, optional hint underneath."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, SPACE["xs"], 0, SPACE["xs"])
    layout.setSpacing(2)

    row = QHBoxLayout()
    row.setSpacing(SPACE["lg"])
    text = QLabel(label)
    row.addWidget(text, stretch=1)
    row.addWidget(control)
    layout.addLayout(row)

    if hint:
        note = QLabel(hint)
        note.setObjectName("tertiary")
        note.setWordWrap(True)
        layout.addWidget(note)
    return container


def connect_theme(widget: QWidget, restyle: Callable[[], None]) -> None:
    """Re-run a widget's manual styling whenever the palette changes."""
    theme.subscribe(lambda _palette: restyle())
    restyle()


# ---------- layout hygiene ----------
#
# A control that refuses to shrink sets a floor under the whole window. The app
# shipped with several: a combo box sized to "Custom (OpenAI-compatible)", a
# button carrying a full sentence, labels that would not wrap. Together they
# made the window open with controls compressed or out of reach until the user
# resized it by hand. These helpers exist so every such control shrinks
# honestly, and tests/test_layout.py fails if a new one appears.

#: Roughly the width of a typical setting value; combos elide past this.
COMBO_CHARS = 16


def compact_combo(combo: QComboBox, chars: int = COMBO_CHARS) -> QComboBox:
    """Stop a combo box from sizing itself to its longest item.

    Qt's default (AdjustToContentsOnFirstShow) lets one long entry dictate the
    window's minimum width. The list still shows full text when opened.
    """
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
    combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return combo


def elide_label(label: QLabel, minimum: int = 120) -> QLabel:
    """Let a single-line label shrink, showing an ellipsis instead of forcing width."""
    label.setMinimumWidth(minimum)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def wrap_label(label: QLabel) -> QLabel:
    """Body text wraps. Anything that reads as a sentence should use this."""
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


def fit_to_screen(window: QWidget, width: int, height: int) -> None:
    """Open a window at its intended size, honouring content and the screen.

    Three rules, in order: never smaller than the content needs (otherwise
    controls are clipped at launch), never bigger than the screen it opens on
    (1366x768 laptops are still common), and prefer the intended size between
    those bounds.
    """
    from PySide6.QtGui import QGuiApplication

    needed = window.minimumSizeHint()
    width = max(width, needed.width())
    height = max(height, needed.height())

    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, int(available.width() * 0.95))
        height = min(height, int(available.height() * 0.95))
    window.resize(width, height)


class FlowLayout(QLayout):
    """A horizontal layout that wraps onto the next line when it runs out of room.

    Variable-width controls retain their usable sizes while wrapping to fit
    narrow windows. The layout supports a variable number of equally important
    controls, including the Live view's toggle chips.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = SPACE["sm"]):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # Qt API casing
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # Qt API casing
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # Qt API casing
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # Qt API casing
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # Qt API casing
        return True

    def heightForWidth(self, width: int) -> int:  # Qt API casing
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # Qt API casing
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # Qt API casing
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # Qt API casing
        # The widest single item, not the sum: that is what lets the window shrink.
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        right = rect.right() - margins.right()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if next_x > right and line_height > 0:
                x = rect.x() + margins.left()
                y += line_height + self._spacing
                next_x = x + hint.width()
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + self._spacing
            line_height = max(line_height, hint.height())

        return y + line_height + margins.bottom() - rect.y()
