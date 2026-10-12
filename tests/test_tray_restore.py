"""Tray commands restore usable windows after hiding and minimizing."""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSystemTrayIcon

from subbyai.core.settings import Settings
from subbyai.ui.shell import Shell
from subbyai.ui.tray import Tray


@pytest.fixture
def window(qt_app):
    shell = Shell(Settings())
    tray = Tray(shell)
    tray.open_requested.connect(shell.show_for_user)
    shell.show()
    qt_app.processEvents()
    yield shell, tray
    shell.close_for_quit()


@pytest.mark.parametrize("state", ["hidden", "minimized", "maximized_minimized"])
def test_open_restores_window_without_recreating_or_unmaximizing(window, qt_app, state):
    shell, tray = window
    handle = shell.winId()
    if state == "hidden":
        shell.close()
        assert not shell.isVisible()
    else:
        if state == "maximized_minimized":
            shell.showMaximized()
        shell.showMinimized()
        assert shell.isMinimized()
    tray._open_action.trigger()
    QTest.qWait(30)
    assert shell.isVisible() and not shell.isMinimized()
    assert shell.winId() == handle
    assert shell.isMaximized() == (state == "maximized_minimized")


@pytest.mark.parametrize("reason", [
    QSystemTrayIcon.ActivationReason.Trigger,
    QSystemTrayIcon.ActivationReason.DoubleClick,
])
def test_click_and_double_click_open_the_hidden_window(window, reason):
    shell, tray = window
    shell.close()
    tray._icon.activated.emit(reason)
    QTest.qWait(30)
    assert shell.isVisible() and not shell.isMinimized()


def test_context_click_does_not_open_main_window_and_duplicate_requests_coalesce(window):
    shell, tray = window
    shell.close()
    requests = []
    tray.open_requested.connect(lambda: requests.append(True))
    tray._icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    QTest.qWait(30)
    assert not shell.isVisible() and not requests
    tray._icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    tray._icon.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    QTest.qWait(30)
    assert requests == [True]


def test_open_releases_popup_before_restoring_and_does_not_interrupt_quit(window):
    shell, tray = window
    shell.close()
    tray._menu.popup(QPoint(40, 40))
    tray._open_action.trigger()
    QTest.qWait(30)
    assert shell.isVisible() and not tray._menu.isVisible()
    shell.close_for_quit()
    tray._open_action.trigger()
    QTest.qWait(30)
    assert not shell.isVisible()
