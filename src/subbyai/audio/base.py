"""Platform-neutral audio capture and device selection.

Backends deliver raw float32 blocks at the native rate/channel count. The segmenter
handles downmixing and resampling. Errors carry actionable messages and device
watchers allow recovery when an output changes."""

from __future__ import annotations

import logging
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..branding import APP_NAME

log = logging.getLogger(__name__)

# Callback receives (samples[frames, channels] float32 in [-1, 1], sample_rate).
# The array is only guaranteed valid for the duration of the call and runs on a
# real-time thread: copy what you keep, and do no work beyond a buffer write.
AudioCallback = Callable[[np.ndarray, int], None]

# Fired when the set of capture devices — or which one is default — changed.
DeviceChangeCallback = Callable[[], None]

DEVICE_POLL_SECONDS = 2.0


class AudioCaptureError(Exception):
    """Raised when a device cannot be listed, opened, or read.

    ``str(exc)`` is the technical detail for the log; ``message`` is written for
    a person and is safe to show verbatim.
    """

    def __init__(self, detail: str, message: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.message = message or f"{APP_NAME} could not use your audio device."

    @classmethod
    def for_device(cls, device_name: str, cause: BaseException) -> AudioCaptureError:
        """Build an open/read failure with a hint inferred from the driver error."""
        return cls(
            f"Could not open audio device '{device_name}': {cause}",
            f"{APP_NAME} could not open {device_name}. {_hint_for(cause)}",
        )

    @classmethod
    def for_enumeration(cls, cause: BaseException) -> AudioCaptureError:
        return cls(
            f"Could not enumerate audio devices: {cause}",
            f"{APP_NAME} could not see your audio devices. Restarting the app usually clears this.",
        )


# Substrings seen in PortAudio/WASAPI/CoreAudio failures, mapped to what a
# person can actually do about them.
_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("exclusive", "device unavailable", "-9996", "already in use", "in use"),
        "Another app may be using it exclusively.",
    ),
    (
        ("access", "denied", "permission", "not authorized"),
        "Your system blocked access to it. Check the privacy settings for audio recording.",
    ),
    (
        ("invalid sample rate", "-9997", "invalid number of channels", "-9998", "format"),
        "Its audio format changed. Reconnecting the device usually fixes it.",
    ),
    (
        ("no device", "invalid device", "-9996l", "device not found", "-9985"),
        "It may have been unplugged.",
    ),
)


def _hint_for(cause: BaseException) -> str:
    text = str(cause).lower()
    for needles, hint in _HINTS:
        if any(needle in text for needle in needles):
            return hint
    return "It may have been unplugged or changed since it was chosen."


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    sample_rate: int
    channels: int
    is_loopback: bool
    is_default: bool = False


class AudioCapture(ABC):
    """A capture session bound to one device. Start/stop are idempotent."""

    @staticmethod
    @abstractmethod
    def list_devices() -> list[AudioDevice]:
        """Devices usable for captioning (loopback outputs first)."""

    @abstractmethod
    def start(self, device: AudioDevice | None, callback: AudioCallback) -> AudioDevice:
        """Begin capture; returns the device actually opened (resolves default)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop capture and release the device."""

    # ---------- device change notification (opt-in, no-op by default) ----------

    def watch_devices(self, callback: DeviceChangeCallback) -> None:  # noqa: B027
        """Call ``callback`` (off the audio thread) when the device set changes.

        Optional: backends with no way to detect changes keep the no-op, and the
        pipeline simply never gets told. Independent of start/stop, so a backend
        keeps watching while capture is idle and the UI's device list stays
        honest. Owners must call ``unwatch``.
        """

    def unwatch(self) -> None:  # noqa: B027 - optional hook, see watch_devices
        """Stop reporting device changes."""


class DevicePoller:
    """Reports device changes by polling a cheap signature on a daemon thread.

    Deliberately not COM/CoreAudio event plumbing: a string compare every two
    seconds costs nothing measurable and cannot wedge the audio thread, and the
    only question we need answered is "is the default device still the one we
    opened?".
    """

    def __init__(
        self,
        probe: Callable[[], str | None],
        callback: DeviceChangeCallback,
        interval: float = DEVICE_POLL_SECONDS,
    ) -> None:
        self._probe = probe
        self._callback = callback
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-device-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._interval + 1.0)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        baseline = self._read()
        while not self._stop.wait(self._interval):
            current = self._read()
            # None means "could not tell" — a transient enumeration failure must
            # not be mistaken for the user swapping hardware.
            if current is None or current == baseline:
                continue
            baseline = current
            try:
                self._callback()
            except Exception:  # a bad listener must not kill the poller
                log.exception("Device change callback failed")

    def _read(self) -> str | None:
        try:
            return self._probe()
        except Exception:  # probing is best-effort by design
            log.debug("Device probe failed", exc_info=True)
            return None


def create_capture() -> AudioCapture:
    """Instantiate the capture backend for the current platform."""
    if sys.platform == "win32":
        from .windows import WasapiLoopbackCapture

        return WasapiLoopbackCapture()
    if sys.platform == "darwin":
        from .macos import CoreAudioInputCapture

        return CoreAudioInputCapture()
    raise AudioCaptureError(
        f"Unsupported platform: {sys.platform}",
        f"{APP_NAME} does not support capturing audio on this operating system yet.",
    )


def resolve_device(
    devices: list[AudioDevice],
    preferred_id: str | None,
    preferred_name: str = "",
    strict: bool = False,
) -> AudioDevice | None:
    """Pick the configured device by stable name, with an optional device ID."""
    if not devices:
        if strict:
            raise AudioCaptureError(
                "No audio outputs found",
                "No playback device is available. Connect speakers or headphones and try again.",
            )
        return None
    if preferred_id:
        if preferred_name:
            named = next((d for d in devices if d.name == preferred_name), None)
            if named is not None:
                return named
        for device in devices:
            if device.name == preferred_id or (not preferred_name and device.id == preferred_id):
                return device
        if strict:
            raise AudioCaptureError(
                "Selected device missing",
                "Your selected audio output is disconnected. "
                "Reconnect it or choose another output.",
            )
    return next((d for d in devices if d.is_default), devices[0])
