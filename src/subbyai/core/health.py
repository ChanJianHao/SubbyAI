"""Pipeline health as an explicit, user-facing state machine.

An empty overlay can mean silence, music, the wrong device, a stalled pipeline
or an unavailable engine. Explicit states distinguish these situations visually.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class HealthState(StrEnum):
    OFF = "off"
    STARTING = "starting"
    DOWNLOADING = "downloading"
    LISTENING = "listening"  # hearing speech, captions flowing
    SILENT = "silent"  # device open, no sound at all
    SOUND_NO_SPEECH = "sound_no_speech"  # sound present, no speech (music etc.)
    DELAYED = "delayed"  # falling behind real time
    ERROR = "error"

    @property
    def is_running(self) -> bool:
        return self not in (HealthState.OFF, HealthState.ERROR)


@dataclass(frozen=True, slots=True)
class HealthReport:
    state: HealthState
    detail: str = ""
    device_name: str | None = None
    level: float = 0.0
    lag_seconds: float = 0.0


class HealthMonitor:
    """Derives a health state from audio level, speech activity, and lag.

    Pure logic with an injectable clock so it can be tested without sleeping.
    """

    SILENCE_LEVEL = 0.002
    SILENCE_AFTER = 10.0  # seconds of no sound before reporting SILENT
    NO_SPEECH_AFTER = 12.0  # seconds of sound but no speech
    DELAYED_AFTER = 3.0  # seconds of pipeline lag before warning

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._last_sound = clock()
        self._last_speech = clock()
        self._level = 0.0
        self._lag = 0.0
        self._device_name: str | None = None
        self._state = HealthState.OFF
        self._detail = ""

    # ---------- inputs ----------

    def set_state(self, state: HealthState, detail: str = "") -> None:
        """Force a lifecycle state (off/starting/downloading/error)."""
        self._state = state
        self._detail = detail
        if state is HealthState.LISTENING:
            now = self._clock()
            self._last_sound = now
            self._last_speech = now

    def set_device(self, name: str | None) -> None:
        self._device_name = name

    def note_audio(self, level: float) -> None:
        self._level = level
        if level >= self.SILENCE_LEVEL:
            self._last_sound = self._clock()

    def note_speech(self) -> None:
        now = self._clock()
        self._last_speech = now
        self._last_sound = now

    def note_lag(self, seconds: float) -> None:
        self._lag = seconds

    # ---------- output ----------

    def report(self) -> HealthReport:
        if self._state in (
            HealthState.OFF,
            HealthState.STARTING,
            HealthState.DOWNLOADING,
            HealthState.ERROR,
        ):
            return HealthReport(self._state, self._detail, self._device_name, self._level)

        now = self._clock()
        if self._lag >= self.DELAYED_AFTER:
            return HealthReport(
                HealthState.DELAYED,
                f"Captions are about {self._lag:.0f} seconds behind.",
                self._device_name,
                self._level,
                self._lag,
            )
        if now - self._last_sound >= self.SILENCE_AFTER:
            return HealthReport(
                HealthState.SILENT,
                "We can't hear anything. Check that something is playing and isn't muted.",
                self._device_name,
                self._level,
            )
        if now - self._last_speech >= self.NO_SPEECH_AFTER:
            return HealthReport(
                HealthState.SOUND_NO_SPEECH,
                "Hearing sound, but no speech yet.",
                self._device_name,
                self._level,
            )
        return HealthReport(HealthState.LISTENING, "", self._device_name, self._level)
