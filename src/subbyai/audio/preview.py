"""A meter-only capture test. It never loads a model or retains audio."""

import contextlib
import threading

import numpy as np


class AudioPreview:
    def __init__(self, factory):
        self._factory = factory
        self._condition = threading.Condition()
        self._active = False
        self._closed = False
        self._device = None
        self._version = 0
        self.level = 0.0
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="subbyai-audio-test", daemon=True)
        self._thread.start()

    def set_active(self, active, device=None):
        with self._condition:
            self._active = active
            self._device = device
            self._version += 1
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _on_audio(self, audio, rate):
        # A scalar is the only data retained; no audio queue or disk write.
        self.level = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._active or self._closed)
                if self._closed:
                    return
                device, version = self._device, self._version
            capture = self._factory()
            try:
                self.error = ""
                capture.start(device, self._on_audio)
                with self._condition:
                    self._condition.wait_for(
                        lambda version=version: self._version != version or self._closed
                    )
            except Exception as exc:
                self.error = (
                    getattr(exc, "message", "") or "Could not test this output. Try another source."
                )
                with self._condition:
                    self._condition.wait_for(
                        lambda version=version: self._version != version or self._closed
                    )
            finally:
                with contextlib.suppress(Exception):
                    capture.stop()
                self.level = 0.0
