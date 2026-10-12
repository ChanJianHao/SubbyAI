"""The caption overlay: the one window that *is* the product.

Everything else in SubbyAI is a guest. This window never activates, never takes
focus, and never raises itself over the user's work — it only re-asserts topmost
so a borderless-fullscreen game cannot bury the captions.

Three rules explain the shapes here:

- A caption must not rewrite itself while it is being read, so the translation
  line is reserved the instant a caption appears (see ``overlay_layout``) and a
  late translation fills a gap instead of shifting text.
- Presets are data (``tokens.overlay_style``), never branches: a new look is a
  table row, and the accessibility preset turns motion off by setting a field.
- An empty panel must never be ambiguous. Silence, music and a stalled pipeline
  look identical, so when there is nothing to show the panel says which it is.
"""

from __future__ import annotations

import logging
import weakref
from collections import deque
from dataclasses import replace
from enum import StrEnum

from PySide6.QtCore import (
    QAbstractAnimation,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from ..branding import APP_NAME
from ..core.events import CaptionSegment
from ..core.health import HealthReport, HealthState
from ..core.settings import Settings
from ..system import window_effects
from . import overlay_geometry as geo
from . import overlay_paint as paint
from .motion import EASING, policy
from .overlay_layout import (
    PAIR_GAP,
    OverlayFonts,
    PairLayout,
    build_notice,
    build_pair,
    preview_segments,
)
from .overlay_pill import ControlPill
from .tokens import DURATION, SPACE, overlay_style

log = logging.getLogger(__name__)

#: States whose detail is worth painting when there is nothing else to show.
_NOTICE_STATES = (HealthState.SILENT, HealthState.SOUND_NO_SPEECH, HealthState.DELAYED)


class OverlayState(StrEnum):
    DISABLED = "disabled"
    WAITING = "waiting"
    VISIBLE = "visible"
    IDLE = "idle"
    PREVIEW = "preview"
    STARTING = "starting"
    DOWNLOADING = "downloading"
    ERROR = "error"


class CaptionOverlay(QWidget):
    """Frameless, translucent, always-on-top caption panel."""

    style_change_requested = Signal()
    hidden_by_user = Signal()
    scrub_changed = Signal(int)
    geometry_saved = Signal()
    state_changed = Signal(object)
    visibility_failed = Signal()
    subtitle_presented = Signal(object)  # actual painted text; trace recording is opt-in
    """Placement was written into ``settings.overlay.geometry``; persist it."""

    RING_SIZE = 50
    HOVER_DWELL_MS = 400
    SCRUB_IDLE_MS = 8000
    TOPMOST_TICK_MS = 2000
    EDGE_ZONE = 8
    MIN_WIDTH = 320
    PILL_ZONE = ControlPill.HEIGHT + SPACE["xs"]

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        # A caption window must remain independent of main-window minimization.
        super().__init__(None)
        self._settings = settings
        self._style = overlay_style(settings.overlay.preset, settings.overlay)
        self._fonts = OverlayFonts(self._style)

        self._ring: deque[CaptionSegment] = deque(maxlen=self.RING_SIZE)
        self._scrub_offset = 0
        self._pairs: list[PairLayout] = []
        self._caption_pages: dict[int, int] = {}
        self._painted_text: dict[int, tuple] = {}
        self._retiring: PairLayout | None = None
        self._notice: PairLayout | None = None
        self._entering_id: int | None = None
        self._health: HealthReport | None = None

        self._preview = False
        self._restoring_geometry = False
        self._state = OverlayState.DISABLED
        self._last_painted_id = None
        self._native_warning = False
        self._pinned = False
        self._auto_hidden = False
        self._enable_acknowledgment = False
        self._drag_origin: QPoint | None = None
        self._press_geometry: QRect | None = None
        self._resize_edge = 0

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, settings.overlay.always_on_top)
        self.setWindowTitle(f"{APP_NAME} captions")

        self._pill = ControlPill(self)
        self._pill.pin_toggled.connect(self._on_pin)
        self._pill.scrub_back_requested.connect(self.scrub_back)
        self._pill.scrub_forward_requested.connect(self.scrub_forward)
        self._pill.live_requested.connect(self.go_live)
        self._pill.style_requested.connect(self.style_change_requested)
        self._pill.hide_requested.connect(self._on_hide_pressed)

        self._hover_timer = _timer(
            self, self.HOVER_DWELL_MS, lambda: self._pill.reveal(animated=self._style.animate)
        )
        self._auto_hide_timer = _timer(self, 4000, self._sleep)
        self._page_timer = _timer(self, 1500, self._advance_caption_pages)
        self._scrub_idle_timer = _timer(self, self.SCRUB_IDLE_MS, self.go_live)
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(self.TOPMOST_TICK_MS)
        self._topmost_timer.timeout.connect(self._apply_topmost)

        self._window_fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._window_fade.setEasingCurve(EASING)
        self._enter_fade = _fade(self, DURATION["fast"], self._on_enter_value)
        self._exit_fade = _fade(self, DURATION["base"], self._on_exit_value)
        self._exit_fade.finished.connect(self._on_exit_done)
        policy().changed.connect(self._motion_changed)

        self.resize(self.MIN_WIDTH, self._chrome_height())
        self.restore_geometry()
        QGuiApplication.instance().screenRemoved.connect(self._screen_removed)
        self._preferred_screen = settings.overlay.screen_name
        self.setWindowOpacity(settings.overlay.opacity)
        self.set_click_through(settings.overlay.click_through)
        self._watched_screens = weakref.WeakSet()
        self._watched_window = None
        for screen in QGuiApplication.screens():
            self._watch_screen(screen)
        QGuiApplication.instance().screenAdded.connect(self._watch_screen)
        self._watch_window()
        log.debug("Overlay created flags=%s", int(self.windowFlags()))

    @property
    def state(self) -> OverlayState:
        return self._state

    def _report_state(self) -> None:
        state = (
            OverlayState.DISABLED
            if not self.isVisible()
            else OverlayState.PREVIEW
            if self._preview
            else OverlayState.IDLE
            if self._auto_hidden
            else OverlayState.STARTING
            if self._health
            and self._health.state is HealthState.STARTING
            else OverlayState.DOWNLOADING
            if self._health and self._health.state is HealthState.DOWNLOADING
            else OverlayState.ERROR
            if self._health and self._health.state is HealthState.ERROR
            else OverlayState.VISIBLE
            if self._pairs
            else OverlayState.WAITING
        )
        if state != self._state:
            self._state = state
            log.debug(
                "Overlay state=%s mapped=%s opacity=%.2f",
                state,
                self.isVisible(),
                self.windowOpacity(),
            )
            self.state_changed.emit(state)

    def set_enabled(self, enabled: bool) -> None:
        """Apply the requested preference to the live native window immediately."""
        log.debug("Overlay requested enabled=%s", enabled)
        self._settings.overlay.visible = enabled
        if enabled:
            self._enable_acknowledgment = True
            self._relayout()
            self._wake()
        else:
            self._preview = False
            self._auto_hide_timer.stop()
            self._window_fade.stop()
            self.hide()
        self._report_state()

    def _watch_screen(self, screen) -> None:
        if screen not in self._watched_screens:
            self._watched_screens.add(screen)
            screen.availableGeometryChanged.connect(self._display_metrics_changed)
            screen.logicalDotsPerInchChanged.connect(self._display_metrics_changed)

    def _watch_window(self) -> None:
        handle = self.windowHandle()
        if handle is not None and handle is not self._watched_window:
            self._watched_window = handle
            handle.screenChanged.connect(self._display_metrics_changed)

    def _display_metrics_changed(self, *_args) -> None:
        area = self.screen().availableGeometry()
        self.setMinimumWidth(min(self.MIN_WIDTH, area.width()))
        self.setGeometry(geo.clamp_rect(self.geometry(), area))
        self._fonts = OverlayFonts(self._style)
        self._drop_retiring()
        self._relayout()

    def _screen_removed(self, screen) -> None:
        if not geo.is_on_a_screen(self.geometry()):
            self.restore_geometry()

    def move_to_screen(self, name: str) -> None:
        screen = next(
            (screen for screen in QGuiApplication.screens() if screen.name() == name), None
        )
        if screen is None:
            return
        self.save_geometry()
        self._settings.overlay.screen_name = name
        self._preferred_screen = name
        self.setScreen(screen)
        self.restore_geometry()
        self.save_geometry()

    # ---------- captions ----------

    def show_segment(self, seg: CaptionSegment) -> None:
        """Append a finished caption and wake the panel."""
        self._preview = False
        self._enable_acknowledgment = False
        self._ring.append(seg)
        self._page_timer.stop()  # give new visible text a full reading interval
        retained = {item.id for item in self._ring}
        self._caption_pages = {
            key: page for key, page in self._caption_pages.items() if key in retained
        }
        self._painted_text = {
            key: value for key, value in self._painted_text.items() if key in retained
        }
        if self._scrub_offset:  # hold the reader's window still while scrubbing
            self._scrub_offset = min(self._scrub_offset + 1, self._max_scrub())
        self._relayout(entering=seg.id)
        self._wake()
        log.debug(
            "Overlay subtitle received id=%s translated=%s mapped=%s",
            seg.id,
            bool(seg.display_translation),
            self.isVisible(),
        )

    def update_segment(self, seg: CaptionSegment) -> None:
        """Replace a caption in place — how a late translation arrives."""
        for index, existing in enumerate(self._ring):
            if existing.id == seg.id:
                if (
                    existing.text != seg.text
                    or existing.display_translation != seg.display_translation
                ):
                    self._caption_pages[seg.id] = 0
                    self._page_timer.stop()
                self._ring[index] = seg
                break
        else:
            return
        self._relayout()
        if any(pair.segment_id == seg.id for pair in self._pairs):
            self._wake()

    def clear(self) -> None:
        self._ring.clear()
        self._caption_pages.clear()
        self._painted_text.clear()
        self._page_timer.stop()
        self._scrub_offset = 0
        self._drop_retiring()
        self._pill.set_scrubbing(False)
        self._relayout()

    def set_health(self, report: HealthReport) -> None:
        self._health = report
        self._relayout()
        if self._notice is not None:
            self._wake()
        self._report_state()

    # ---------- appearance ----------

    def apply_settings(self, settings: Settings) -> None:
        topmost = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if topmost != settings.overlay.always_on_top:
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, settings.overlay.always_on_top)
            if visible:
                self.show()
        if not settings.overlay.always_on_top:
            self._topmost_timer.stop()
        elif self.isVisible():
            self._topmost_timer.start()
        if settings.overlay.screen_name != self._preferred_screen:
            self._preferred_screen = settings.overlay.screen_name
            self.move_to_screen(settings.overlay.screen_name)
        self._settings = settings
        self._style = overlay_style(settings.overlay.preset, settings.overlay)
        self._fonts = OverlayFonts(self._style)
        self._drop_retiring()  # it was laid out with the old fonts
        self.set_click_through(settings.overlay.click_through)
        self._relayout()
        if not self._auto_hidden:
            self.setWindowOpacity(max(0.2, min(1.0, settings.overlay.opacity)))
        self._restart_auto_hide()
        if not settings.overlay.visible and not self._preview:
            self.hide()
        elif not self.isVisible() or (self._auto_hidden and not settings.overlay.auto_hide):
            self._wake()
        self._report_state()

    def set_click_through(self, enabled: bool) -> None:
        """Let clicks reach whatever is underneath, and mark the panel so."""
        self._settings.overlay.click_through = enabled
        self._sync_mouse_transparency()
        self.update()

    def show_placement_preview(self, enabled: bool) -> None:
        """Show sample captions so settings and onboarding can place the panel."""
        self._preview = enabled
        self._relayout()
        if enabled:
            self._wake()
        elif not self._settings.overlay.visible:
            self.hide()
        else:
            self._wake()

    @property
    def caption_style(self):
        """The resolved ``OverlayStyle`` being painted (``style`` belongs to Qt)."""
        return self._style

    @property
    def visible_pairs(self) -> list[PairLayout]:
        return list(self._pairs)

    @property
    def auto_hide_pending(self) -> bool:
        return self._auto_hide_timer.isActive()

    # ---------- scrubbing ----------

    def scrub_back(self) -> None:
        self._set_scrub(self._scrub_offset + 1)

    def scrub_forward(self) -> None:
        self._set_scrub(self._scrub_offset - 1)

    def go_live(self) -> None:
        self._set_scrub(0)

    @property
    def scrub_offset(self) -> int:
        """Pairs behind live; 0 means the newest caption is on screen."""
        return self._scrub_offset

    def _set_scrub(self, offset: int) -> None:
        offset = max(0, min(offset, self._max_scrub()))
        if offset == self._scrub_offset:
            return
        self._scrub_offset = offset
        self._pill.set_scrubbing(bool(offset))
        self._relayout()
        self._wake()
        self.scrub_changed.emit(offset)

    def _max_scrub(self) -> int:
        return max(0, len(self._ring) - 1)

    # ---------- geometry ----------

    def save_geometry(self) -> None:
        screen = self.screen()
        if screen is not None:
            self._settings.overlay.screen_name = screen.name()
            self._preferred_screen = screen.name()
        rect = self.geometry()
        self._settings.overlay.geometry[geo.screen_signature(self)] = [
            rect.x(),
            rect.y(),
            rect.width(),
            rect.height(),
        ]
        self.geometry_saved.emit()

    def restore_geometry(self) -> None:
        """Restore this display's placement, or fall back to a visible default."""
        preferred = self._settings.overlay.screen_name
        screen = next((s for s in QGuiApplication.screens() if s.name() == preferred), None)
        if screen is not None:
            self.setScreen(screen)
        area = self.screen().availableGeometry()
        self.setMinimumWidth(min(self.MIN_WIDTH, area.width()))
        saved = self._settings.overlay.geometry.get(geo.screen_signature(self))
        rect = geo.rect_from(saved, self.MIN_WIDTH)
        if rect is None or not geo.is_on_a_screen(rect):
            rect = geo.default_rect(self, self.MIN_WIDTH, max(self.height(), self._chrome_height()))
        self.setGeometry(geo.clamp_rect(rect, area))
        self._restoring_geometry = True
        try:
            self._relayout()
        finally:
            self._restoring_geometry = False

    def _chrome_height(self) -> int:
        return self.PILL_ZONE + self._style.pad_v * 2

    # ---------- layout ----------

    def _relayout(self, entering: int | None = None) -> None:
        width = max(self.minimumWidth(), self.width()) - self._style.pad_h * 2
        position = self._settings.overlay.original_position
        reserve = self._settings.translation_enabled
        segments = (
            preview_segments(reserve, self._settings.overlay.max_pairs)
            if self._preview
            else self._visible_segments()
        )
        area = self.screen().availableGeometry()
        line_height = (
            max(
                self._fonts.for_role("translation")[1].height(),
                self._fonts.for_role("original")[1].height(),
            )
            * self._style.line_spacing
        )
        roles = 2 if reserve and position.value != "hidden" else 1
        budget = max(
            1, int((area.height() - self._chrome_height() - PAIR_GAP) / max(1, line_height * roles))
        )
        viewport_style = replace(self._style, max_lines=min(self._style.max_lines, budget))
        pairs = [
            build_pair(
                seg,
                viewport_style,
                self._fonts,
                width,
                position,
                reserve,
                page=None if self._preview else self._caption_pages.get(seg.id, 0),
            )
            for seg in segments
        ]
        while len(pairs) > 1 and (
            sum(pair.height for pair in pairs) + PAIR_GAP * (len(pairs) - 1)
            > area.height() - self._chrome_height()
        ):
            pairs.pop(0)
        if entering is not None:
            self._retire(pairs)
            self._start_enter(entering)
        elif self._retiring is not None and self._retiring.segment_id in {
            pair.segment_id for pair in pairs
        }:
            # Scrubbing back can bring the fading pair into view again; two
            # copies of one caption is worse than a fade cut short.
            self._drop_retiring()
        if self._enter_fade.state() is QAbstractAnimation.State.Running:
            value = self._enter_fade.currentValue()
            for pair in pairs:
                if pair.segment_id == self._entering_id and value is not None:
                    pair.opacity = float(value)
        self._pairs = pairs
        pending_pages = [pair for pair in pairs if pair.page_index + 1 < pair.page_count]
        if pending_pages and not self._preview and self._settings.overlay.visible:
            if not self._page_timer.isActive():
                chars = max(sum(len(line.text) for line in pair.lines) for pair in pending_pages)
                self._page_timer.start(max(800, min(2500, chars * 45)))
        else:
            self._page_timer.stop()
        text = self._notice_text()
        self._notice = build_notice(text, self._style, self._fonts, width) if text else None
        self._apply_height()
        self.update()

    def _visible_segments(self) -> list[CaptionSegment]:
        items = list(self._ring)
        end = len(items) - self._scrub_offset
        start = max(0, end - max(1, self._settings.overlay.max_pairs))
        return items[start:end]

    def _advance_caption_pages(self) -> None:
        self._page_timer.stop()  # recompute dwell for the next page's text
        for pair in self._pairs:
            if pair.page_index + 1 < pair.page_count:
                self._caption_pages[pair.segment_id] = pair.page_index + 1
        self._relayout()
        self._wake()

    def _render_pairs(self) -> list[PairLayout]:
        """Everything the painter draws, oldest first."""
        if self._notice is not None:
            return [self._notice]
        pairs = list(self._pairs)
        if self._retiring is not None:
            pairs.insert(0, self._retiring)
        return pairs

    def _apply_height(self) -> None:
        pairs = self._render_pairs()
        content = sum(pair.height for pair in pairs) + PAIR_GAP * max(0, len(pairs) - 1)
        height = round(self._chrome_height() + max(0.0, content))
        if height != self.height():
            rect = self.geometry()
            # Preserve the subtitle's bottom anchor as wrapping/translation
            # changes its height. Repeated clamping of a fixed top edge made
            # the panel creep upwards with every long phrase.
            top = rect.y() if self._restoring_geometry else rect.bottom() + 1 - height
            rect = QRect(rect.x(), top, max(self.minimumWidth(), self.width()), height)
            self.setGeometry(geo.clamp_rect(rect, self.screen().availableGeometry()))

    def _notice_text(self) -> str:
        report = self._health
        if self._preview:
            return ""
        state = report.state if report else HealthState.OFF
        if state in (HealthState.OFF, HealthState.LISTENING) and not self._enable_acknowledgment:
            return ""
        if self._pairs and state not in (
            HealthState.STARTING,
            HealthState.DOWNLOADING,
            HealthState.ERROR,
        ):
            return ""
        return {
            HealthState.OFF: "Overlay on · Start captions to begin.",
            HealthState.STARTING: "Starting captions… You can stop from Live.",
            HealthState.DOWNLOADING: "Getting the speech model… See progress on Live.",
            HealthState.LISTENING: "Listening for speech…",
            HealthState.ERROR: "Captions stopped. Open Live to fix the error and retry.",
        }.get(state, report.detail if report and state in _NOTICE_STATES else "")

    # ---------- painting ----------

    def paintEvent(self, event) -> None:
        pairs = self._render_pairs()
        if not pairs:
            return  # nothing to say and nothing to explain: stay invisible
        panel = QRectF(
            0.0,
            float(self.PILL_ZONE),
            float(self.width()),
            float(self.height() - self.PILL_ZONE),
        )
        painter = QPainter(self)
        paint.paint_overlay(
            painter,
            self._style,
            pairs,
            panel,
            self._settings.overlay.click_through,
            bool(self._scrub_offset),
        )
        painter.end()
        newest = pairs[-1].segment_id
        if newest != self._last_painted_id:
            self._last_painted_id = newest
            log.debug("Overlay subtitle rendered id=%s mapped=%s", newest, self.isVisible())
        for pair in pairs:
            segment = next((item for item in self._ring if item.id == pair.segment_id), None)
            if segment is None:
                continue
            signature = tuple((line.role, line.text) for line in pair.lines)
            if self._painted_text.get(segment.id) != signature:
                self._painted_text[segment.id] = signature
                self.subtitle_presented.emit(
                    {
                        "segment": segment,
                        "lines": [{"role": role, "text": text} for role, text in signature],
                        "page": pair.page_index,
                        "visible": self.isVisible(),
                    }
                )

    # ---------- visibility ----------

    def _wake(self) -> None:
        if not self._settings.overlay.visible and not self._preview:
            return
        self._auto_hidden = False
        # Opacity alone cannot map a hidden QWidget. This is also the recovery
        # path after stopping a session, closing setup, or changing window flags.
        if not self.isVisible():
            self._window_fade.stop()
            self.setWindowOpacity(max(0.2, min(1.0, self._settings.overlay.opacity)))
            self.show()
        if (
            window_effects.IS_WINDOWS
            and QGuiApplication.platformName() == "windows"
            and not window_effects.show_overlay(int(self.winId()))
        ):
            self._settings.overlay.visible = False
            self.hide()
            log.warning("Windows could not map the subtitle window")
            self.visibility_failed.emit()
            return
        self._sync_mouse_transparency()
        self._fade_window(max(0.2, min(1.0, self._settings.overlay.opacity)), DURATION["base"])
        self._apply_topmost()
        self._restart_auto_hide()
        self._report_state()

    def _sleep(self) -> None:
        if self._pinned or self._preview or self.underMouse():
            return
        # Stay mapped at zero opacity: unmapping surrenders the topmost slot,
        # and winning it back over a game is the fight we are trying to avoid.
        self._auto_hidden = True
        self._enable_acknowledgment = False
        self._relayout()
        self._sync_mouse_transparency()
        self._fade_window(0.0, DURATION["gentle"])
        self._report_state()

    def _restart_auto_hide(self) -> None:
        self._auto_hide_timer.stop()
        overlay = self._settings.overlay
        persistent_notice = self._notice is not None and not (
            self._health and self._health.state is HealthState.LISTENING
        )
        if not overlay.auto_hide or self._pinned or self._preview or persistent_notice:
            return
        self._auto_hide_timer.start(int(overlay.auto_hide_seconds * 1000))

    def _fade_window(self, target: float, duration: int) -> None:
        self._window_fade.stop()
        if not self._animations_enabled():
            self.setWindowOpacity(target)
            return
        self._window_fade.setDuration(duration)
        self._window_fade.setStartValue(self.windowOpacity())
        self._window_fade.setEndValue(target)
        self._window_fade.start()

    def _animations_enabled(self) -> bool:
        return (
            self._style.animate
            and not policy().reduced
            and not self._settings.general.reduce_motion
        )

    def _motion_changed(self) -> None:
        if self._animations_enabled():
            return
        if self._window_fade.state() == QAbstractAnimation.State.Running:
            target = self._window_fade.endValue()
            self._window_fade.stop()
            self.setWindowOpacity(float(target))
        self._enter_fade.stop()
        self._drop_retiring()
        for pair in self._pairs:
            pair.opacity = 1.0
        self._apply_height()
        self.update()

    def _sync_mouse_transparency(self) -> None:
        """An auto-hidden panel is still mapped, so it must stop eating clicks."""
        ignore = self._settings.overlay.click_through or self._auto_hidden
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, ignore)
        if window_effects.IS_WINDOWS:
            window_effects.set_input_transparent(int(self.winId()), ignore)
        else:
            # WA_TransparentForMouseEvents only affects Qt's own event routing.
            visible = self.isVisible()
            if bool(self.windowFlags() & Qt.WindowType.WindowTransparentForInput) != ignore:
                self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, ignore)
                if visible:
                    self.show()

    def _on_hide_pressed(self) -> None:
        self._settings.overlay.visible = False
        self.hide()
        self.hidden_by_user.emit()

    def _on_pin(self, pinned: bool) -> None:
        self._pinned = pinned
        self._wake()

    # ---------- pair fades ----------

    def _retire(self, pairs: list[PairLayout]) -> None:
        """Fade the pair that just fell off the top instead of blinking it away."""
        keep = {pair.segment_id for pair in pairs}
        dropped = [pair for pair in self._pairs if pair.segment_id not in keep]
        if not dropped or not self._animations_enabled():
            self._drop_retiring()
            return
        self._retiring = dropped[0]
        total = sum(pair.height for pair in pairs) + self._retiring.height + PAIR_GAP * len(pairs)
        if total > self.screen().availableGeometry().height() - self._chrome_height():
            self._drop_retiring()
            return
        self._exit_fade.stop()
        self._exit_fade.start()

    def _drop_retiring(self) -> None:
        self._exit_fade.stop()
        self._retiring = None

    def _start_enter(self, segment_id: int) -> None:
        if not self._animations_enabled():
            return
        self._entering_id = segment_id
        self._enter_fade.stop()
        self._enter_fade.start()

    def _on_enter_value(self, value) -> None:
        for pair in self._pairs:
            if pair.segment_id == self._entering_id:
                pair.opacity = float(value)
        self.update()

    def _on_exit_value(self, value) -> None:
        if self._retiring is not None:
            self._retiring.opacity = 1.0 - float(value)
            self.update()

    def _on_exit_done(self) -> None:
        self._retiring = None
        self._apply_height()
        self.update()

    # ---------- window events ----------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._watch_window()
        if not geo.is_on_a_screen(self.geometry()):  # a display left since last run
            self.restore_geometry()
        self._apply_topmost()
        if self._settings.overlay.always_on_top:
            self._topmost_timer.start()
        self._sync_mouse_transparency()
        screen_index = QGuiApplication.screens().index(self.screen()) + 1
        rect = self.geometry()
        log.debug(
            "Overlay shown display=%s geometry=%s,%s,%s,%s dpr=%.2f flags=%s",
            screen_index,
            rect.x(),
            rect.y(),
            rect.width(),
            rect.height(),
            self.devicePixelRatioF(),
            int(self.windowFlags()),
        )
        self._report_state()

    def hideEvent(self, event) -> None:
        self._topmost_timer.stop()
        self._pill.conceal(animated=False)
        super().hideEvent(event)
        self._report_state()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._pill.move(max(0, self.width() - self._pill.width() - self._style.pad_h), 0)

    def _apply_topmost(self) -> None:
        """Re-assert always-on-top: a game takes the top slot when it focuses."""
        if (
            not self._settings.overlay.always_on_top
            or not self.isVisible()
            or (window_effects.IS_WINDOWS and QGuiApplication.platformName() != "windows")
        ):
            return
        try:
            ok = window_effects.set_topmost(int(self.winId()))
            if window_effects.IS_WINDOWS and not ok and not self._native_warning:
                self._native_warning = True
                log.warning("Overlay native topmost request failed; Qt topmost remains enabled")
        except Exception:
            log.debug("Could not re-assert always-on-top", exc_info=True)

    # ---------- mouse ----------

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._scrub_idle_timer.stop()
        self._auto_hide_timer.stop()
        self._hover_timer.start()
        if self._auto_hidden:
            self._wake()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover_timer.stop()
        self._pill.conceal(animated=self._style.animate)
        if self._scrub_offset:
            self._scrub_idle_timer.start()
        self._restart_auto_hide()

    def mousePressEvent(self, event) -> None:
        if event.button() is not Qt.MouseButton.LeftButton:
            return
        self._drag_origin = event.globalPosition().toPoint()
        self._press_geometry = QRect(self.geometry())
        self._resize_edge = self._edge_at(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:
        origin, start = self._drag_origin, self._press_geometry
        if origin is None or start is None:
            edge = self._edge_at(event.position().toPoint())
            self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.OpenHandCursor)
            return
        delta = event.globalPosition().toPoint() - origin
        if self._resize_edge:
            self.setGeometry(geo.resized(start, self._resize_edge, delta.x(), self.MIN_WIDTH))
            self._relayout()
        else:
            self.move(start.topLeft() + delta)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin is None:
            return
        self._drag_origin = None
        self._press_geometry = None
        self._resize_edge = 0
        self.save_geometry()

    def _edge_at(self, pos: QPoint) -> int:
        return geo.edge_at(pos, self.width(), self.PILL_ZONE, self.EDGE_ZONE)


# ---------- helpers ----------


def _timer(parent: QWidget, interval: int, slot) -> QTimer:
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval)
    timer.timeout.connect(slot)
    return timer


def _fade(parent: QWidget, duration: int, slot) -> QVariantAnimation:
    animation = QVariantAnimation(parent)
    animation.setDuration(duration)
    animation.setEasingCurve(EASING)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.valueChanged.connect(slot)
    return animation
