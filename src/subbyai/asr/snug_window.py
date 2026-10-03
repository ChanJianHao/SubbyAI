"""Bound Whisper's encoder work to the phrase length with a silence margin.

The pinned faster-whisper release pads phrases to 3000 mel frames by default.
CTranslate2 accepts shorter windows. This adapter preserves at least 256
trailing frames and a 512-frame minimum to reduce repetition near phrase ends.
Rounded shapes limit allocator churn; long phrases retain the full window.

The patch targets ``faster_whisper.transcribe.pad_or_trim``, the imported name
actually used by the decoder. If the upstream API changes, full-window decoding
remains available.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

#: Mel frames per second. Whisper's feature extractor is fixed at this.
FRAMES_PER_SECOND = 100

#: Full window: 30 seconds, and the most the encoder accepts.
MAX_FRAMES = 3000

#: Trailing frames kept beyond the speech, so the decoder can tell it ended.
#: Not a tuning knob — see the module docstring for what happens below this.
MARGIN_FRAMES = 256

#: Never encode less than this, whatever the margin arithmetic says.
MIN_FRAMES = 512

#: Windows are rounded up to a multiple of this, so a session reuses a handful
#: of shapes instead of a new one per utterance.
QUANTUM = 128

_lock = threading.Lock()
_applied = False


def window_for(content_frames: int) -> int:
    """The encoder window to use for this much speech."""
    if content_frames >= MAX_FRAMES:
        return MAX_FRAMES
    wanted = content_frames + MARGIN_FRAMES
    rounded = -(-wanted // QUANTUM) * QUANTUM
    return min(MAX_FRAMES, max(MIN_FRAMES, rounded))


def apply() -> bool:
    """Install the shorter window. Returns whether it took effect.

    Safe to call repeatedly and safe to fail: if faster-whisper's internals
    ever stop looking like this, the app keeps working with full windows rather
    than breaking. Never raises.
    """
    global _applied
    with _lock:
        if _applied:
            return True
        try:
            from faster_whisper import transcribe as fw

            original = fw.pad_or_trim
            if not callable(original):
                return False

            def pad_or_trim(array, length: int = MAX_FRAMES, *, axis: int = -1):
                # Only ever narrows, and only the default full window. An
                # explicit shorter length is somebody else's decision.
                if length == MAX_FRAMES:
                    try:
                        length = window_for(array.shape[axis])
                    except Exception:  # pragma: no cover - shape surprises
                        length = MAX_FRAMES
                return original(array, length, axis=axis)

            fw.pad_or_trim = pad_or_trim
            _applied = True
            log.info("Encoder window follows the audio, capped at %d frames", MAX_FRAMES)
            return True
        except Exception:
            log.warning("Could not shorten the encoder window; using the full one", exc_info=True)
            return False


def trailing_silence_seconds() -> float:
    """Silence to append before decoding, so the margin has something to hold.

    The segmenter leaves 0.35-0.6 s after a normal silence close, but a segment
    closed by the length limit can end mid-word without trailing silence.
    """
    return 0.4
