"""Audio layer tests. Nothing here opens a device — no create_capture()."""

import threading

import numpy as np
import pytest

from subbyai.audio import RingBuffer, SegmenterConfig, SpeechSegmenter, to_mono_16k
from subbyai.audio.base import AudioCaptureError, AudioDevice, DevicePoller, resolve_device
from subbyai.audio.segmenter import (
    CLOSE_FAST_SILENCE,
    CLOSE_LONG_SPEECH,
    CLOSE_SILENCE,
    TARGET_RATE,
)


def _tone(seconds: float, rate: int = TARGET_RATE, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _silence(seconds: float, rate: int = TARGET_RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.float32)


def _hiss(seconds: float, level: float, rate: int = TARGET_RATE) -> np.ndarray:
    """Steady low-level noise: quiet enough to be silence, loud enough to matter."""
    rng = np.random.default_rng(1234)
    return rng.normal(0.0, level, int(seconds * rate)).astype(np.float32)


# ---------- to_mono_16k ----------


def test_to_mono_16k_downmixes_and_resamples():
    stereo_48k = np.ones((48000, 2), dtype=np.float32) * 0.5
    mono = to_mono_16k(stereo_48k, 48000)
    assert mono.ndim == 1
    assert abs(mono.size - TARGET_RATE) <= 2  # ~1 second at 16 kHz
    assert np.allclose(mono, 0.5, atol=1e-4)


def test_to_mono_16k_passes_through_at_target_rate():
    audio = _tone(0.1)
    assert np.array_equal(to_mono_16k(audio, TARGET_RATE), audio)


def test_resample_cache_matches_naive_implementation():
    """The cached index arrays must not change a single sample."""

    def naive(samples: np.ndarray, rate: int) -> np.ndarray:
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        samples = np.asarray(samples, dtype=np.float32)
        duration = samples.size / rate
        target_count = max(1, round(duration * TARGET_RATE))
        positions = np.linspace(0, samples.size - 1, target_count)
        return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)

    rng = np.random.default_rng(7)
    for rate in (44100, 48000, 22050):
        for size in (1024, 960, 4096):
            block = rng.normal(0, 0.2, (size, 2)).astype(np.float32)
            for _ in range(3):  # cold, then warm cache
                assert np.array_equal(to_mono_16k(block, rate), naive(block, rate))


# ---------- segmentation ----------


def test_speech_segment_emitted_after_silence():
    seg = SpeechSegmenter()
    audio = np.concatenate([_silence(0.5), _tone(1.0), _silence(1.0)])
    segments = seg.push(audio, TARGET_RATE)
    assert len(segments) == 1
    # Segment contains the speech plus pre-roll and some trailing silence.
    duration = segments[0].size / TARGET_RATE
    assert 1.0 <= duration <= 2.2


def test_short_blip_is_discarded():
    seg = SpeechSegmenter(SegmenterConfig(min_speech_seconds=0.3))
    audio = np.concatenate([_tone(0.1), _silence(1.5)])
    assert seg.push(audio, TARGET_RATE) == []


def test_long_speech_is_force_split():
    seg = SpeechSegmenter(SegmenterConfig(max_segment_seconds=2.0))
    segments = seg.push(_tone(5.0), TARGET_RATE)
    assert len(segments) >= 2
    for segment in segments:
        assert segment.size / TARGET_RATE <= 2.3


def test_streaming_in_small_blocks_matches_single_push():
    audio = np.concatenate([_silence(0.4), _tone(1.2), _silence(1.0)])
    seg_stream = SpeechSegmenter()
    collected = []
    for start in range(0, audio.size, 480):  # 30 ms blocks
        collected += seg_stream.push(audio[start : start + 480], TARGET_RATE)
    assert len(collected) == 1

    seg_once = SpeechSegmenter()
    once = seg_once.push(audio, TARGET_RATE)
    assert len(once) == 1
    assert np.array_equal(collected[0], once[0])


def test_flush_returns_open_segment():
    seg = SpeechSegmenter()
    seg.push(np.concatenate([_silence(0.3), _tone(1.0)]), TARGET_RATE)
    tail = seg.flush()
    assert tail is not None
    assert tail.size > 0


def test_pure_silence_yields_nothing():
    seg = SpeechSegmenter()
    assert seg.push(_silence(5.0), TARGET_RATE) == []
    assert seg.flush() is None


