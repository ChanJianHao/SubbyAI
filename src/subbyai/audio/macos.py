"""macOS input-device capture through Core Audio and sounddevice.

System audio requires a virtual loopback input such as BlackHole. Loopback
devices appear first and are marked as such in the source picker.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from .base import (
    AudioCallback,
    AudioCapture,
    AudioCaptureError,
    AudioDevice,
    DevicePoller,
    resolve_device,
)

log = logging.getLogger(__name__)

_LOOPBACK_HINTS = ("blackhole", "loopback", "soundflower")


class CoreAudioInputCapture(AudioCapture):
    def __init__(self) -> None:
        self._stream = None
        self._lock = threading.Lock()
        self._poller: DevicePoller | None = None

    @staticmethod
    def list_devices() -> list[AudioDevice]:
        try:
            import sounddevice as sd

            default_input = sd.default.device[0]
            devices: list[AudioDevice] = []
            for index, info in enumerate(sd.query_devices()):
                if info["max_input_channels"] < 1:
                    continue
                name = str(info["name"])
                devices.append(
                    AudioDevice(
                        id=str(index),
                        name=name,
                        sample_rate=int(info["default_samplerate"]),
                        channels=min(2, int(info["max_input_channels"])),
                        is_loopback=any(h in name.lower() for h in _LOOPBACK_HINTS),
                        is_default=index == default_input,
                    )
                )
        except Exception as exc:
            raise AudioCaptureError.for_enumeration(exc) from exc
        # Loopback-style devices first: they are what captioning wants.
        devices.sort(key=lambda d: (not d.is_loopback, d.name.lower()))
        return devices

    def start(self, device: AudioDevice | None, callback: AudioCallback) -> AudioDevice:
        import sounddevice as sd

        with self._lock:
            if self._stream is not None:
                raise AudioCaptureError(
                    "Capture already running",
                    "Captions are already running.",
                )

            if device is None:
                device = resolve_device(self.list_devices(), None, strict=True)
                assert device is not None

            rate = device.sample_rate

            def _on_block(indata, frames, time_info, status) -> None:
                if status:
                    log.debug("Audio stream status: %s", status)
                # sounddevice already hands us float32; the consumer copies, so
                # this stays a view and the real-time thread allocates nothing.
                try:
                    callback(np.asarray(indata, dtype=np.float32), rate)
                except Exception:
                    log.exception("Audio callback failed")

            try:
                self._stream = sd.InputStream(
                    device=int(device.id),
                    channels=device.channels,
                    samplerate=rate,
                    dtype="float32",
                    callback=_on_block,
                )
                self._stream.start()
            except Exception as exc:
                self._close_stream()
                raise AudioCaptureError.for_device(device.name, exc) from exc

            log.info(
                "Audio capture opened (%d Hz, %d ch, %s)", rate, device.channels, device.source
            )
            return device

    def stop(self) -> None:
        with self._lock:
            self._close_stream()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            for action in (stream.stop, stream.close):
                try:
                    action()
                except Exception:
                    log.warning("Error releasing audio stream", exc_info=True)

    def watch_devices(self, callback) -> None:
        self.unwatch()
        self._poller = DevicePoller(self.device_signature, callback)
        self._poller.start()

    def unwatch(self) -> None:
        if self._poller is not None:
            self._poller.stop()
            self._poller = None

    @classmethod
    def device_signature(cls) -> str:
        return repr(
            sorted(
                (d.id, d.name, d.sample_rate, d.channels, d.is_default, d.is_loopback)
                for d in cls.list_devices()
            )
        )
