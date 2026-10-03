"""Application bootstrap and wiring.

This module is the only place that knows about every layer at once. It owns the
policies that span them:

- the model is downloaded *before* captioning starts, with visible progress,
  while the interface stays responsive;
- captions and translation degrade independently, and every failure becomes a
  banner with an action rather than a modal dialog;
- the pipeline receives an immutable snapshot of settings, so changing a
  setting mid-session can never mutate a running worker.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, branding, paths
from .asr.engine import EngineError, default_cache
from .asr.models import DownloadProgress, ModelManager
from .audio.base import AudioCaptureError, AudioDevice, create_capture, resolve_device
from .core import logging_setup, secrets
from .core.events import CaptionSegment
from .core.health import HealthReport, HealthState
from .core.settings import (
    IntelligenceSettings,
    ProcessingSettings,
    SessionConfig,
    Settings,
    SettingsStore,
    from_dict,
)
from .pipeline import Captioner
from .storage import SessionStore
from .system.foreground import foreground_app_name
from .system.hotkeys import HotkeyManager
from .system.single_instance import SingleInstance
from .translation.chain import TranslationChain
from .ui import theme
from .ui.live_view import LiveView
from .ui.overlay import CaptionOverlay
from .ui.settings_view import SettingsDeps, SettingsView
from .ui.shell import Shell
from .ui.tray import Tray

log = logging.getLogger(__name__)

_LIVE, _HISTORY, _SETTINGS = 0, 1, 2


class Controller(QObject):
    """Owns the objects and the conversations between them."""

    download_progress = Signal(object)
    # Emitted (from the download worker thread) when a model finishes
    # downloading. Qt signals are the one cross-thread hop that works here:
    # QTimer.singleShot from a plain threading.Thread has no event dispatcher
    # to fire on.
    download_finished = Signal()
    shutdown_ready = Signal()
    devices_changed = Signal()
    capability_ready = Signal(object)

    def __init__(self, app: QApplication, store: SettingsStore):
        super().__init__()
        self.app = app
        self.store = store
        self.settings: Settings = store.settings

        self.sessions = SessionStore()
        self.sessions.start_writer()
        self.models = ModelManager()
        self.engines = default_cache()

        self.captioner = Captioner(
            engine_provider=self._provide_engine,
            translation_provider=self._provide_translator,
        )
        self.overlay = CaptionOverlay(self.settings)
        self.shell = Shell(self.settings)
        self.live = LiveView(self.settings)
        self.tray = Tray(self.shell)
        self.hotkeys = HotkeyManager(self)

        self._session_id: int | None = None
        self._download_thread: threading.Thread | None = None
        self._download_cancel = threading.Event()
        self._segment_count = 0
        self._word_count = 0
        self._devices: list[AudioDevice] = []
        self._quitting = False
        self._pending_restart = False
        self._download_requested = False
        self._watcher = None
        self._capability = None

        self._build_surfaces()
        self._connect()
        self._apply_retention()

    # ---------- construction ----------

    def _build_surfaces(self) -> None:
        from .ui.history_view import HistoryView
        from .ui.transcript_view import TranscriptView

        self.history = HistoryView(self.sessions)
        self.transcript = TranscriptView(self.sessions)
        self.settings_view = SettingsView(
            self.store,
            SettingsDeps(
                audio_devices=self._list_devices,
                model_manager=self.models,
                # Left unset on purpose: the ASR layer detects and caches on first
                # use, so opening Settings costs the probe, not every cold start.
                capability=None,
                hotkeys=self.hotkeys,
                session_store=self.sessions,
                on_rerun_onboarding=self.run_onboarding,
                # Without these two the key a user types is discarded, while
                # the UI and the docs both claim it went to the OS keychain.
                api_key_get=secrets.get_key,
                api_key_set=secrets.set_key,
                api_key_delete=secrets.delete_key,
                release_models=self.release_models,
                diagnostics=self._diagnostics,
            ),
        )
        # History pushes to the transcript viewer in place, so the shell keeps
        # exactly three destinations.
        from PySide6.QtWidgets import QStackedWidget

        self._history_stack = QStackedWidget()
        self._history_stack.addWidget(self.history)
        self._history_stack.addWidget(self.transcript)

        self.shell.add_surface(self.live)
        self.shell.add_surface(self._history_stack)
        self.shell.add_surface(self.settings_view)

    def _connect(self) -> None:
        captioner = self.captioner
        captioner.segment_ready.connect(self._on_segment)
        captioner.segment_updated.connect(self._on_segment_updated)
        captioner.health_changed.connect(self._on_health)
        captioner.status_message.connect(self._on_status)
        captioner.error_occurred.connect(self._on_error)
        captioner.stopped.connect(self._on_stopped)
        captioner.phase_changed.connect(self._on_pipeline_phase)
        captioner.device_opened.connect(lambda _name: log.info("Audio capture opened"))

        self.live.start_requested.connect(self.start_captions)
        self.live.stop_requested.connect(self.stop_captions)
        self.live.translation_toggled.connect(self._on_translation_toggled)
        self.live.show_original_toggled.connect(self._on_show_original)
        self.live.overlay_toggled.connect(self._on_overlay_toggled)
        self.live.click_through_toggled.connect(self._on_click_through)
        self.live.style_requested.connect(lambda: self._open_settings("captions"))
        self.live.banner_action.connect(self._on_banner_action)

        self.overlay.geometry_saved.connect(lambda: self.store.notify("overlay"))
        self.overlay.hidden_by_user.connect(self._sync_overlay_controls)
        self.overlay.style_change_requested.connect(lambda: self._open_settings("captions"))

        self.history.session_opened.connect(self._open_transcript)
        self.history.segment_opened.connect(self._open_transcript)
        self.history.enable_history_requested.connect(self._enable_history)
        self.history.manage_storage_requested.connect(lambda: self._open_settings("general"))
        self.transcript.back_requested.connect(lambda: self._history_stack.setCurrentIndex(0))

        self.settings_view.restart_capture_requested.connect(self._restart_capture)
        self.settings_view.style_changed.connect(self._apply_overlay_settings)
        self.settings_view.preview_requested.connect(self.overlay.show_placement_preview)
        self.settings_view.shortcuts_changed.connect(self._register_hotkeys)
        self.settings_view.check_updates_requested.connect(self._check_updates)

        self.tray.toggle_captions.connect(self.toggle_captions)
        self.tray.toggle_overlay.connect(
            lambda: self._on_overlay_toggled(not self.settings.overlay.visible)
        )
        self.tray.toggle_click_through.connect(
            lambda: self._on_click_through(not self.settings.overlay.click_through)
        )
        self.tray.preset_selected.connect(self._on_preset_selected)
        self.tray.open_requested.connect(self.show_window)
        self.tray.quit_requested.connect(self.quit)

        self.shell.quit_requested.connect(self.quit)
        self.shell.close_to_tray_requested.connect(self._notify_tray_once)
        self.shell.segment_changed.connect(self._on_segment_changed)

        self.hotkeys.activated.connect(self._on_hotkey)
        self.store.subscribe(self._on_settings_changed)
        self.store.subscribe_errors(
            lambda message: self.live.banner.show_message(message, "warning")
        )
        self.download_progress.connect(self._on_download_progress)
        self.download_finished.connect(self._on_download_finished)
        self.shutdown_ready.connect(self._finish_quit)
        self.devices_changed.connect(self._on_devices_changed)
        self.capability_ready.connect(self._on_capability_ready)
        self.live.cancel_download_requested.connect(self.cancel_download)

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(100)
        self._level_timer.timeout.connect(self._tick_level)
        self._level_timer.start()

    # ---------- lifecycle ----------

    def start(self) -> None:
        theme.apply(self.app, self.settings.general.theme, self.settings.general.accent)
        self.tray.show()
        self.tray.set_preset(self.settings.overlay.preset)
        # Show first: _register_hotkeys warns via a banner when the shell is
        # visible, and registering before the show swallowed the warning.
        self.shell.show()
        self._watcher = create_capture()
        self._watcher.watch_devices(self.devices_changed.emit)
        from .asr.capability import detect

        def probe():
            self.capability_ready.emit(detect())

        threading.Thread(target=probe, name="subbyai-hardware", daemon=True).start()
        self._register_hotkeys(self.settings.shortcuts.globals)
        if self.settings.general.autostart_captions:
            QTimer.singleShot(400, self.start_captions)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._pending_restart = False
        self._level_timer.stop()
        self.cancel_download()
        self._download_cancel.set()
        self.captioner.stop()
        self.hotkeys.unregister_all()
        self.overlay.save_geometry()
        self.overlay.close()
        self.store.try_save()
        self.live.banner.show_message("Finishing the current phrase and closing safely…", "info")

        def shutdown():
            from .translation.builtin import shutdown_downloads

            shutdown_downloads()
            if self._watcher is not None:
                self._watcher.unwatch()
            while not self.captioner.wait(timeout=1):
                pass
            if self._download_thread is not None:
                self._download_thread.join()
            try:
                if self._session_id is not None:
                    self.sessions.end_session(self._session_id)
                    self._session_id = None
            except (OSError, TimeoutError, sqlite3.Error):
                log.exception("Could not finish transcript storage during shutdown")
            finally:
                self.sessions.close()
            from PySide6.QtCore import QThreadPool

            QThreadPool.globalInstance().waitForDone()
            self.shutdown_ready.emit()

        threading.Thread(target=shutdown, name="subbyai-shutdown", daemon=True).start()

    def _finish_quit(self) -> None:
        self.tray.hide()
        self.app.quit()

    def show_window(self) -> None:
        self.shell.show()
        self.shell.raise_()
        self.shell.activateWindow()

    # ---------- captioning ----------

    def toggle_captions(self) -> None:
        if self.captioner.is_active:
            self.stop_captions()
        else:
            self.start_captions()

    def start_captions(self) -> None:
        if self.captioner.is_active or self._quitting:
            return
        model = self.settings.model_name
        if self.settings.processing.asr_backend == "local" and not self.models.is_downloaded(model):
            self._start_download(model)
            return
        self._begin_session()

    def stop_captions(self) -> None:
        self._pending_restart = False
        self.cancel_download()
        self.captioner.stop()

    def _begin_session(self) -> None:
        # The gate lives here, not only in start_captions: the restart path
        # (_restart_capture -> stop -> _on_stopped -> _begin_session) comes
        # straight back in, so changing quality mid-session — or starting after
        # a model was removed — would otherwise reach _provide_engine and try
        # to fetch gigabytes on the pipeline worker.
        model = self.settings.model_name
        if self._quitting:
            return
        if self.settings.processing.asr_backend == "local" and not self.models.is_downloaded(model):
            self._start_download(model)
            return
        config = SessionConfig.from_settings(self.settings)
        try:
            device = resolve_device(
                self._list_devices(),
                self.settings.audio.device_id,
                self.settings.audio.device_name,
                strict=True,
            )
        except AudioCaptureError as exc:
            self._on_error(exc.message)
            return
        self.live.clear_segments()
        self.overlay.clear()
        self._segment_count = 0
        self._word_count = 0

        if not self.captioner.start(config, device):
            return
        if self.settings.history.enabled and self.settings.history.retention_days != -1:
            self._session_id = self.sessions.create_session(
                _session_title(),
                config.source_language,
                config.target_language,
            )
        self.live.set_running(True)
        if self.settings.overlay.visible and not self.settings.overlay.auto_hide:
            self.overlay.show()

    def _restart_capture(self) -> None:
        """Apply a setting that needs a fresh capture, without asking the user."""
        if not self.captioner.is_active:
            return
        self._pending_restart = True
        self.captioner.stop()

    # ---------- model download ----------

    def _start_download(self, model: str) -> None:
        if self._download_thread and self._download_thread.is_alive():
            return
        tier_label = _tier_label(self.settings)
        size_mb = self.models.estimated_size_mb(model)
        self.live.set_download_progress(
            0.0,
            f"The {tier_label} caption engine ({size_mb} MB) downloads once and then "
            "works completely offline. Nothing you play is ever uploaded.",
        )
        self.tray.set_health(HealthState.DOWNLOADING)
        self._download_cancel.clear()
        self._download_requested = True

        def run() -> None:
            ok = self.models.download(model, self.download_progress.emit, self._download_cancel)
            if ok:
                # Hop to the UI thread via a signal. QTimer.singleShot here
                # would run on this plain threading.Thread, which has no Qt
                # event dispatcher, so it would never fire.
                self.download_finished.emit()

        self._download_thread = threading.Thread(target=run, name="subbyai-download", daemon=True)
        self._download_thread.start()

    def _on_download_finished(self) -> None:
        if self._quitting or not self._download_requested or self._download_cancel.is_set():
            return
        self._download_requested = False
        self._begin_session()

    def cancel_download(self) -> None:
        self._download_requested = False
        self._download_cancel.set()
        self.live.hide_download_progress()

    def _on_download_progress(self, progress: DownloadProgress) -> None:
        if self._quitting or not self._download_requested:
            return
        if progress.phase == "cancelled":
            self.live.hide_download_progress()
            return
        if progress.phase == "error":
            self.live.hide_download_progress()
            self.live.banner.show_message(
                progress.message or "The download was interrupted. Check your connection.",
                "error",
                "Try again",
                "retry_download",
            )
            return
        if progress.phase == "done":
            self.live.hide_download_progress()
            return
        done_mb = progress.downloaded_bytes // (1024 * 1024)
        total_mb = max(1, progress.total_bytes // (1024 * 1024))
        self.live.set_download_progress(
            progress.fraction, f"Downloading the caption engine — {done_mb} MB of {total_mb} MB"
        )

    # ---------- pipeline events ----------

    def _on_segment(self, segment: CaptionSegment) -> None:
        if self._quitting:
            return
        self.overlay.show_segment(segment)
        self.live.add_segment(segment)
        self._segment_count += 1
        self._word_count += len(segment.text.split())
        self._update_ticker(segment)
        if self._session_id is not None and self.settings.history.enabled:
            self.sessions.add_segment(self._session_id, segment)

    def _on_segment_updated(self, segment: CaptionSegment) -> None:
        if self._quitting:
            return
        self.overlay.update_segment(segment)
        self.live.update_segment(segment)
        if self._session_id is not None and self.settings.history.enabled:
            self.sessions.update_segment(self._session_id, segment)

    def _on_health(self, report: HealthReport) -> None:
        self.live.set_health(report)
        self.shell.set_health(report)
        self.overlay.set_health(report)
        self.tray.set_health(report.state, _tier_label(self.settings))
        self._show_health_banner(report)

    def _show_health_banner(self, report: HealthReport) -> None:
        if report.state is HealthState.SILENT:
            self.live.banner.show_message(
                report.detail, "warning", "Choose audio source", "choose_device"
            )
        elif report.state is HealthState.DELAYED:
            self.live.banner.show_message(
                report.detail, "warning", "Use faster quality", "faster_quality"
            )
        elif report.state in (HealthState.LISTENING, HealthState.OFF):
            self.live.banner.hide()

    def _on_status(self, message: str) -> None:
        self.live.banner.show_message(message, "info")

    def _on_error(self, message: str) -> None:
        self.live.set_running(False)
        self.live.banner.show_message(message, "error", "Open settings", "open_settings")
        if not self.shell.isVisible():
            # Never raise a window over someone's game or call.
            self.tray.notify(APP_NAME, message)

    def _on_stopped(self) -> None:
        self.live.start_button.setEnabled(not self._quitting)
        self.live.set_running(False)
        self.overlay.clear()
        self.overlay.hide()
        if not self._quitting:
            self._end_session()
        if self._pending_restart and not self._quitting:
            self._pending_restart = False
            QTimer.singleShot(100, self._begin_session)

    def _on_pipeline_phase(self, phase) -> None:
        busy = phase.value in ("stopping", "failed") and self.captioner.has_workers
        self.live.start_button.setEnabled(not busy and not self._quitting)
        if phase.value == "stopping":
            self.live.start_button.setText("Finishing up…")

    def _end_session(self) -> None:
        if self._session_id is not None:
            session_id = self._session_id
            self._session_id = None
            try:
                self.sessions.end_session(session_id)
            except (OSError, TimeoutError, sqlite3.Error):
                log.exception("Could not finish transcript storage")
                self.live.banner.show_message(
                    "Your captions stopped, but transcript storage couldn't finish. "
                    "Check free disk space and try again.",
                    "warning",
                )
                return
            self.history.refresh()

    # ---------- providers ----------

    def _provide_engine(self, config: SessionConfig):
        processing = from_dict(ProcessingSettings, json.loads(config.processing_json))
        if processing.asr_backend == "remote":
            from .asr.remote import RemoteASR

            return RemoteASR(
                processing,
                secrets.get_key(secrets.endpoint_key_id("remote-asr", processing.base_url)),
            )
        engine = self.engines.get(
            config.model_name, config.compute_device, config.compute_type, config.cpu_threads
        )
        engine.beam_size = config.beam_size
        engine.load()
        return engine

    def release_models(self) -> None:
        """Unload the cached model so its files can be deleted.

        The cache deliberately holds the model open across stop/start so a
        restart does not pay the load again, which means stopping captions is
        not enough to release the file handle. On Windows that is the whole
        difference between a delete that works and one that raises.
        """
        if getattr(self.captioner, "has_workers", self.captioner.is_active):
            raise RuntimeError("Stop captions before removing a model.")
        self.engines.evict_all()

    def _provide_translator(self, config: SessionConfig):
        """Assemble endpoint-consented translators for this immutable session."""
        from .translation import build_chain
        from .translation.builtin import ArgosProvider

        intelligence = from_dict(IntelligenceSettings, json.loads(config.intelligence_json))
        chain = build_chain(intelligence, api_key_for=secrets.get_key)
        if not chain.providers:
            chain = TranslationChain([ArgosProvider()])
        # Packs prepare lazily in the translation lane; audio and ASR never wait.
        return chain

    # ---------- settings reactions ----------

    def _on_settings_changed(self, section: str) -> None:
        if section == "overlay":
            self._apply_overlay_settings()
        elif section == "general":
            theme.apply(self.app, self.settings.general.theme, self.settings.general.accent)
            self.settings_view.refresh_theme()
        elif section in ("captions", "audio", "intelligence", "processing"):
            self.live.sync_from_settings()
            self.settings_view.everyday.refresh()
        elif section == "history":
            if not self.settings.history.enabled:
                self._end_session()
            self._apply_retention()

    def _apply_overlay_settings(self) -> None:
        self.overlay.apply_settings(self.settings)
        self.tray.set_preset(self.settings.overlay.preset)
        self.tray.set_overlay_visible(self.settings.overlay.visible)
        self.tray.set_click_through(self.settings.overlay.click_through)
        self.live.sync_from_settings()

    def _on_translation_toggled(self, enabled: bool) -> None:
        from .languages import default_target_language

        if enabled and not self.settings.captions.target_language:
            self.settings.captions.target_language = default_target_language()
        elif not enabled:
            self.settings.captions.target_language = ""
        self.store.notify("captions")
        if self.captioner.is_active:
            self._restart_capture()

    def _on_show_original(self, enabled: bool) -> None:
        self.settings.captions.show_original = enabled
        self.store.notify("captions")
        self._apply_overlay_settings()

    def _on_overlay_toggled(self, visible: bool) -> None:
        self.settings.overlay.visible = visible
        self.overlay.setVisible(visible)
        self.store.notify("overlay")
        self._sync_overlay_controls()

    def _on_click_through(self, enabled: bool) -> None:
        self.overlay.set_click_through(enabled)
        self.store.notify("overlay")
        self._sync_overlay_controls()

    def _on_preset_selected(self, preset) -> None:
        self.settings.overlay.preset = preset
        self.store.notify("overlay")

    def _sync_overlay_controls(self) -> None:
        self.live.sync_from_settings()
        self.tray.set_overlay_visible(self.settings.overlay.visible)
        self.tray.set_click_through(self.settings.overlay.click_through)

    def _enable_history(self) -> None:
        self.settings.history.enabled = True
        self.store.notify("history")
        self.history.set_history_enabled(True)
        self.history.refresh()

    # ---------- shortcuts ----------

    def _register_hotkeys(self, bindings: dict[str, str]) -> None:
        failures = self.hotkeys.register_all(bindings)
        for action, reason in failures.items():
            log.info("Shortcut for %s unavailable: %s", action, reason)
        if failures and self.shell.isVisible():
            first = next(iter(failures.values()))
            self.live.banner.show_message(first, "warning", "Change shortcut", "open_shortcuts")

    def _on_hotkey(self, action: str) -> None:
        actions = {
            "toggle_captions": self.toggle_captions,
            "toggle_overlay": lambda: self._on_overlay_toggled(not self.settings.overlay.visible),
            "toggle_click_through": lambda: self._on_click_through(
                not self.settings.overlay.click_through
            ),
            "toggle_translation": lambda: self._on_translation_toggled(
                not self.settings.captions.target_language
            ),
            "caption_bigger": lambda: self._resize_captions(2),
            "caption_smaller": lambda: self._resize_captions(-2),
            "scrub_back": self.overlay.scrub_back,
            "scrub_forward": self.overlay.scrub_forward,
        }
        handler = actions.get(action)
        if handler:
            handler()

    def _resize_captions(self, delta: int) -> None:
        size = max(10, min(72, self.settings.overlay.font_size + delta))
        self.settings.overlay.font_size = size
        self.store.notify("overlay")

    # ---------- misc ----------

    def _diagnostics(self) -> dict:
        return {
            "phase": self.captioner.phase.value,
            "processing": self.settings.processing.asr_backend,
            "pipeline": asdict(self.captioner.metrics),
            "history_pending": self.sessions.pending_count,
            "history_dropped": self.sessions.dropped_writes,
        }

    def _on_capability_ready(self, caps) -> None:
        if self._quitting:
            return
        self._capability = caps
        everyday = self.settings_view.everyday
        everyday.capability = caps
        everyday.recommendation.setText(
            "Your graphics card can help with fast local subtitles. Balanced is a good start."
            if caps.has_cuda
            else "Local subtitles use your processor. "
            "Fast is a good choice on a lightweight laptop."
        )
        self.settings_view.intelligence.quality._capability = caps
        self.settings_view.intelligence.quality.refresh_states()

    def _on_devices_changed(self) -> None:
        if self._quitting:
            return
        devices = self._list_devices()
        try:
            selected = resolve_device(
                devices, self.settings.audio.device_id, self.settings.audio.device_name, strict=True
            )
        except AudioCaptureError:
            if self.captioner.is_active:
                self._recover_device = True
                self.captioner.stop()
                self._on_status(
                    "Your audio output disconnected. Reconnect it or choose another source."
                )
            return
        if selected is not None:
            if self.captioner.is_active:
                self._restart_capture()
            elif getattr(self, "_recover_device", False):
                self._recover_device = False
                if self.captioner.has_workers:
                    self._pending_restart = True
                else:
                    self.start_captions()
        self.settings_view.audio.refresh()
        self.settings_view.everyday.refresh_devices(devices)

    def _list_devices(self) -> list[AudioDevice]:
        try:
            self._devices = create_capture().list_devices()
        except AudioCaptureError as exc:
            log.warning("Could not list audio devices: %s", exc)
            self._devices = []
        return self._devices

    def _tick_level(self) -> None:
        report = self.captioner.health
        self.live.level_meter.set_level(report.level, report.state is HealthState.LISTENING)
        self.settings_view.set_level(report.level)

    def _update_ticker(self, segment: CaptionSegment) -> None:
        from .languages import language_name

        parts = [f"{self._word_count:,} words"]
        if segment.language:
            parts.append(f"{language_name(segment.language)} detected")
        self.live.set_ticker(" · ".join(parts))

    def _check_updates(self) -> None:
        """Send the user to the releases page.

        Deliberately not an auto-updater: shipping one would mean this app
        downloads and runs code on its own, which is hard to square with a
        product whose whole promise is that it does nothing behind your back.
        The button opens the page and says what it did.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        url = f"{branding.HOMEPAGE_URL}/releases"
        if QDesktopServices.openUrl(QUrl(url)):
            self.live.banner.show_message(
                f"Opened the releases page. You have version {branding.VERSION}.", "info"
            )
        else:
            self.live.banner.show_message(f"Releases are listed at {url}", "info")

    def _open_settings(self, section: str) -> None:
        self.show_window()
        self.shell.show_segment(_SETTINGS)
        self.settings_view.show_section(section)

    def _open_transcript(self, session_id: int, segment_id: int | None = None) -> None:
        from .translation.assistant import TranscriptAssistant

        intelligence = self.settings.intelligence
        config = next(
            (p for p in intelligence.providers if p.id == intelligence.assistant_provider), None
        )
        assistant = None
        if intelligence.assistant_enabled and config is not None and config.kind != "builtin":
            try:
                assistant = TranscriptAssistant(
                    config, secrets.get_key(secrets.endpoint_key_id(config.id, config.base_url))
                )
            except ValueError:
                self.live.banner.show_message("Check the assistant's server address.", "warning")
        self.transcript.set_assistant(assistant)
        self.transcript.drawer.set_consent(False)
        self.transcript.load_session(session_id, segment_id)
        self._history_stack.setCurrentIndex(1)
        self.shell.show_segment(_HISTORY)

    def _on_segment_changed(self, index: int) -> None:
        if index == _HISTORY:
            self.history.refresh()

    def _on_banner_action(self, key: str) -> None:
        if key == "setup":
            self._open_settings("everyday")
        elif key == "choose_device":
            self._open_settings("audio")
        elif key == "faster_quality":
            self._open_settings("intelligence")
        elif key == "open_shortcuts":
            self._open_settings("shortcuts")
        elif key == "open_settings":
            self._open_settings("intelligence")
        elif key == "retry_download":
            self.start_captions()

    def _notify_tray_once(self) -> None:
        if not self.settings.general.usage_intents or getattr(self, "_tray_notified", False):
            return
        self._tray_notified = True
        self.tray.notify(
            APP_NAME,
            f"{APP_NAME} keeps running in the tray so shortcuts and captions stay available.",
        )

    def _apply_retention(self) -> None:
        days = self.settings.history.retention_days
        if days > 0:
            removed = self.sessions.apply_retention(days)
            if removed:
                log.info("Removed %d transcript(s) older than %d days", removed, days)

    def run_onboarding(self) -> None:
        from .ui.onboarding import OnboardingWizard

        wizard = OnboardingWizard(self.settings, devices=self._list_devices())
        wizard.enable_audio_test(create_capture)
        wizard.finished_setup.connect(self._on_onboarding_done)
        wizard.style_previewed.connect(lambda _p: self._apply_overlay_settings())
        wizard.exec()

    def _on_onboarding_done(self, start_now: bool) -> None:
        self.store.try_save()
        self._apply_overlay_settings()
        self.live.sync_from_settings()
        if start_now:
            QTimer.singleShot(200, self.start_captions)