# ---------- adaptive close ----------


def _close_duration(tail: np.ndarray) -> tuple[float, str]:
    seg = SpeechSegmenter()
    audio = np.concatenate([_silence(0.3), _tone(1.0), tail])
    segments = seg.push(audio, TARGET_RATE)
    assert len(segments) == 1
    return segments[0].size / TARGET_RATE, seg.last_close_reason


def test_adaptive_close_is_faster_on_a_clean_boundary():
    clean, clean_reason = _close_duration(_silence(1.0))
    # A tail that never settles to the floor looks mid-sentence, not final.
    murky, murky_reason = _close_duration(_hiss(1.0, level=0.006))

    assert clean_reason == CLOSE_FAST_SILENCE
    assert murky_reason == CLOSE_SILENCE
    assert clean < murky
    assert murky - clean == pytest.approx(0.25, abs=0.06)  # 0.6 s wait vs 0.35 s


def test_adaptive_close_can_be_disabled():
    seg = SpeechSegmenter(SegmenterConfig(adaptive_close=False))
    segments = seg.push(
        np.concatenate([_silence(0.3), _tone(1.0), _silence(1.0)]), TARGET_RATE
    )
    assert seg.last_close_reason == CLOSE_SILENCE
    assert segments[0].size / TARGET_RATE == pytest.approx(1.85, abs=0.06)


def test_noise_floor_tracks_a_quiet_background():
    seg = SpeechSegmenter()
    assert seg.noise_floor == 0.0
    seg.push(_hiss(20.0, level=0.005), TARGET_RATE)
    assert 0.002 < seg.noise_floor < 0.006


def test_long_monologue_splits_on_a_weak_boundary():
    """5 s of speech with one short dip must not wait for the 8 s force-split."""
    seg = SpeechSegmenter()
    audio = np.concatenate([_tone(5.0), _hiss(0.25, level=0.006), _tone(1.0)])
    segments = seg.push(audio, TARGET_RATE)
    assert len(segments) == 1
    assert seg.last_close_reason == CLOSE_LONG_SPEECH
    assert segments[0].size / TARGET_RATE == pytest.approx(5.2, abs=0.1)


# ---------- sensitivity ----------


def test_sensitivity_changes_what_counts_as_speech():
    # RMS ~0.0057: above the "high" threshold (0.004), below "standard" (0.008).
    quiet = np.concatenate([_silence(0.3), _tone(1.0, amplitude=0.008), _silence(1.0)])

    assert SpeechSegmenter(SegmenterConfig(sensitivity="high")).push(quiet, TARGET_RATE)
    assert not SpeechSegmenter(SegmenterConfig(sensitivity="standard")).push(quiet, TARGET_RATE)
    assert not SpeechSegmenter(SegmenterConfig(sensitivity="low")).push(quiet, TARGET_RATE)


def test_sensitivity_thresholds_and_override():
    assert SegmenterConfig(sensitivity="low").threshold == 0.015
    assert SegmenterConfig(sensitivity="standard").threshold == 0.008
    assert SegmenterConfig(sensitivity="high").threshold == 0.004
    assert SegmenterConfig(sensitivity="nonsense").threshold == 0.008  # falls back
    assert SegmenterConfig(sensitivity="low", silence_threshold=0.02).threshold == 0.02


# ---------- ring buffer ----------


def test_ring_write_and_read_round_trip():
    ring = RingBuffer(1000)
    block = np.arange(300, dtype=np.float32)
    assert ring.write(block) == 0
    assert ring.available == 300
    assert np.array_equal(ring.read(300), block)
    assert ring.available == 0
    assert ring.read(10).size == 0


def test_ring_read_returns_a_copy_not_a_view():
    ring = RingBuffer(64)
    ring.write(np.ones(8, dtype=np.float32))
    out = ring.read(8)
    ring.write(np.full(64, 5.0, dtype=np.float32))
    assert np.array_equal(out, np.ones(8, dtype=np.float32))


def test_ring_wraps_around_without_losing_order():
    ring = RingBuffer(100)
    ring.write(np.arange(80, dtype=np.float32))
    assert np.array_equal(ring.read(80), np.arange(80, dtype=np.float32))
    ring.write(np.arange(80, 140, dtype=np.float32))  # wraps past the end
    assert np.array_equal(ring.read(60), np.arange(80, 140, dtype=np.float32))


