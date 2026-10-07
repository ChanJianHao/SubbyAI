"""Interruption, accessibility and lifecycle boundaries of decorative motion."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint, QSignalBlocker, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from subbyai.core.events import CaptionSegment, PrivacyTier
from subbyai.core.settings import Settings, SettingsStore
from subbyai.ui import theme
from subbyai.ui.history_view import HistoryView
from subbyai.ui.live_view import LiveView
from subbyai.ui.motion import Expandable, MotionStack, confirm_action, policy, reveal
from subbyai.ui.motion_widgets import ActivityIndicator, MotionToggle, SmoothProgressBar
from subbyai.ui.overlay import CaptionOverlay
from subbyai.ui.overlay_pill import ControlPill
from subbyai.ui.settings_widgets.controls import ChoiceCard
from subbyai.ui.transcript_model import TranscriptModel
from subbyai.ui.widgets import Toast


def test_native_popup_feedback_survives_collection_and_releases_on_destruction(qt_app):
    import gc
    import weakref

    from PySide6.QtWidgets import QComboBox

    from subbyai.ui.motion import install

    install(qt_app)
    combo = QComboBox()
    combo.addItems(["System sound", "Microphone"])
    combo.show()
    combo.showPopup()
    qt_app.processEvents()
    popup = combo.view().window()
    key = id(popup)
    feedback = weakref.ref(popup._window_entrance)
    del popup
    gc.collect()
    assert feedback() is not None
    assert feedback().tween.animation is not None
    combo.hidePopup()
    combo.showPopup()
    qt_app.processEvents()
    combo.hidePopup()
    combo.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()
    assert key not in qt_app._feedback_installer._windows


def test_popup_feedback_creation_tolerates_reentrant_show(qt_app, monkeypatch):
    from PySide6.QtWidgets import QMenu

    from subbyai.ui import motion

    motion.install(qt_app)
    original = motion._WindowEntrance
    calls = []

    def construct(widget):
        calls.append(widget)
        QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.Show))
        return original(widget)

    monkeypatch.setattr(motion, "_WindowEntrance", construct)
    menu = QMenu()
    menu.addAction("Start captions")
    menu.show()
    qt_app.processEvents()
    assert calls == [menu]
    assert menu._window_entrance.tween.animation is not None
    menu.close()
    menu.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def motion_enabled(qt_app, monkeypatch):
    controller = policy()
    previous = controller.requested
    monkeypatch.setattr(controller, "_system", False)
    controller.configure(False)
    yield
    controller.configure(previous)


def test_navigation_is_immediate_and_last_request_wins(qt_app):
    stack = MotionStack()
    pages = [QPushButton(str(index)) for index in range(3)]
    for page in pages:
        stack.addWidget(page)
    stack.resize(500, 300)
    stack.show()
    qt_app.processEvents()
    for index in (1, 2, 0, 2, 1):
        stack.setCurrentIndex(index)
        assert stack.currentWidget() is pages[index]
        assert not pages[index].isHidden()
    QTest.qWait(260)
    assert stack._snapshot.isHidden()
    assert stack._snapshot.pixmap.isNull()
    assert stack._transition.animation.state() == QAbstractAnimation.State.Stopped
    stack.setCurrentIndex(0)
    stack.resize(600, 400)
    assert stack._snapshot.pixmap.isNull()


def test_reduced_motion_settles_active_transitions(qt_app):
    stack = MotionStack()
    stack.addWidget(QWidget())
    stack.addWidget(QWidget())
    stack.show()
    qt_app.processEvents()
    stack.setCurrentIndex(1)
    assert not stack._snapshot.pixmap.isNull()
    policy().configure(True)
    assert stack._snapshot.pixmap.isNull()
    stack.setCurrentIndex(0)
    assert stack.currentIndex() == 0
    assert stack._snapshot.pixmap.isNull()


def test_reveal_reverses_and_disables_exiting_actions(qt_app):
    button = QPushButton("Confirm")
    button.show()
    reveal(button, False)
    assert not button.isEnabled()
    reveal(button, True)
    assert button.isEnabled()
    QTest.qWait(220)
    assert button.isVisible()
    assert button.graphicsEffect().opacity() == 1.0
    reveal(button, False)
    policy().configure(True)
    assert button.isHidden()
    reveal(button, True)
    assert button.isEnabled()
    assert button.graphicsEffect().opacity() == 1.0


def test_expandable_reverses_without_leaving_clipped_content(qt_app):
    host = QWidget()
    layout = QVBoxLayout(host)
    content = QPushButton("Advanced controls")
    panel = Expandable(content)
    layout.addWidget(panel)
    host.show()
    qt_app.processEvents()
    panel.set_expanded(False)
    assert not content.isEnabled()
    panel.set_expanded(True)
    QTest.qWait(280)
    assert panel.isVisible() and content.isEnabled()
    assert panel.maximumHeight() == 16777215
    panel.set_expanded(False)
    QTest.qWait(280)
    assert panel.isHidden()
    panel.set_expanded(True)
    assert panel._height.animation.state() == QAbstractAnimation.State.Running
    policy().configure(True)
    assert panel.maximumHeight() == 16777215


def test_switch_keeps_state_when_refresh_blocks_signals(qt_app):
    toggle = MotionToggle("Use captions")
    toggle.show()
    blocker = QSignalBlocker(toggle)
    toggle.setChecked(True)
    assert toggle._position.value == 1.0
    del blocker
    for _ in range(7):
        toggle.click()
    assert not toggle.isChecked()
    QTest.qWait(150)
    assert toggle._position.value == 0.0
    toggle.setFocus()
    QTest.keyClick(toggle, Qt.Key.Key_Space)
    assert toggle.isChecked()


def test_progress_reports_real_value_and_stops_when_hidden(qt_app):
    bar = SmoothProgressBar()
    bar.show()
    bar.setValue(20)
    bar.setValue(90)
    assert bar.value() == 90
    QTest.qWait(220)
    assert bar._fill.value == pytest.approx(0.9)
    bar.setRange(0, 0)
    assert bar._cycle.state() == QAbstractAnimation.State.Running
    bar.hide()
    assert bar._cycle.state() == QAbstractAnimation.State.Stopped
    bar.show()
    policy().configure(True)
    assert bar._cycle.state() == QAbstractAnimation.State.Stopped
    bar.setRange(0, 100)
    bar.setValue(35)
    assert bar._fill.value == pytest.approx(0.35)


def test_busy_feedback_never_runs_in_background(qt_app):
    indicator = ActivityIndicator()
    indicator.set_busy(True)
    assert indicator._cycle.state() == QAbstractAnimation.State.Running
    indicator.hide()
    assert indicator._cycle.state() == QAbstractAnimation.State.Stopped
    indicator.show()
    assert indicator._cycle.state() == QAbstractAnimation.State.Running
    policy().configure(True)
    assert indicator.isVisible()
    assert indicator._cycle.state() == QAbstractAnimation.State.Stopped


def test_replacement_toast_does_not_inherit_old_deadline(qt_app):
    toast = Toast()
    toast.show_message("Temporary", seconds=0.02)
    toast.show_message("Keep this message", seconds=0)
    QTest.qWait(60)
    assert toast.isVisible() and toast.message == "Keep this message"
    assert not toast._timer.isActive()
    toast.dismiss()
    toast.show_message("New message", seconds=0)
    QTest.qWait(220)
    assert toast.isVisible() and toast.graphicsEffect().opacity() == 1.0


def test_confirmation_repeats_and_preserves_a_new_button_state(qt_app):
    button = QPushButton("Copy")
    confirm_action(button, "Copied ✓")
    confirm_action(button, "Copied ✓")
    assert button._confirmation.original == "Copy"
    button._confirmation.restore()
    assert button.text() == "Copy"
    confirm_action(button, "Copied ✓")
    button.setText("Unavailable")
    button._confirmation.restore()
    assert button.text() == "Unavailable"


def test_card_release_outside_cancels_and_keyboard_works(qt_app):
    card = ChoiceCard("Balanced")
    card.resize(200, 100)
    calls = []
    card.clicked.connect(lambda: calls.append(True))
    card.show()
    QTest.mousePress(card, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    assert not calls
    QTest.mouseRelease(card, Qt.MouseButton.LeftButton, pos=QPoint(-1, -1))
    assert not calls
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    QTest.keyClick(card, Qt.Key.Key_Space)
    assert len(calls) == 2


def test_translation_updates_live_caption_without_replacing_its_widget(qt_app):
    live = LiveView(Settings())
    live.show()
    segment = CaptionSegment(text="Hello", language="en")
    live.add_segment(segment)
    block = live._segment_blocks[segment.id]
    live.update_segment(segment.with_translation("Bonjour", "fr", "Local", PrivacyTier.ON_DEVICE))
    assert live._segment_blocks[segment.id] is block
    assert block._translated.text() == "Bonjour"
    theme.apply(qt_app, "light")
    assert live._segment_blocks[segment.id] is block
    for index in range(4):
        live.add_segment(CaptionSegment(text=f"Line {index}", language="en"))
    assert len(live._segment_blocks) == 3
    live.clear_segments()
    assert not live._segment_blocks
    assert live._preview_stack.currentIndex() == 0


def test_overlay_and_hover_pill_honor_reduced_motion(qt_app):
    overlay = CaptionOverlay(Settings())
    overlay.show()
    overlay.show_segment(CaptionSegment(text="Hello", language="en"))
    policy().configure(True)
    assert overlay._enter_fade.state() == QAbstractAnimation.State.Stopped
    assert all(pair.opacity == 1.0 for pair in overlay._pairs)
    overlay._fade_window(0.5, 180)
    assert overlay.windowOpacity() == pytest.approx(0.5, abs=0.01)
    pill = ControlPill(overlay)
    pill.reveal()
    assert pill._fade.opacity() == 1.0
    pill.conceal()
    assert pill.isHidden()


def test_motion_preference_round_trips(tmp_path):
    store = SettingsStore(tmp_path / "motion.json")
    store.settings.general.reduce_motion = True
    store.notify("general")
    assert SettingsStore(store.path).settings.general.reduce_motion


def test_transcript_flash_restarts_its_deadline(qt_app, monkeypatch):
    from subbyai.ui import transcript_model

    monkeypatch.setattr(transcript_model, "FLASH_MS", 80)
    model = TranscriptModel()
    first, second = CaptionSegment(text="One"), CaptionSegment(text="Two")
    model.set_segments([first, second])
    model.flash(first.id)
    QTest.qWait(50)
    model.flash(second.id)
    QTest.qWait(45)
    assert model.is_flashing(second) and not model.is_flashing(first)
    QTest.qWait(180)
    assert not model.is_flashing(second)
    model.flash(first.id)
    model.set_segments([second])
    assert not model._flash_timer.isActive()


def test_deferred_history_work_is_owned_by_its_widget(qt_app, store):
    history = HistoryView(store)
    assert history._preview_timer.parent() is history
    history.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.qWait(25)  # A queued preview must not outlive the deleted view.
