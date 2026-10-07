"""Windows system-sound and microphone capture via WASAPI/PyAudioWPatch.

Uses PortAudio's callback mode so audio arrives on PortAudio's own thread. That
thread is real-time: the callback converts int16 to float32 and hands the block
straight to the consumer (a ``RingBuffer.write``), and does nothing else.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from .base import (
    DEVICE_POLL_SECONDS,
    AudioCallback,
    AudioCapture,
    AudioCaptureError,
    AudioDevice,
    DeviceChangeCallback,
    DevicePoller,
    resolve_device,
)

log = logging.getLogger(__name__)

_FRAMES_PER_BUFFER = 1024
_INT16_SCALE = 1.0 / 32768.0


class WasapiLoopbackCapture(AudioCapture):
    def __init__(self) -> None:
        self._pa = None
        self._stream = None
        self._lock = threading.Lock()
        self._poller: DevicePoller | None = None

    @staticmethod
    def list_devices() -> list[AudioDevice]:
        import pyaudiowpatch as pyaudio

        devices: list[AudioDevice] = []
        try:
            with pyaudio.PyAudio() as pa:
                try:
                    default_index = pa.get_default_wasapi_loopback()["index"]
                except OSError:
                    default_index = None
                for info in pa.get_loopback_device_info_generator():
                    devices.append(
                        AudioDevice(
                            id=str(info["index"]),
                            name=str(info["name"]).removesuffix(" [Loopback]"),
                            sample_rate=int(info["defaultSampleRate"]),
                            channels=max(1, int(info["maxInputChannels"])),
                            is_loopback=True,
                            is_default=info["index"] == default_index,
                        )
                    )
                # Restrict inputs to WASAPI: other host APIs repeat the same
                # microphones under different indices and often older formats.
                host = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_input = host.get("defaultInputDevice", -1)
                for index in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(index)
                    if (
                        info["hostApi"] != host["index"]
                        or info["maxInputChannels"] < 1
                        or info.get("isLoopbackDevice", False)
                    ):
                        continue
                    devices.append(
                        AudioDevice(
                            id=str(info["index"]),
                            name=str(info["name"]),
                            sample_rate=int(info["defaultSampleRate"]),
                            channels=min(2, int(info["maxInputChannels"])),
                            is_loopback=False,
                            is_default=info["index"] == default_input,
                        )
                    )
        except Exception as exc:
            raise AudioCaptureError.for_enumeration(exc) from exc
        return devices

    def start(self, device: AudioDevice | None, callback: AudioCallback) -> AudioDevice:
        import pyaudiowpatch as pyaudio

        with self._lock:
            if self._stream is not None:
                raise AudioCaptureError(
                    "Capture already running",
                    "Captions are already running.",
                )

            if device is None:
                device = self._default_device()

            self._pa = pyaudio.PyAudio()
            channels = device.channels
            rate = device.sample_rate

            def _on_block(in_data, frame_count, time_info, status_flags):
                samples = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
                samples *= _INT16_SCALE
                try:
                    callback(samples.reshape(-1, channels), rate)
                except Exception:
                    log.exception("Audio callback failed")
                return (None, pyaudio.paContinue)

            try:
                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=int(device.id),
                    frames_per_buffer=_FRAMES_PER_BUFFER,
                    stream_callback=_on_block,
                )
            except Exception as exc:
                self._teardown()
                raise AudioCaptureError.for_device(device.name, exc) from exc

            log.info("Audio capture opened (%d Hz, %d ch, %s)", rate, channels, device.source)
            return device

    def stop(self) -> None:
        with self._lock:
            self._teardown()

    # ---------- device changes ----------

    def watch_devices(self, callback: DeviceChangeCallback) -> None:
        self.unwatch()
        self._poller = DevicePoller(self.device_signature, callback, DEVICE_POLL_SECONDS)
        self._poller.start()

    def unwatch(self) -> None:
        if self._poller is not None:
            self._poller.stop()
            self._poller = None

    @classmethod
    def device_signature(cls) -> str:
        """Include every output, its format, and which output is now default."""
        return repr(
            sorted(
                (d.id, d.name, d.sample_rate, d.channels, d.is_default, d.is_loopback)
                for d in cls.list_devices()
            )
        )

    @staticmethod
    def default_loopback_signature() -> str | None:
        """Identify the current default loopback device, or None if unknowable."""
        import pyaudiowpatch as pyaudio

        try:
            # PortAudio snapshots the device list when it initialises, so a fresh
            # instance is the only way to see hardware plugged in since we started.
            with pyaudio.PyAudio() as pa:
                info = pa.get_default_wasapi_loopback()
            return f"{info['index']}:{info['name']}"
        except Exception:
            return None

    # ---------- internals ----------

    def _default_device(self) -> AudioDevice:
        device = resolve_device(self.list_devices(), None, strict=True)
        assert device is not None
        return device

    def _teardown(self) -> None:
        stream, self._stream = self._stream, None
        pa, self._pa = self._pa, None
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                log.warning("Error stopping audio stream", exc_info=True)
            try:
                stream.close()
            except Exception:
                log.warning("Error closing audio stream", exc_info=True)
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                log.warning("Error releasing audio driver", exc_info=True)