def test_ring_flattens_multichannel_blocks_interleaved():
    ring = RingBuffer(64)
    stereo = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    ring.write(stereo)
    assert np.array_equal(ring.read(4), np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32))


def test_ring_drops_oldest_on_overflow_and_counts():
    ring = RingBuffer(100)
    ring.write(np.arange(80, dtype=np.float32))
    dropped = ring.write(np.arange(80, 120, dtype=np.float32))
    assert dropped == 20
    assert ring.dropped_frames == 20
    assert ring.available == 100
    # The newest 100 survive; the first 20 are gone.
    assert np.array_equal(ring.read(100), np.arange(20, 120, dtype=np.float32))


def test_ring_write_larger_than_capacity_keeps_newest_tail():
    ring = RingBuffer(50)
    dropped = ring.write(np.arange(200, dtype=np.float32))
    assert dropped == 150
    assert ring.available == 50
    assert np.array_equal(ring.read(50), np.arange(150, 200, dtype=np.float32))


def test_ring_clear_resets_contents_and_drops():
    ring = RingBuffer(10)
    ring.write(np.arange(30, dtype=np.float32))
    assert ring.dropped_frames > 0
    ring.clear()
    assert ring.available == 0
    assert ring.dropped_frames == 0
    assert ring.read(10).size == 0


def test_ring_rejects_bad_capacity():
    with pytest.raises(ValueError):
        RingBuffer(0)


def test_ring_survives_concurrent_producer_and_consumer():
    ring = RingBuffer(4096)
    total = 200 * 256
    received: list[np.ndarray] = []

    def produce() -> None:
        for i in range(200):
            ring.write(np.full(256, float(i), dtype=np.float32))

    writer = threading.Thread(target=produce)
    writer.start()
    while writer.is_alive() or ring.available:
        received.append(ring.read(1024))
    writer.join()

    read_total = sum(chunk.size for chunk in received)
    assert read_total + ring.dropped_frames == total


def test_ring_for_seconds_sizes_by_wall_clock():
    ring = RingBuffer.for_seconds(2.0, 16000, channels=2)
    assert ring.capacity == 64000


# ---------- capture plumbing (no hardware) ----------


def test_capture_error_carries_a_plain_language_message():
    err = AudioCaptureError.for_device(
        "Speakers (Realtek)", OSError("[Errno -9996] Device unavailable")
    )
    assert err.message == (
        "SubbyAI could not open Speakers (Realtek). Another app may be using it exclusively."
    )
    assert "-9996" in str(err)  # technical detail preserved for the log


def test_capture_error_has_a_message_even_for_unknown_causes():
    err = AudioCaptureError.for_device("Headphones", RuntimeError("kaboom"))
    assert err.message.startswith("SubbyAI could not open Headphones.")
    assert AudioCaptureError("raw detail").message


def test_resolve_device_prefers_id_then_name_then_default():
    a = AudioDevice("1", "Speakers", 48000, 2, True)
    b = AudioDevice("2", "Headphones", 48000, 2, True, is_default=True)
    assert resolve_device([a, b], "1") is a
    assert resolve_device([a, b], "Speakers") is a  # a saved device name is supported
    assert resolve_device([a, b], "gone") is b  # falls back to the default
    assert resolve_device([a, b], None) is b
    assert resolve_device([], "1") is None


def test_device_poller_reports_only_real_changes():
    signatures = ["a", "a", None, "b", "b", "c"]
    seen: list[int] = []
    fired = threading.Event()

    def probe() -> str | None:
        return signatures.pop(0) if signatures else "c"

    def on_change() -> None:
        seen.append(1)
        if len(seen) >= 2:
            fired.set()

    poller = DevicePoller(probe, on_change, interval=0.01)
    poller.start()
    assert fired.wait(5.0), "poller never reported the device changes"
    poller.stop()
    assert not poller.running
    assert len(seen) == 2  # a->b and b->c; the None probe is not a change


def test_device_poller_survives_a_failing_probe():
    calls: list[int] = []
    fired = threading.Event()

    def probe() -> str | None:
        calls.append(1)
        if len(calls) < 3:
            raise OSError("enumeration failed")
        return f"device-{len(calls)}"

    poller = DevicePoller(probe, fired.set, interval=0.01)
    poller.start()
    assert fired.wait(5.0)
    poller.stop()
