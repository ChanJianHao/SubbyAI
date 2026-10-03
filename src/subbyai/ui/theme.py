"""Apply shared design tokens and follow operating-system theme changes."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from . import tokens
from .tokens import RADIUS, SPACE, Palette

log = logging.getLogger(__name__)

_listeners: list[Callable[[Palette], None] | weakref.WeakMethod] = []
_current: Palette = tokens.DARK
_theme_setting = "system"
_accent_setting = "sakura"


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() != Qt.ColorScheme.Light
    except AttributeError:  # very old Qt; assume dark
        return True


def current() -> Palette:
    return _current


def apply(app: QApplication, theme_setting: str, accent: str = "sakura") -> Palette:
    """Apply a theme and start following the OS if the setting says so."""
    global _current, _theme_setting, _accent_setting
    _theme_setting = theme_setting
    _accent_setting = accent
    app.setStyle("Fusion")
    _load_fonts()
    palette = tokens.palette_for(theme_setting, system_is_dark(), accent)
    _current = palette
    app.setFont(ui_font())
    app.setStyleSheet(stylesheet(palette))
    _connect_system_watch(app)
    for entry in list(_listeners):
        listener = entry() if isinstance(entry, weakref.WeakMethod) else entry
        if listener is None:
            _listeners.remove(entry)
            continue
        try:
            listener(palette)
        except Exception:  # a bad listener must not break theming
            log.exception("Theme listener failed")
    return palette


def subscribe(listener: Callable[[Palette], None]) -> None:
    # A global theme listener must not keep a closed view and its entire widget
    # tree alive. Free functions remain strong; bound view methods are weak.
    _listeners.append(
        weakref.WeakMethod(listener)
        if getattr(listener, "__self__", None) is not None
        else listener
    )


_watch_connected = False


def _connect_system_watch(app: QApplication) -> None:
    global _watch_connected
    if _watch_connected:
        return
    try:
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _scheme: apply(app, _theme_setting, _accent_setting)
        )
        _watch_connected = True
    except (AttributeError, RuntimeError):
        log.debug("This Qt build cannot report OS colour-scheme changes")


def _load_fonts() -> None:
    """Load bundled fonts if present; system fallbacks cover their absence."""
    from importlib import resources

    try:
        font_dir = resources.files("subbyai.resources.fonts")
    except (ModuleNotFoundError, FileNotFoundError):
        return
    try:
        for entry in font_dir.iterdir():
            if entry.name.endswith((".ttf", ".otf")):
                with resources.as_file(entry) as path:
                    QFontDatabase.addApplicationFont(str(path))
    except (OSError, AttributeError):
        log.debug("No bundled fonts loaded")


def ui_font(role: str = "body") -> QFont:
    size, weight = tokens.TYPE_SCALE.get(role, tokens.TYPE_SCALE["body"])
    font = QFont(_first_available(tokens.UI_FONT_STACK))
    font.setPixelSize(size)
    font.setWeight(QFont.Weight(weight))
    return font


def caption_font(size: int, weight: int, family: str = "") -> QFont:
    """A caption font, optionally in a family the user picked.

    A family that is not installed falls back to the stack rather than to
    whatever Qt substitutes, so a settings file copied between machines
    degrades to something legible instead of something arbitrary.
    """
    stack = [family, *tokens.CAPTION_FONT_STACK] if family else tokens.CAPTION_FONT_STACK
    font = QFont(_first_available(stack))
    font.setPixelSize(size)
    font.setWeight(QFont.Weight(weight))
    return font


def mono_font(size: int = 12) -> QFont:
    font = QFont(_first_available(tokens.MONO_FONT_STACK))
    font.setPixelSize(size)
    return font


def _first_available(stack: list[str]) -> str:
    families = set(QFontDatabase.families())
    for name in stack:
        if name in families:
            return name
    return stack[-1]


def app_icon() -> QIcon:
    """The two-bar mark: a line you heard, and a line you understand."""
    from importlib import resources

    icon = QIcon()
    try:
        data = resources.files("subbyai.resources").joinpath("icon.ico").read_bytes()
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        icon.addPixmap(pixmap)
    except (OSError, ModuleNotFoundError):
        log.debug("Application icon resource missing")
    return icon


def stylesheet(c: Palette) -> str:
    """One QSS template driven entirely by tokens."""
    return f"""
    QWidget {{
        background: transparent;
        color: {c.text};
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background: {c.canvas}; }}
    QFrame#card, QFrame#surface {{
        background: {c.surface};
        border: 1px solid {c.hairline};
        border-radius: {RADIUS["lg"]}px;
    }}
    QFrame#hairline {{ background: {c.hairline}; border: none; max-height: 1px; }}

    QLabel#display {{ font-size: 26px; font-weight: 600; }}
    QLabel#title {{ font-size: 20px; font-weight: 600; }}
    QLabel#heading {{ font-size: 16px; font-weight: 600; }}
    QLabel#secondary {{ color: {c.text_secondary}; }}
    QLabel#tertiary {{ color: {c.text_tertiary}; }}
    QLabel#micro {{
        color: {c.text_tertiary};
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.4px;
    }}

    QPushButton {{
        background: {c.raised};
        border: 1px solid {c.stroke};
        border-radius: {RADIUS["md"]}px;
        padding: {SPACE["sm"]}px {SPACE["lg"]}px;
        color: {c.text};
    }}
    QPushButton:hover {{ border-color: {c.accent}; }}
    QPushButton:pressed {{ background: {c.input_bg}; }}
    QPushButton:disabled {{ color: {c.text_disabled}; border-color: {c.hairline}; }}
    /* Keyboard focus must be visible on every control, not just text fields.
       There was no focus indicator on buttons at all, which made the app
       unusable by keyboard for anyone who cannot track focus by guesswork. */
    QPushButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QCheckBox:focus, QRadioButton:focus, QSlider:focus,
    QListWidget:focus, QListView:focus {{
        border: 2px solid {c.focus};
    }}
    QPushButton#primary {{
        background: {c.accent};
        color: {c.accent_on};
        border: none;
        border-radius: {RADIUS["full"]}px;
        padding: 12px 28px;
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: {c.accent_hover}; }}
    QPushButton#primary:pressed {{ background: {c.accent_pressed}; }}
    QPushButton#danger {{ color: {c.error}; border-color: {c.error}; }}
    QPushButton#quiet {{
        background: transparent;
        border: none;
        color: {c.text_secondary};
        padding: 6px 10px;
    }}
    QPushButton#quiet:hover {{ color: {c.accent}; }}
    QPushButton#chip {{
        background: {c.raised};
        border: 1px solid {c.hairline};
        border-radius: {RADIUS["full"]}px;
        padding: 8px 16px;
        color: {c.text_secondary};
    }}
    QPushButton#chip:checked {{
        background: {c.accent_subtle};
        border-color: {c.accent};
        color: {c.text};
    }}
    QPushButton#segment {{
        background: transparent;
        border: none;
        border-radius: {RADIUS["full"]}px;
        padding: 7px 20px;
        color: {c.text_secondary};
    }}
    QPushButton#segment:checked {{
        background: {c.accent_subtle};
        color: {c.text};
        font-weight: 600;
    }}
    QPushButton#segment:hover {{ color: {c.text}; }}

    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {c.input_bg};
        border: 1px solid {c.stroke};
        border-radius: {RADIUS["md"]}px;
        padding: 7px 10px;
        color: {c.text};
        selection-background-color: {c.accent};
        selection-color: {c.accent_on};
    }}

    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background: {c.float_};
        border: 1px solid {c.hairline};
        border-radius: {RADIUS["md"]}px;
        selection-background-color: {c.accent_subtle};
        selection-color: {c.text};
        padding: 4px;
    }}

    QCheckBox {{ spacing: {SPACE["sm"]}px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {c.stroke};
        border-radius: {RADIUS["sm"] - 2}px;
        background: {c.input_bg};
    }}
    QCheckBox::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; }}
    QRadioButton::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {c.stroke};
        border-radius: 8px;
        background: {c.input_bg};
    }}
    QRadioButton::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; }}

    QListWidget, QListView, QTreeView {{
        background: {c.surface};
        border: 1px solid {c.hairline};
        border-radius: {RADIUS["lg"]}px;
        outline: none;
        padding: 4px;
    }}
    QListWidget::item, QListView::item {{
        border-radius: {RADIUS["md"]}px;
        padding: {SPACE["sm"]}px;
        color: {c.text};
    }}
    QListWidget::item:hover, QListView::item:hover {{ background: {c.raised}; }}
    QListWidget::item:selected, QListView::item:selected {{
        background: {c.accent_subtle};
        color: {c.text};
    }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {c.stroke};
        border-radius: 5px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c.text_tertiary}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {c.stroke};
        border-radius: 5px;
        min-width: 28px;
    }}

    QProgressBar {{
        background: {c.input_bg};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: 3px; }}

    QMenu {{
        background: {c.float_};
        border: 1px solid {c.hairline};
        border-radius: {RADIUS["md"]}px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: {RADIUS["sm"]}px; }}
    QMenu::item:selected {{ background: {c.accent_subtle}; }}
    QMenu::separator {{ height: 1px; background: {c.hairline}; margin: 6px 8px; }}

    QToolTip {{
        background: {c.float_};
        color: {c.text};
        border: 1px solid {c.hairline};
        border-radius: {RADIUS["sm"]}px;
        padding: 6px 8px;
    }}
    QSlider::groove:horizontal {{ background: {c.input_bg}; height: 4px; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {c.accent};
        width: 14px; height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSplitter::handle {{ background: {c.hairline}; }}
    """
