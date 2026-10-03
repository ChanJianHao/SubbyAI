"""Audio capture, buffering, and segmentation.

Backends stay import-light: ``create_capture`` imports the platform module only
when it is actually needed, so nothing here pulls in PyAudio or sounddevice.
"""

from .base import (
    AudioCallback,
    AudioCapture,
    AudioCaptureError,
    AudioDevice,
    DeviceChangeCallback,
    DevicePoller,
    create_capture,
    resolve_device,
)
from .ring import RingBuffer
from .segmenter import (
    DEFAULT_SENSITIVITY,
    SENSITIVITY_THRESHOLDS,
    TARGET_RATE,
    SegmenterConfig,
    SpeechSegmenter,
    to_mono_16k,
)

__all__ = [
    "DEFAULT_SENSITIVITY",
    "SENSITIVITY_THRESHOLDS",
    "TARGET_RATE",
    "AudioCallback",
    "AudioCapture",
    "AudioCaptureError",
    "AudioDevice",
    "DeviceChangeCallback",
    "DevicePoller",
    "RingBuffer",
    "SegmenterConfig",
    "SpeechSegmenter",
    "create_capture",
    "resolve_device",
    "to_mono_16k",
]
