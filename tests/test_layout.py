"""Layout invariants: nothing may be clipped or unreachable at launch.

The bug these exist for: the app opened with content that needed more room than
the window gave it, so buttons were compressed or pushed off-screen and users
had to resize the window before they could use it. Increasing the default size
would have hidden it; these tests instead assert the honest relationship —

    a window never opens smaller than its content needs, and
    content can always shrink to the window's stated minimum

— so a widget that refuses to shrink (an unwrapped label, a combo box sized to
its longest item, a row of fixed-width buttons) fails here rather than in front
of a user.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QComboBox, QLabel, QWidget

from subbyai.core.settings import SettingsStore
from subbyai.storage import SessionStore
from subbyai.ui import theme

#: The smallest laptop we promise to work on: 1366x768 minus OS chrome.
SMALL_SCREEN = (1366, 700)


@pytest.fixture
def surfaces(qt_app, tmp_path):
    """Every top-level surface, built the way the app builds them."""
    from subbyai.ui.history_view import HistoryView
    from subbyai.ui.live_view import LiveView
    from subbyai.ui.onboarding import OnboardingWizard
    from subbyai.ui.settings_view import SettingsDeps, SettingsView
    from subbyai.ui.shell import Shell

    theme.apply(QApplication.instance(), "dark")
    store = SettingsStore(tmp_path / "settings.json")
    sessions = SessionStore(tmp_path / "sessions.db")

    shell = Shell(store.settings)
    live = LiveView(store.settings)
    history = HistoryView(sessions)
    settings_view = SettingsView(store, SettingsDeps())
    for surface in (live, history, settings_view):
        shell.add_surface(surface)
    wizard = OnboardingWizard(store.settings, devices=[])

    yield {
        "shell": shell,
        "live": live,
        "history": history,
        "settings": settings_view,
        "wizard": wizard,
    }
    sessions.close()
    for widget in (shell, wizard):
        widget.close()


def test_windows_open_big_enough_for_their_content(surfaces):
    """A window must never launch smaller than the content it contains."""
    failures = []
    for name in ("shell", "wizard"):
        window = surfaces[name]
        needed = window.minimumSizeHint()
        opened = window.size()
        if needed.width() > opened.width() or needed.height() > opened.height():
            failures.append(
                f"{name} opens at {opened.width()}x{opened.height()} but its content "
                f"needs {needed.width()}x{needed.height()}"
            )
    assert not failures, "; ".join(failures)


def test_stated_minimum_is_achievable(surfaces):
    """A window that says it can shrink to N must actually work at N.

    If content cannot shrink that far, Qt squeezes children below their
    minimums and things get clipped — the original bug.
    """
    failures = []
    for name in ("shell", "wizard"):
        window = surfaces[name]
        stated = window.minimumSize()
        needed = window.minimumSizeHint()
        if needed.width() > stated.width() or needed.height() > stated.height():
            failures.append(
                f"{name} claims a minimum of {stated.width()}x{stated.height()} but "
                f"its content cannot go below {needed.width()}x{needed.height()}"
            )
    assert not failures, "; ".join(failures)


def test_surfaces_fit_a_small_laptop_screen(surfaces):
    """1366x768 is still the most common cheap-laptop resolution."""
    width, height = SMALL_SCREEN
    failures = []
    for name, widget in surfaces.items():
        needed = widget.minimumSizeHint()
        if needed.width() > width or needed.height() > height:
            failures.append(
                f"{name} needs {needed.width()}x{needed.height()}, "
                f"too big for {width}x{height}"
            )
    assert not failures, "; ".join(failures)


@pytest.mark.parametrize(("surface", "segment"), [("live", 0), ("history", 1), ("settings", 2)])
@pytest.mark.parametrize("width", [880, 640], ids=["default", "minimum"])
def test_no_horizontal_scrolling(surfaces, surface, segment, width):
    """Horizontal scrollbars in a settings pane mean controls are out of reach.

    Checked at the launch width *and* at the window's stated minimum: a minimum
    the content cannot actually honour is the bug, not the number.
    """
    shell = surfaces["shell"]
    shell.resize(width, 620 if width > 700 else 460)
    shell.show()
    shell.show_segment(segment)
    QApplication.processEvents()

    widget = surfaces[surface]
    QApplication.processEvents()

    offenders = []
    for area in widget.findChildren(QAbstractScrollArea):
        bar = area.horizontalScrollBar()
        if bar is None or bar.maximum() <= 0:
            continue
        inner = area.widget() if hasattr(area, "widget") else None
        needed = inner.minimumSizeHint().width() if inner is not None else None
        offenders.append(
            f"{type(area).__name__} scrolls horizontally by {bar.maximum()}px"
            + (f" (content needs {needed}px)" if needed else "")
        )
    assert not offenders, f"{surface}: " + "; ".join(offenders)


def test_body_labels_wrap(surfaces):
    """A long label that refuses to wrap sets an unshrinkable window width."""
    offenders = []
    for name, widget in surfaces.items():
        for label in widget.findChildren(QLabel):
            if label.wordWrap():
                continue
            if label.minimumSizeHint().width() > 420:
                offenders.append(f"{name}: {label.text()[:50]!r}")
    assert not offenders, "labels needing wordWrap: " + "; ".join(offenders)


def test_combo_boxes_do_not_size_to_their_longest_item(surfaces):
    """A combo full of long provider names must not dictate the window width."""
    offenders = []
    for name, widget in surfaces.items():
        for combo in widget.findChildren(QComboBox):
            if combo.minimumSizeHint().width() > 320:
                offenders.append(
                    f"{name}: combo wants {combo.minimumSizeHint().width()}px"
                )
    assert not offenders, "; ".join(offenders)


def test_nothing_is_wider_than_the_window_it_lives_in(surfaces):
    """Catches a child whose minimum exceeds its own parent surface."""
    shell = surfaces["shell"]
    shell.resize(880, 620)
    shell.show()
    QApplication.processEvents()

    offenders = []
    for child in shell.findChildren(QWidget):
        if not child.isVisible():
            continue
        if child.minimumSizeHint().width() > 880:
            offenders.append(
                f"{type(child).__name__}#{child.objectName() or '-'} "
                f"needs {child.minimumSizeHint().width()}px"
            )
    assert not offenders, "; ".join(sorted(set(offenders)))


def test_large_app_text_keeps_settings_reachable(surfaces, qt_app):
    theme.apply(qt_app, "dark", text_scale=1.5)
    shell = surfaces["shell"]
    shell.resize(640, 460)
    shell.show_segment(2)
    shell.show()
    QApplication.processEvents()
    for area in surfaces["settings"].findChildren(QAbstractScrollArea):
        assert area.horizontalScrollBar().maximum() == 0
    theme.apply(qt_app, "dark")
