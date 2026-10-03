"""Rotating, redacted diagnostics with quiet third-party request loggers."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import socket
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .. import paths

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_NOISY = ("faster_whisper", "urllib3", "huggingface_hub", "httpx", "httpcore", "filelock")


def redact(text: str) -> str:
    """Run after formatting, so exception messages and traceback paths are covered."""
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|password|token|secret)\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text
    )
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,})", "[redacted]", text)
    text = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)
    # Provider addresses and query strings can identify private servers or carry
    # credentials. Troubleshooting needs the exception class, not the address.
    text = re.sub(r"https?://[^\s\"'<>]+", "<endpoint>", text)
    text = re.sub(
        r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01])|169\.254)"
        r"(?:\.\d{1,3}){2,3}\b", "<private-address>", text
    )
    text = re.sub(
        r"(?i)[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s\"']+", "<home>", text
    )
    text = re.sub(r"/(?:Users|home)/[^/\s\"']+", "<home>", text)
    username = os.environ.get("USERNAME", "")
    if len(username) > 2:
        text = re.sub(rf"\b{re.escape(username)}\b", "<user>", text, flags=re.IGNORECASE)
    hostname = socket.gethostname()
    if len(hostname) > 2:
        text = re.sub(rf"\b{re.escape(hostname)}\b", "<machine>", text, flags=re.IGNORECASE)
    return text


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class CallbackHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self._callback = callback
        self.setFormatter(SafeFormatter(_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # logging must never break the app
            self._callback(self.format(record))


def setup(verbose: bool = False) -> Path | None:
    # An unavailable log folder must not prevent the startup error dialog from
    # explaining a settings/data failure in a windowed build.
    try:
        log_file = paths.log_file()
    except OSError:
        log_file = None
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    try:
        if log_file is None:
            raise OSError("Log folder unavailable")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
        )
    except OSError:
        file_handler = None
        log_file = None
    if file_handler is not None:
        file_handler.setFormatter(SafeFormatter(_FORMAT))
        root.addHandler(file_handler)

    if sys.stderr is not None:  # absent in windowed builds
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(SafeFormatter(_FORMAT))
        root.addHandler(stream)

    # HTTP debug logs can include private request details even in diagnostic mode.
    noisy_level = logging.WARNING
    for name in _NOISY:
        logging.getLogger(name).setLevel(noisy_level)
    return log_file


def attach_sink(callback: Callable[[str], None]) -> CallbackHandler:
    handler = CallbackHandler(callback)
    logging.getLogger().addHandler(handler)
    return handler


def detach_sink(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)
