"""The Ask AI drawer of the transcript viewer.

Two rules the rest of this file exists to enforce:

- **Nothing is sent silently.** The first time a cloud assistant is used here the
  drawer shows the exact text that would leave the machine and waits for Send.
  The provider object is not touched until then.
- **The drawer knows nothing about HTTP.** It calls whatever assistant it was
  handed, on a worker thread, and reports what came back with the provider and
  its privacy tier attached to every answer.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.events import PrivacyTier
from . import theme
from .history_widgets import Hairline, blend
from .tokens import RADIUS, SPACE, Palette

DRAWER_WIDTH = 320
_CONSENT_PREVIEW_CHARS = 280

SUMMARIZE_PROMPT = "Summarize this session"
EXPLAIN_PROMPT = "Explain selection"


class Assistant(Protocol):
    """What the shell must hand in for Ask AI to appear at all.

    ``ask`` is called on a worker thread and may block; raise to report failure.
    """

    @property
    def label(self) -> str: ...

    @property
    def tier(self) -> PrivacyTier: ...

    def ask(self, question: str, transcript: str) -> str: ...


def ask_button_label(assistant: Assistant | None) -> str:
    """`Ask AI · Cloud` — the privacy tier rides in the button, not a tooltip."""
    if assistant is None:
        return "Ask AI"
    return f"Ask AI · {assistant.tier.label}"


def provider_line(assistant: Assistant) -> str:
    line = f"{assistant.label} · {assistant.tier.label}"
    if assistant.tier is PrivacyTier.CLOUD:
        line += f" — the transcript excerpt is sent to {assistant.label}"
    return line


def consent_question(assistant: Assistant) -> str:
    return (
        f"This sends the selected text to {assistant.label}. "
        "Transcripts otherwise never leave this device."
    )


class AskAiDrawer(QWidget):
    """Right-hand panel: summarize, explain a selection, or ask something."""

    consent_granted = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None, assistant: Assistant | None = None):
        super().__init__(parent)
        self._assistant = assistant
        self._consent = False
        self._busy = False
        self._generation = 0
        self._pending: tuple[str, str] | None = None
        self._transcript_source: Callable[[], str] = str
        self._selection_source: Callable[[], str] = str

        self.setFixedWidth(DRAWER_WIDTH)
        # Without this a plain QWidget ignores its own background rule, and the
        # drawer would float over the transcript with nothing behind its text.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build()
        self._sink = _Sink(self)
        self._sink.finished.connect(self._on_answer)
        self._sink.failed.connect(self._on_failure)
        theme.subscribe(self._restyle)

    # ---------- construction ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        root.setSpacing(SPACE["sm"])

        header = QHBoxLayout()
        title = QLabel("Ask AI")
        title.setObjectName("heading")
        header.addWidget(title)
        header.addStretch(1)
        close = QPushButton("✕")
        close.setObjectName("quiet")
        close.setFixedWidth(28)
        close.clicked.connect(self.close_requested)
        header.addWidget(close)
        root.addLayout(header)

        self._provider_label = QLabel()
        self._provider_label.setObjectName("secondary")
        self._provider_label.setWordWrap(True)
        root.addWidget(self._provider_label)
        root.addWidget(Hairline())

        self.summarize_button = QPushButton(SUMMARIZE_PROMPT)
        self.summarize_button.clicked.connect(self.summarize)
        root.addWidget(self.summarize_button)

        self.explain_button = QPushButton(EXPLAIN_PROMPT)
        self.explain_button.setEnabled(False)
        self.explain_button.clicked.connect(self.explain_selection)
        root.addWidget(self.explain_button)

        self.consent_panel = _ConsentPanel()
        self.consent_panel.accepted.connect(self._grant_consent)
        self.consent_panel.rejected.connect(self._cancel_pending)
        self.consent_panel.hide()
        root.addWidget(self.consent_panel)

        self.answers = QTextBrowser()
        self.answers.setOpenExternalLinks(False)
        self.answers.setPlaceholderText("Answers appear here. Nothing is sent until you ask.")
        root.addWidget(self.answers, 1)

        ask_row = QHBoxLayout()
        ask_row.setSpacing(SPACE["sm"])
        self.question_field = QLineEdit()
        self.question_field.setPlaceholderText("Ask about this session")
        self.question_field.returnPressed.connect(self.ask_question)
        ask_row.addWidget(self.question_field, 1)
        self.ask_button = QPushButton("Ask")
        self.ask_button.clicked.connect(self.ask_question)
        ask_row.addWidget(self.ask_button)
        root.addLayout(ask_row)

        self._restyle(theme.current())
        self._refresh_provider_line()

    # ---------- wiring ----------

    def set_sources(
        self, transcript: Callable[[], str], selection: Callable[[], str]
    ) -> None:
        """Hand in where the text comes from; the drawer never reads the store."""
        self._transcript_source = transcript
        self._selection_source = selection

    def set_assistant(self, assistant: Assistant | None) -> None:
        self._generation += 1
        self._assistant = assistant
        self._consent = False
        self._pending = None
        self.consent_panel.hide()
        self._refresh_provider_line()

    def set_consent(self, given: bool) -> None:
        self._consent = given

    def has_consent(self) -> bool:
        return self._consent

    def set_has_selection(self, has_selection: bool) -> None:
        self.explain_button.setEnabled(has_selection and self._assistant is not None)

    # ---------- actions ----------

    def summarize(self) -> None:
        self._request(SUMMARIZE_PROMPT, self._transcript_source())

    def explain_selection(self) -> None:
        selection = self._selection_source()
        if selection.strip():
            self._request(EXPLAIN_PROMPT, selection)

    def ask_question(self) -> None:
        question = self.question_field.text().strip()
        if question:
            self._request(question, self._transcript_source())

    # ---------- the consent gate ----------

    def _request(self, question: str, excerpt: str) -> None:
        if self._assistant is None or self._busy:
            return
        if self._assistant.tier is PrivacyTier.CLOUD and not self._consent:
            self._pending = (question, excerpt)
            self.consent_panel.show_for(consent_question(self._assistant), excerpt)
            return
        self._dispatch(question, excerpt)

    def _grant_consent(self) -> None:
        self._consent = True
        self.consent_granted.emit()
        self.consent_panel.hide()
        pending, self._pending = self._pending, None
        if pending:
            self._dispatch(*pending)

    def _cancel_pending(self) -> None:
        self._pending = None
        self.consent_panel.hide()

    def _dispatch(self, question: str, excerpt: str) -> None:
        assistant = self._assistant
        if assistant is None or self._busy:
            return
        self._set_busy(True)
        self._append(f"<b>{_escape(question)}</b>")
        self._append(f"<i>Asking {_escape(assistant.label)}…</i>")
        QThreadPool.globalInstance().start(
            _AskTask(assistant, question, excerpt, self._sink, self._generation)
        )

    # ---------- results ----------

    def _on_answer(self, generation: int, answer: str) -> None:
        self._set_busy(False)
        if generation != self._generation:
            return
        if self._assistant is not None:
            self._append(
                f'<span style="color:{theme.current().text_secondary};">'
                f"{_escape(provider_line(self._assistant))}</span>"
            )
        self._append(_escape(answer).replace("\n", "<br>"))

    def _on_failure(self, generation: int, reason: str) -> None:
        self._set_busy(False)
        if generation != self._generation:
            return
        name = self._assistant.label if self._assistant else "The assistant"
        self._append(
            f'<span style="color:{theme.current().warning};">'
            f"{_escape(name)} couldn't answer: {_escape(reason)}</span>"
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.summarize_button, self.ask_button, self.explain_button):
            button.setEnabled(not busy)
        self.question_field.setEnabled(not busy)

    def _append(self, html_text: str) -> None:
        self.answers.append(html_text)

    def _refresh_provider_line(self) -> None:
        if self._assistant is None:
            self._provider_label.setText("")
            self.explain_button.setEnabled(False)
            return
        self._provider_label.setText(provider_line(self._assistant))

    def _restyle(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"AskAiDrawer {{ background: {palette.surface};"
            f" border-left: 1px solid {palette.hairline}; }}"
        )
        self.consent_panel.restyle(palette)


class _ConsentPanel(QFrame):
    """The one-time inline confirm before anything reaches a cloud provider."""

    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setObjectName("secondary")
        layout.addWidget(self.preview)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.rejected)
        buttons.addWidget(cancel)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.accepted)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)
        self.restyle(theme.current())

    def show_for(self, message: str, excerpt: str) -> None:
        self.message.setText(message)
        preview = excerpt.strip().replace("\n", " ")
        if len(preview) > _CONSENT_PREVIEW_CHARS:
            preview = preview[:_CONSENT_PREVIEW_CHARS] + "…"
        self.preview.setText(
            f"{len(excerpt):,} characters will be sent.\n“{preview}”" if preview else ""
        )
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.show()

    def restyle(self, palette: Palette) -> None:
        # Blended rather than the token's translucent tint: this panel must stay
        # readable whatever happens to be painted behind the drawer.
        self.setStyleSheet(
            f"_ConsentPanel {{ background: {blend(palette.accent, palette.surface, 0.16)};"
            f" border: 1px solid {palette.accent};"
            f" border-radius: {RADIUS['md']}px; }}"
        )


class _Sink(QObject):
    """Bridges the worker thread back onto the UI thread through queued signals."""

    finished = Signal(int, str)
    failed = Signal(int, str)


class _AskTask(QRunnable):
    def __init__(
        self, assistant: Assistant, question: str, excerpt: str, sink: _Sink, generation: int
    ):
        super().__init__()
        self._assistant = assistant
        self._question = question
        self._excerpt = excerpt
        self._sink = sink
        self._generation = generation

    def run(self) -> None:
        try:
            answer = self._assistant.ask(self._question, self._excerpt)
        except Exception as exc:
            self._sink.failed.emit(self._generation, str(exc) or exc.__class__.__name__)
            return
        self._sink.finished.emit(self._generation, answer)


def _escape(text: str) -> str:
    return html.escape(text)
