"""Observable speech lifecycle. Workers publish; Qt observers update the UI."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, Signal

from .models import DownloadProgress

log = logging.getLogger(__name__)


class SpeechPhase(StrEnum):
    SELECTED = "selected"
    LOADING = "loading"
    READY = "ready"
    ACTIVE = "active"
    FAILED = "failed"


@dataclass(frozen=True)
class SpeechState:
    phase: SpeechPhase = SpeechPhase.SELECTED
    model: str = ""
    requested: str = ""
    source: str = ""
    target: str = ""
    message: str = ""
    backend: str = "local"


class ModelDownloads(QObject):
    """One shared, resumable install job; installation never activates a model."""

    changed = Signal(str, object)
    finished = Signal(str, bool)
    _worker_finished = Signal(str, bool)
    _worker_progress = Signal(str, object)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.model = ""
        self.progress: dict[str, DownloadProgress] = {}
        self.thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self._worker_progress.connect(self._accept_progress)
        self._worker_finished.connect(self._complete)

    @property
    def busy(self) -> bool:
        # Clear only after queued completion on the GUI thread.
        return bool(self.model)

    def start(self, model: str) -> bool:
        if self.busy:
            return self.model == model
        self.model = model
        self.cancel_event = threading.Event()
        cancellation = self.cancel_event
        self._accept_progress(model, DownloadProgress(phase="checking"))

        def run():
            ok = False
            try:
                ok = self.manager.download(
                    model,
                    lambda progress: self._worker_progress.emit(model, progress),
                    cancellation,
                )
                if ok and not self.manager.is_downloaded(model):
                    ok = False
                    self._worker_progress.emit(
                        model,
                        DownloadProgress(
                            phase="error", message="Model files are incomplete. Retry the download."
                        ),
                    )
            except Exception:
                log.exception("Model installation failed model=%s", model)
                self._worker_progress.emit(
                    model,
                    DownloadProgress(
                        phase="error",
                        message="The model couldn't be installed. Check your "
                        "connection and free disk space, then retry.",
                    ),
                )
            self._worker_finished.emit(model, bool(ok) and not cancellation.is_set())

        self.thread = threading.Thread(target=run, name="subbyai-model-install", daemon=True)
        self.thread.start()
        return True

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            self._accept_progress(
                self.model,
                DownloadProgress(
                    phase="cancelling",
                    message="Cancelling… Finishing the current transfer. "
                    "Partial files can be resumed.",
                ),
            )

    def _accept_progress(self, model, progress):
        if model != self.model:
            return
        if self.cancel_event.is_set() and progress.phase in (
            "checking",
            "downloading",
            "verifying",
        ):
            return
        self.progress[model] = progress
        self.changed.emit(model, progress)

    def _complete(self, model, ok):
        if model != self.model:
            return
        latest = self.progress[model]
        phase = "done" if ok else "cancelled" if self.cancel_event.is_set() else "error"
        if latest.phase != phase:
            self._accept_progress(
                model,
                DownloadProgress(
                    latest.downloaded_bytes, latest.total_bytes, phase, latest.message
                ),
            )
        self.model = ""
        self.finished.emit(model, ok)
