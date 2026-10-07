"""Skippable first-run setup with asynchronous hardware and audio checks.

Completion is recorded when the dialog closes. Each page leaves usable settings;
heavy work remains off the GUI thread."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..asr.capability import MachineCapability
from ..branding import APP_NAME
from ..core.settings import Settings
from . import theme
from .motion import MotionStack
from .onboarding_hardware import AudioCheckStep, QualityStep
from .onboarding_steps import LanguageStep, ReadyStep, Step, StyleStep, UsageStep, WelcomeStep
from .onboarding_widgets import ProgressDots
from .tokens import SPACE
from .widgets import fit_to_screen

log = logging.getLogger(__name__)

WIZARD_SIZE = (640, 560)
#: Small enough for a 1366x768 laptop with OS chrome; the step area scrolls
#: rather than clipping the footer, so this stays honest.
WIZARD_MINIMUM = (560, 420)


class _CapabilityProbe(QThread):
    """Hardware detection off the UI thread. A spinner is not a frozen window."""

    ready = Signal(object)

    def run(self) -> None:
        from ..asr import capability

        try:
            self.ready.emit(capability.detect())
        except Exception:
            log.exception("Could not work out what this computer can do")
            self.ready.emit(None)


class OnboardingWizard(QDialog):
    """The first-run wizard. Mutates ``settings`` in place; saving is the shell's job.

    ``finished_setup`` carries whether the user asked to start captioning right
    away, and fires exactly once however the dialog was dismissed.
    """

    #: True when the user pressed "Start captions", False for every other exit.
    finished_setup = Signal(bool)
    #: A preset the user is looking at right now, so the shell can show the real
    #: overlay with sample text while step 5 is open.
    style_previewed = Signal(object)

    def __init__(
        self,
        settings: Settings,
        capability: MachineCapability | None = None,
        devices: list | None = None,
        restore_callback: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._restore_callback = restore_callback
        self._capability = capability
        self._probe: _CapabilityProbe | None = None
        self._start_now = False
        self._emitted = False
        self._torn_down = False
        self._audio_preview = None

        self.setWindowTitle(f"Set up {APP_NAME}")
        self.setWindowIcon(theme.app_icon())
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, False)
        self.setMinimumSize(*WIZARD_MINIMUM)

        self._welcome = WelcomeStep(settings, show_restore=restore_callback is not None)
        self._usage = UsageStep(settings)
        self._quality = QualityStep(settings)
        self._language = LanguageStep(settings)
        self._style = StyleStep(settings)
        self._audio = AudioCheckStep(settings, devices)
        self._ready = ReadyStep(settings)
        self._steps: tuple[Step, ...] = (
            self._welcome,
            self._usage,
            self._quality,
            self._language,
            self._style,
            self._audio,
            self._ready,
        )

        self._build_layout()
        self._welcome.restore_requested.connect(self._restore_previous)
        self._usage.games_changed.connect(self._ready.set_games_tip)
        self._style.preset_chosen.connect(self.style_previewed)
        self._ready.set_games_tip(self._usage.games_selected)

        if capability is not None:
            self._quality.set_capability(capability)
        else:
            self._probe = _CapabilityProbe(self)
            self._probe.ready.connect(self._capability_ready)
            self._probe.start()

        self._show_step(0)
        # Sized after the steps exist, so the window is never smaller than the
        # page it is showing, and never larger than the screen it opens on.
        fit_to_screen(self, *WIZARD_SIZE)
        self._centre()

    # ---------- public wiring for the shell ----------

    def enable_audio_test(self, factory) -> None:
        from ..audio.preview import AudioPreview

        self._audio_preview = AudioPreview(factory)

        def level():
            if self._audio_preview.error:
                self._audio._status.setText(self._audio_preview.error)
            return self._audio_preview.level

        self._audio.set_level_source(level)
        self._audio.device_chosen.connect(lambda: self._sync_audio_test())

    def _sync_audio_test(self) -> None:
        if self._audio_preview is None:
            return
        from ..audio.base import resolve_device

        device = resolve_device(
            self._audio._devices,
            self.settings.audio.device_id,
            self.settings.audio.device_name,
            source=self.settings.audio.source,
        )
        self._audio_preview.set_active(
            self._steps[self.current_index] is self._audio and device is not None, device
        )

    def set_level_source(self, source: Callable[[], float] | None) -> None:
        """Give the audio check something to poll: a callable returning 0..1 loudness.

        Polled at roughly 30 Hz while step 6 is on screen and stopped on exit, so
        the shell can hand over a live meter without owning a timer. Use
        ``set_level`` instead if the shell would rather push.
        """
        self._audio.set_level_source(source)

    @Slot(float)
    def set_level(self, level: float) -> None:
        """Push one loudness reading (0..1) into the audio check."""
        self._audio.set_level(level)

    # ---------- layout ----------

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        root.setSpacing(SPACE["lg"])

        self._skip = QPushButton("Skip")
        self._skip.setObjectName("quiet")
        self._skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip.clicked.connect(self._skip_ahead)
        root.addWidget(self._skip, 0, Qt.AlignmentFlag.AlignRight)

        self._stack = MotionStack()
        for step in self._steps:
            self._stack.addWidget(step)

        # The step area scrolls; Skip and the footer buttons never do. Without
        # this, the tallest step (choosing quality) set the wizard's minimum
        # height above its own window size, so Back/Continue were pushed off the
        # bottom edge at launch and the user had to drag the window taller
        # before they could finish setup.
        self._scroller = QScrollArea()
        self._scroller.setWidget(self._stack)
        self._scroller.setWidgetResizable(True)
        self._scroller.setFrameShape(QFrame.Shape.NoFrame)
        self._scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroller, 1)

        footer = QGridLayout()
        footer.setColumnStretch(0, 1)
        footer.setColumnStretch(2, 1)
        self._back = QPushButton("Back")
        self._back.setObjectName("quiet")
        self._back.clicked.connect(self._go_back)
        footer.addWidget(self._back, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self._dots = ProgressDots(len(self._steps))
        footer.addWidget(self._dots, 0, 1, Qt.AlignmentFlag.AlignCenter)

        right = QGridLayout()
        self._secondary = QPushButton(f"Open {APP_NAME} first")
        self._secondary.setObjectName("quiet")
        self._secondary.clicked.connect(lambda: self._finish(start_now=False))
        self._primary = QPushButton("Get started")
        self._primary.setObjectName("primary")
        self._primary.setDefault(True)
        self._primary.clicked.connect(self._go_next)
        right.addWidget(self._secondary, 0, 0)
        right.addWidget(self._primary, 0, 1)
        footer.addLayout(right, 0, 2, Qt.AlignmentFlag.AlignRight)
        root.addLayout(footer)

    def _centre(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())

    def showEvent(self, event) -> None:  # Qt API casing
        super().showEvent(event)
        # The native frame dimensions are available only after the first show.
        self._centre()

    # ---------- navigation ----------

    @property
    def current_index(self) -> int:
        return self._stack.currentIndex()

    def _show_step(self, index: int) -> None:
        index = max(0, min(len(self._steps) - 1, index))
        last = index == len(self._steps) - 1
        step = self._steps[index]
        self._stack.setCurrentIndex(index)
        self._sync_audio_test()
        self._dots.set_index(index)
        self._back.setVisible(index > 0)
        self._secondary.setVisible(last)
        self._primary.setText(step.primary_label)
        step.on_enter()

    def _go_next(self) -> None:
        index = self.current_index
        self._steps[index].commit()
        if index >= len(self._steps) - 1:
            self._finish(start_now=True)
            return
        self._show_step(index + 1)

    def _go_back(self) -> None:
        self._show_step(self.current_index - 1)

    def _skip_ahead(self) -> None:
        """Take the promised default for every page, then land on Ready."""
        self._mark_complete()
        if self.current_index >= len(self._steps) - 1:
            self._finish(start_now=False)
            return
        self._show_step(len(self._steps) - 1)

    def _restore_previous(self) -> None:
        """Hand the "I've used SubbyAI before" link to the shell, which owns importing."""
        if self._restore_callback is None:
            return
        try:
            restored = bool(self._restore_callback())
        except Exception:
            log.exception("Restoring earlier settings failed")
            restored = False
        self._welcome.show_restore_result(restored)
        if restored:
            self._mark_complete()
            self._show_step(len(self._steps) - 1)

    # ---------- completion ----------

    def _capability_ready(self, capability: object) -> None:
        self._capability = capability if isinstance(capability, MachineCapability) else None
        self._quality.set_capability(self._capability)

    def _mark_complete(self) -> None:
        for step in self._steps:
            step.apply_defaults()
        self.settings.general.onboarding_complete = True

    def _finish(self, start_now: bool) -> None:
        self._start_now = start_now
        self.accept()

    def _teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        self._audio.stop()
        if self._audio_preview is not None:
            self._audio_preview.close()
        probe, self._probe = self._probe, None
        if probe is not None:
            if probe.isRunning():
                # Let a slow driver probe finish offscreen; never destroy a
                # running QThread or wait for it on the GUI thread.
                probe.setParent(None)
                _pending_probes.add(probe)
                probe.finished.connect(lambda: _release_probe(probe))
            else:
                probe.deleteLater()

    def closeEvent(self, event) -> None:
        """QDialog skips ``done`` when it was never shown; setup still counts as run."""
        if not self.isVisible() and not self._emitted:
            self.done(QDialog.DialogCode.Rejected)
        super().closeEvent(event)

    def done(self, result: int) -> None:
        """Every exit funnels through here — accept, reject, Esc and the window button."""
        self._mark_complete()
        self._teardown()
        super().done(result)
        if not self._emitted:
            self._emitted = True
            self.finished_setup.emit(self._start_now)


_pending_probes: set = set()


def _release_probe(probe) -> None:
    _pending_probes.discard(probe)
    probe.deleteLater()
