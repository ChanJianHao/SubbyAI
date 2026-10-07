"""A support snapshot deliberately free of audio, text, keys, URLs and paths."""

import json
import platform

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..branding import VERSION
from .motion import confirm_action
from .settings_widgets import hint_label


class DiagnosticsSection(QWidget):
    def __init__(self, deps=None, parent=None):
        super().__init__(parent)
        self._deps = deps
        root = QVBoxLayout(self)
        root.addWidget(
            hint_label(
                "Share this snapshot when something feels wrong. It includes timings "
                "and queue counts, with no captions, audio, keys, "
                "server addresses or personal paths."
            )
        )
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(230)
        root.addWidget(self.report)
        copy = QPushButton("Copy safe diagnostics")
        def copy_report():
            QApplication.clipboard().setText(self.report.toPlainText())
            confirm_action(copy, "Copied ✓")

        copy.clicked.connect(copy_report)
        root.addWidget(copy)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)

    def refresh(self):
        from ..core.diagnostics import safe_snapshot

        source = getattr(self._deps, "diagnostics", None)
        data = {"version": VERSION, "os": platform.system(), "architecture": platform.machine()}
        if source:
            try:
                data.update(safe_snapshot(source()))
            except Exception:
                data["snapshot_available"] = False
        self.report.setPlainText(json.dumps(data, indent=2))

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
