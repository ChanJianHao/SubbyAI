"""The caption style editor.

Nothing here paints the overlay. The section writes the setting, announces the
``overlay`` section and emits ``style_changed``; the shell owns the one real
overlay window and repaints it. A settings page that reached into the overlay
would be a second author of the same pixels.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import OriginalPosition, OverlayPreset, SettingsStore
from . import theme
from .motion import Expandable
from .motion_widgets import MotionToggle as QCheckBox
from .saved_themes import SavedThemes
from .settings_widgets import CardGrid, ChoiceCard, Group, SettingsSection
from .tokens import CAPTION_FONT_CHOICES, OVERLAY_PRESETS, RADIUS, SPACE, parse_rgba, rgba_css
from .widgets import compact_combo, preset_label

__all__ = ["CaptionsSection"]

#: Preset -> the one-line reason to pick it. The names themselves come from
#: ``widgets.PRESET_LABELS`` so the tray and this page can never disagree.
PRESET_BLURBS: dict[OverlayPreset, str] = {
    OverlayPreset.GLASS: "A translucent panel that stays readable over anything.",
    OverlayPreset.ANIME: "Lavender subtitles with a firm outline and no panel.",
    OverlayPreset.CUTE: "Soft sakura text on a rounded plum panel.",
    OverlayPreset.CINEMA: (
        "White text with a soft shadow, the way films do it. Can be hard to "
        "read over bright scenes."
    ),
    OverlayPreset.MINIMAL: "Just text, outlined so it survives any picture.",
    OverlayPreset.SOLID: (
        "One caption at a time on an opaque panel, and clicks pass straight through to the game."
    ),
    OverlayPreset.LARGE_TEXT: "Bigger text on the usual panel, for reading from further back.",
    OverlayPreset.HIGH_CONTRAST: (
        "Big, bold and opaque, with no fading and no italics. Built for reading, not for looks."
    ),
    OverlayPreset.CUSTOM: "Your own colours, edges and sizes.",
}

#: Behaviours a preset seeds when you pick it. Style alone cannot express "good
#: for gaming" — that is also about how many captions stay up and whether clicks
#: pass through. Seeded on selection only, never re-applied, so a change made
#: afterwards is not quietly undone.
PRESET_BEHAVIOUR: dict[OverlayPreset, dict[str, object]] = {
    OverlayPreset.SOLID: {"max_pairs": 1, "click_through": True},
    OverlayPreset.LARGE_TEXT: {"max_pairs": 1, "click_through": False},
    OverlayPreset.HIGH_CONTRAST: {"max_pairs": 2, "click_through": False, "auto_hide": False},
}


def seed_from_preset(overlay, preset: OverlayPreset) -> None:
    """Copy a preset's own typography into the settings the sliders show.

    Size and weight are settings, not style, because the sliders must show the
    truth — so a preset whose whole point is 40px text has to write 40 rather
    than lose to whatever the slider happened to hold. Custom is exempt: it
    exists precisely to keep what the user chose.
    """
    if preset is OverlayPreset.CUSTOM:
        return
    base = OVERLAY_PRESETS[preset]
    overlay.font_size = base.font_size
    overlay.font_weight = base.font_weight
    for field, seeded in PRESET_BEHAVIOUR.get(preset, {}).items():
        setattr(overlay, field, seeded)


PRESET_CARDS: tuple[tuple[OverlayPreset, str, str], ...] = tuple(
    (preset, preset_label(preset), blurb) for preset, blurb in PRESET_BLURBS.items()
)

_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("Regular", 400),
    ("Medium", 500),
    ("Semibold", 600),
    ("Bold", 700),
)

_ALIGNMENTS: tuple[tuple[str, str], ...] = (
    ("Match the style", ""),
    ("Left", "left"),
    ("Centred", "center"),
    ("Right", "right"),
)

_PADDINGS: tuple[tuple[str, str], ...] = (
    ("Match the style", ""),
    ("Tight", "tight"),
    ("Normal", "normal"),
    ("Roomy", "roomy"),
)


def _preset_spacing(overlay) -> float:
    """The line spacing in force when the user has not overridden it."""
    preset = overlay.preset
    base = OVERLAY_PRESETS[OverlayPreset.GLASS if preset is OverlayPreset.CUSTOM else preset]
    return base.line_spacing


def installed_caption_fonts() -> list[str]:
    """The offerable subset of CAPTION_FONT_CHOICES, in preference order.

    Qt will happily hand back a QFont for a family that is not installed, and
    ``QFont.family()`` echoes the name you asked for, so a picker built from
    the list alone would offer fonts that silently render as something else.
    ``hasFamily`` is the only honest test.
    """
    preferred = [name for name in CAPTION_FONT_CHOICES if QFontDatabase.hasFamily(name)]
    return preferred + sorted(set(QFontDatabase.families()) - set(preferred), key=str.casefold)


def weights_for(family: str) -> tuple[tuple[str, int], ...]:
    """Weights this family really has. Verdana and Tahoma ship only two."""
    if not family or not QFontDatabase.hasFamily(family):
        return _WEIGHTS

    weights = {QFontDatabase.weight(family, style) for style in QFontDatabase.styles(family)}
    available = tuple((label, value) for label, value in _WEIGHTS if value in weights)
    return available or (("Regular", 400), ("Bold", 700))


_POSITIONS: tuple[tuple[str, OriginalPosition], ...] = (
    ("Above the translation", OriginalPosition.ABOVE),
    ("Below the translation", OriginalPosition.BELOW),
    ("Don't show it", OriginalPosition.HIDDEN),
)


class _SliderRow(QWidget):
    """A slider that says what it is currently set to."""

    def __init__(
        self,
        low: int,
        high: int,
        caption: Callable[[int], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["sm"])
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.setFocusProxy(self.slider)
        self.slider.setRange(low, high)
        self.slider.setMinimumWidth(100)
        self._caption = caption
        self._value = QLabel(self)
        self._value.setObjectName("tertiary")
        self._value.setMinimumWidth(64)
        row.addWidget(self.slider, 1)
        row.addWidget(self._value)
        self.slider.valueChanged.connect(self._show)

    def _show(self, value: int) -> None:
        self._value.setText(self._caption(value))


class _ColourButton(QPushButton):
    """A swatch that opens the system colour picker."""

    colour_picked = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, alpha: bool = False) -> None:
        super().__init__(parent)
        self._alpha = alpha
        self.setFixedSize(64, 28)
        self._hex = "#FFFFFF"
        self.clicked.connect(self._pick)
        self.refresh_theme()

    def hex_colour(self) -> str:
        return self._hex

    def set_hex(self, value: str) -> None:
        self._hex = value if value.startswith("#") else f"#{value}"
        self.setToolTip("Choose colour · " + self._hex)
        self.setAccessibleDescription("Current colour " + self._hex)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        palette = theme.current()
        self.setStyleSheet(
            f"QPushButton {{ background: {rgba_css(parse_rgba(self._hex, (255, 255, 255, 255)))};"
            f" border: 1px solid {palette.stroke};"
            f" border-radius: {RADIUS['sm']}px; }}"
        )

    def _pick(self) -> None:
        options = (
            QColorDialog.ColorDialogOption.ShowAlphaChannel
            if self._alpha else QColorDialog.ColorDialogOption(0)
        )
        chosen = QColorDialog.getColor(
            QColor(*parse_rgba(self._hex, (255, 255, 255, 255))),
            self, "Pick a colour", options,
        )
        if chosen.isValid():
            value = chosen.name().upper()
            if self._alpha:
                value += f"{chosen.alpha():02X}"
            self.set_hex(value)
            self.colour_picked.emit(self._hex)


class CaptionsSection(SettingsSection):
    """Preset cards, the custom editor, and how captions come and go."""

    style_changed = Signal()

    def __init__(self, store: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(store, parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.saved = SavedThemes(store, self)
        self.saved.style_changed.connect(self._saved_applied)
        column.addWidget(self.saved)
        column.addWidget(self._build_presets())
        self.custom_reveal = Expandable(self._build_custom())
        column.addWidget(self.custom_reveal)
        column.addWidget(self._build_text())
        column.addWidget(self._build_behaviour())
        self.refresh()

    # ---------- building ----------

    def _build_presets(self) -> Group:
        group = Group("Style", "Captions on screen change as you pick.")
        self.preset_cards = CardGrid(columns=3, parent=self)
        for preset, title, blurb in PRESET_CARDS:
            self.preset_cards.add_card(preset.value, ChoiceCard(title, blurb))
        self.preset_cards.chosen.connect(self._on_preset)
        group.add(self.preset_cards)
        customize = QPushButton("Make this look my own", self)
        customize.clicked.connect(self._customize)
        group.add(customize)
        return group

    def _build_custom(self) -> Group:
        group = Group("Custom look")
        self.background_button = _ColourButton(self)
        self.background_button.colour_picked.connect(self._on_background_colour)
        group.add_row("Background colour", self.background_button)

        self.opacity = _SliderRow(0, 100, lambda v: f"{v}%", self)
        self.opacity.slider.valueChanged.connect(self._on_opacity)
        group.add_row("How solid the background is", self.opacity)

        self.text_colour_button = _ColourButton(self)
        self.text_colour_button.colour_picked.connect(self._on_text_colour)
        group.add_row("Original text colour", self.text_colour_button)
        self.translation_colour = _ColourButton(self)
        self.translation_colour.colour_picked.connect(
            lambda value: self._custom_value("custom_translation_color", value)
        )
        group.add_row("Translated text colour", self.translation_colour)
        self.border_colour = _ColourButton(self, alpha=True)
        self.border_colour.colour_picked.connect(
            lambda value: self._custom_value("custom_border_color", value)
        )
        group.add_row("Border colour", self.border_colour)
        self.border_width = _SliderRow(0, 50, lambda value: f"{value / 10:.1f} px", self)
        self.border_width.slider.valueChanged.connect(
            lambda value: self._custom_value("custom_border_width", value / 10)
        )
        group.add_row("Border width (0 = off)", self.border_width)

        self.outline = QCheckBox(self)
        self.outline.toggled.connect(self._on_outline)
        group.add_row("Outline the letters", self.outline)

        self.shadow = QCheckBox(self)
        self.shadow.toggled.connect(self._on_shadow)
        group.add_row("Drop a shadow behind the text", self.shadow)

        self.radius = _SliderRow(0, 24, lambda v: f"{v} px", self)
        self.radius.slider.valueChanged.connect(self._on_radius)
        group.add_row("Rounded corners", self.radius)

        self.custom_group = group
        return group

    def _build_text(self) -> Group:
        group = Group("Text")
        self.font = compact_combo(QComboBox(self))
        self.font.addItem("Match the app", "")
        for family in installed_caption_fonts():
            self.font.addItem(family, family)
        self.font.currentIndexChanged.connect(self._on_font)
        group.add_row("Font", self.font)

        self.font_size = _SliderRow(10, 72, lambda v: f"{v} px", self)
        self.font_size.slider.valueChanged.connect(self._on_font_size)
        group.add_row("Text size", self.font_size)

        self.weight = compact_combo(QComboBox(self))
        self.weight.currentIndexChanged.connect(self._on_weight)
        group.add_row("Thickness", self.weight)

        self.line_spacing = _SliderRow(100, 250, lambda v: f"{v / 100:.2f}x", self)
        self.line_spacing.slider.valueChanged.connect(self._on_line_spacing)
        group.add_row("Space between lines", self.line_spacing)

        self.align = compact_combo(QComboBox(self))
        for label, value in _ALIGNMENTS:
            self.align.addItem(label, value)
        self.align.currentIndexChanged.connect(self._on_align)
        group.add_row("Line position", self.align)

        self.padding = compact_combo(QComboBox(self))
        for label, value in _PADDINGS:
            self.padding.addItem(label, value)
        self.padding.currentIndexChanged.connect(self._on_padding)
        group.add_row("Space around the text", self.padding)

        self.max_pairs = compact_combo(QComboBox(self))
        for count in (1, 2, 3, 4):
            self.max_pairs.addItem("1 caption" if count == 1 else f"{count} captions", count)
        self.max_pairs.currentIndexChanged.connect(self._on_max_pairs)
        group.add_row("How much stays on screen", self.max_pairs)

        self.dim_original = QCheckBox(self)
        self.dim_original.toggled.connect(self._on_dim_original)
        group.add_row(
            "Fade the original line",
            self.dim_original,
            "Keeps your eye on the translation without hiding what was said.",
        )
        return group

    def _build_behaviour(self) -> Group:
        group = Group("On screen")
        self.topmost = QCheckBox("Keep subtitles above other windows", self)
        self.topmost.toggled.connect(self._on_topmost)
        group.add(self.topmost)
        from PySide6.QtGui import QGuiApplication

        self.monitor = compact_combo(QComboBox(self))
        self.monitor.addItem("Current display", "")
        for index, screen in enumerate(QGuiApplication.screens(), 1):
            self.monitor.addItem(f"Display {index} · {screen.name()}", screen.name())
        self.monitor.activated.connect(self._on_monitor)
        group.add_row(
            "Subtitle display",
            self.monitor,
            "Drag the overlay between displays to save a separate position for each.",
        )
        self.line_limit = compact_combo(QComboBox(self))
        for count in range(1, 9):
            self.line_limit.addItem(str(count), count)
        self.line_limit.activated.connect(self._on_line_limit)
        group.add_row(
            "Maximum lines per language",
            self.line_limit,
            "Long phrases end with an ellipsis. Saved transcripts keep the complete text.",
        )
        self.stroke_width = _SliderRow(5, 50, lambda value: f"{value / 10:.1f} px", self)
        self.stroke_width.slider.valueChanged.connect(self._on_stroke_width)
        group.add_row("Text outline width", self.stroke_width)
        self.original_position = compact_combo(QComboBox(self))
        for label, value in _POSITIONS:
            self.original_position.addItem(label, value.value)
        self.original_position.currentIndexChanged.connect(self._on_position)
        group.add_row("Where the original language goes", self.original_position)

        self.animate = QCheckBox(self)
        self.animate.toggled.connect(self._on_animate)
        group.add_row(
            "Fade captions in and out",
            self.animate,
            "Turn this off if movement is distracting or makes you feel unwell.",
        )

        self.auto_hide_seconds = _SliderRow(2, 15, lambda v: f"{v} seconds", self)
        self.auto_hide_seconds.slider.valueChanged.connect(self._on_auto_hide_seconds)
        group.add_row("Hide captions after", self.auto_hide_seconds)

        self.never_hide = QCheckBox("Keep them on screen", self)
        self.never_hide.toggled.connect(self._on_never_hide)
        group.add(self.never_hide)

        self.reset_position_button = QPushButton("Reset position", self)
        self.reset_position_button.clicked.connect(self.reset_position)
        group.add_row(
            "Captions have wandered off screen",
            self.reset_position_button,
            "Puts the caption window back at the bottom of your main display.",
        )
        return group

    # ---------- state ----------

    def refresh(self) -> None:
        overlay = self.settings.overlay
        with self.quiet():
            self.topmost.setChecked(overlay.always_on_top)
            self.monitor.setCurrentIndex(max(0, self.monitor.findData(overlay.screen_name)))
            self.line_limit.setCurrentIndex(max(0, self.line_limit.findData(overlay.max_lines)))
            self.stroke_width.slider.setValue(round(overlay.outline_width * 10))
            self.preset_cards.set_value(overlay.preset.value)
            background = overlay.custom_bg_color.lstrip("#")
            self.background_button.set_hex(f"#{background[:6]}")
            alpha = int(background[6:8], 16) if len(background) >= 8 else 255
            self.opacity.slider.setValue(round(alpha / 255 * 100))
            self.text_colour_button.set_hex(overlay.custom_text_color)
            self.translation_colour.set_hex(overlay.custom_translation_color)
            self.border_colour.set_hex(overlay.custom_border_color)
            self.border_width.slider.setValue(round(overlay.custom_border_width * 10))
            self.outline.setChecked(overlay.custom_outline)
            self.shadow.setChecked(overlay.custom_shadow)
            self.radius.slider.setValue(overlay.custom_radius)
            self.font.setCurrentIndex(max(0, self.font.findData(overlay.font_family)))
            self.font_size.slider.setValue(overlay.font_size)
            self._reload_weights(overlay.font_weight)
            self.line_spacing.slider.setValue(
                round((overlay.line_spacing or _preset_spacing(overlay)) * 100)
            )
            self.align.setCurrentIndex(max(0, self.align.findData(overlay.align)))
            self.padding.setCurrentIndex(max(0, self.padding.findData(overlay.padding)))
            self.animate.setChecked(overlay.animate)
            self.max_pairs.setCurrentIndex(max(0, self.max_pairs.findData(overlay.max_pairs)))
            self.dim_original.setChecked(overlay.dim_original)
            self.original_position.setCurrentIndex(
                max(0, self.original_position.findData(overlay.original_position.value))
            )
            self.auto_hide_seconds.slider.setValue(
                max(2, min(15, round(overlay.auto_hide_seconds)))
            )
            self.never_hide.setChecked(not overlay.auto_hide)
        self._sync_enabled()
        self.saved.refresh()

    def _saved_applied(self) -> None:
        self.refresh()
        self.style_changed.emit()

    def _on_topmost(self, checked: bool) -> None:
        self.settings.overlay.always_on_top = checked
        self._changed()

    def _custom_value(self, name: str, value) -> None:
        setattr(self.settings.overlay, name, value)
        self._changed()

    def _customize(self) -> None:
        from .tokens import overlay_style

        overlay = self.settings.overlay
        style = overlay_style(overlay.preset, overlay)
        def colour(values):
            return "#" + "".join(f"{value:02X}" for value in values)
        overlay.custom_bg_color = colour(style.background)
        overlay.custom_text_color = colour(style.text_color)
        overlay.custom_translation_color = colour(style.translation_color)
        overlay.custom_border_color = colour(style.border) if style.border else "#FFFFFF"
        overlay.custom_border_width = style.border_width if style.border else 0.0
        overlay.custom_outline = style.outline
        overlay.custom_shadow = style.shadow
        overlay.custom_radius = style.radius
        overlay.font_size = style.font_size
        overlay.font_weight = style.font_weight
        overlay.line_spacing = style.line_spacing
        overlay.align = style.align
        overlay.preset = OverlayPreset.CUSTOM
        self.refresh()
        self._changed()

    def _on_monitor(self, index: int) -> None:
        self.settings.overlay.screen_name = self.monitor.itemData(index)
        self.apply("overlay")
        self.style_changed.emit()

    def _on_line_limit(self, index: int) -> None:
        self.settings.overlay.max_lines = self.line_limit.itemData(index)
        self.apply("overlay")
        self.style_changed.emit()

    def _on_stroke_width(self, value: int) -> None:
        self.settings.overlay.outline_width = value / 10
        self.apply("overlay")
        self.style_changed.emit()

    def _sync_enabled(self) -> None:
        preset = self.settings.overlay.preset
        self.custom_group.setEnabled(preset is OverlayPreset.CUSTOM)
        self.custom_reveal.set_expanded(preset is OverlayPreset.CUSTOM)
        self.auto_hide_seconds.setEnabled(self.settings.overlay.auto_hide)
        # A preset that refuses to animate must not show a live toggle claiming
        # otherwise. The checkbox is shown unchecked and disabled, but the
        # stored preference is left alone: the style already resolves to no
        # animation, and writing False here would mean picking Accessibility
        # once silently turned animation off for every other preset too.
        still = not OVERLAY_PRESETS[
            OverlayPreset.GLASS if preset is OverlayPreset.CUSTOM else preset
        ].animate
        self.animate.setEnabled(not still)
        blocker = QSignalBlocker(self.animate)
        self.animate.setChecked(self.settings.overlay.animate and not still)
        del blocker

    def _reload_weights(self, current: int) -> None:
        """Offer only the weights the chosen family actually has.

        Signals are blocked across the rebuild: ``clear()`` emits
        ``currentIndexChanged(-1)``, and a handler that ran then would read
        ``itemData(-1)`` — which is None, not a weight.
        """
        blocker = QSignalBlocker(self.weight)
        self.weight.clear()
        for label, value in weights_for(self.settings.overlay.font_family):
            self.weight.addItem(label, value)
        index = self.weight.findData(current)
        if index < 0:
            # Qt can synthesize a weight. Keep the user's choice when changing
            # families or restoring settings from another computer.
            label = dict((value, label) for label, value in _WEIGHTS).get(current, str(current))
            self.weight.addItem(f"{label} · synthesized", current)
            index = self.weight.findData(current)
        self.weight.setCurrentIndex(max(0, index))
        del blocker

    def _changed(self) -> None:
        self.apply("overlay")
        if not self._muted:
            self.style_changed.emit()

    # ---------- handlers ----------

    def _on_preset(self, value: str) -> None:
        preset = OverlayPreset(value)
        overlay = self.settings.overlay
        overlay.preset = preset
        seed_from_preset(overlay, preset)
        self.refresh()
        self._changed()

    def _on_background_colour(self, value: str) -> None:
        alpha = round(self.opacity.slider.value() / 100 * 255)
        self.settings.overlay.custom_bg_color = f"{value}{alpha:02X}"
        self._changed()

    def _on_opacity(self, value: int) -> None:
        alpha = round(value / 100 * 255)
        base = self.background_button.hex_colour()
        self.settings.overlay.custom_bg_color = f"{base}{alpha:02X}"
        self._changed()

    def _on_text_colour(self, value: str) -> None:
        self.settings.overlay.custom_text_color = value
        self._changed()

    def _on_outline(self, checked: bool) -> None:
        self.settings.overlay.custom_outline = checked
        self._changed()

    def _on_shadow(self, checked: bool) -> None:
        self.settings.overlay.custom_shadow = checked
        self._changed()

    def _on_radius(self, value: int) -> None:
        self.settings.overlay.custom_radius = value
        self._changed()

    def _on_font(self, index: int) -> None:
        self.settings.overlay.font_family = str(self.font.itemData(index) or "")
        with self.quiet():
            self._reload_weights(self.settings.overlay.font_weight)
        self._changed()

    def _on_font_size(self, value: int) -> None:
        self.settings.overlay.font_size = value
        self._changed()

    def _on_line_spacing(self, value: int) -> None:
        self.settings.overlay.line_spacing = value / 100
        self._changed()

    def _on_align(self, index: int) -> None:
        self.settings.overlay.align = str(self.align.itemData(index) or "")
        self._changed()

    def _on_padding(self, index: int) -> None:
        self.settings.overlay.padding = str(self.padding.itemData(index) or "")
        self._changed()

    def _on_animate(self, checked: bool) -> None:
        self.settings.overlay.animate = checked
        self._changed()

    def _on_weight(self, index: int) -> None:
        weight = self.weight.itemData(index)
        if weight is None:  # the combo is mid-rebuild; there is nothing to store
            return
        self.settings.overlay.font_weight = int(weight)
        self._changed()

    def _on_max_pairs(self, index: int) -> None:
        self.settings.overlay.max_pairs = int(self.max_pairs.itemData(index))
        self._changed()

    def _on_dim_original(self, checked: bool) -> None:
        self.settings.overlay.dim_original = checked
        self._changed()

    def _on_position(self, index: int) -> None:
        self.settings.overlay.original_position = OriginalPosition(
            self.original_position.itemData(index)
        )
        self._changed()

    def _on_auto_hide_seconds(self, value: int) -> None:
        self.settings.overlay.auto_hide_seconds = float(value)
        self._changed()

    def _on_never_hide(self, checked: bool) -> None:
        self.settings.overlay.auto_hide = not checked
        self._sync_enabled()
        self._changed()

    def reset_position(self) -> None:
        """Forget every remembered position, on every display."""
        self.settings.overlay.geometry.clear()
        self._changed()