def _session_title() -> str:
    """Name a session after whatever the user was watching."""
    app_name = foreground_app_name()
    stamp = datetime.now().strftime("%H:%M")
    return f"{app_name} — {stamp}" if app_name else f"Session — {stamp}"


def _tier_label(settings: Settings) -> str:
    if settings.captions.model_override:
        return "Custom"
    return settings.captions.quality.value.title()


def run(argv: list[str] | None = None) -> int:
    import sys

    argv = argv if argv is not None else sys.argv
    log_file = logging_setup.setup(verbose="--verbose" in argv)
    startup_help = (
        f"See {log_file}." if log_file else
        "Check free disk space and access to your application data folder."
    )

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(branding.ORG_NAME)
    app.setDesktopFileName(branding.APP_ID)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps us alive
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    instance = SingleInstance(branding.SINGLE_INSTANCE_KEY)
    try:
        acquired = instance.acquire()
    except OSError:
        QMessageBox.critical(
            None, APP_NAME, f"Cannot access the application data folder. {startup_help}"
        )
        return 1
    if not acquired:
        QMessageBox.information(
            None, APP_NAME, f"{APP_NAME} is already running — look in your system tray."
        )
        return 0

    try:
        store = SettingsStore()
    except (OSError, ValueError) as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        instance.release()
        return 1
    theme.apply(app, store.settings.general.theme, store.settings.general.accent)
    app.setWindowIcon(theme.app_icon())
    log.info("%s %s starting (log: %s)", APP_NAME, branding.VERSION, log_file)

    try:
        controller = Controller(app, store)
    except EngineError:
        log.exception("Could not start")
        QMessageBox.critical(None, APP_NAME, f"{APP_NAME} could not start. {startup_help}")
        return 1
    except Exception:
        # Controller construction also opens SQLite, the model cache and the
        # overlay; in a frozen windowed build an unhandled error here leaves no
        # console, no dialog and no process - just a silent failure to launch.
        log.exception("Unexpected failure during startup")
        QMessageBox.critical(
            None,
            APP_NAME,
            f"{APP_NAME} could not start: an unexpected error occurred.\n{startup_help}",
        )
        return 1

    if controller.sessions.recovered_backup is not None:
        QMessageBox.warning(
            controller.shell, APP_NAME,
            "Your transcript database was damaged. SubbyAI preserved it for recovery at:\n"
            f"{controller.sessions.recovered_backup}\n\nA new empty history is ready to use."
        )
    if not store.settings.general.onboarding_complete:
        controller.run_onboarding()
    controller.start()

    try:
        return app.exec()
    finally:
        instance.release()
        log.info("%s exiting", APP_NAME)


def models_dir_hint() -> str:
    return str(paths.models_dir())
