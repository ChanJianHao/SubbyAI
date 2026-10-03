"""Measure real recognition at representative live-utterance lengths.

Use --audio with public speech for meaningful timing. The synthetic fallback
only verifies the harness; decoding noise does not measure speech performance.
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

SAMPLE_RATE = 16000


@dataclass
class Result:
    model: str
    provider: str
    device: str
    seconds_of_audio: float
    median_ms: float
    p95_ms: float
    realtime_factor: float
    """Audio seconds processed per wall-clock second. Below 1.0 falls behind."""

    rss_mb: float
    load_ms: float


def speech_shaped(seconds: float) -> np.ndarray:
    """Deterministic noise for harness checks, never a performance result."""
    rng = np.random.default_rng(1234)
    samples = int(seconds * SAMPLE_RATE)
    noise = rng.standard_normal(samples).astype(np.float32)
    # A crude formant-ish shaping: low-pass then amplitude-modulate at ~4 Hz,
    # roughly the syllable rate, so the VAD and encoder see something plausible.
    window = 128
    kernel = np.hanning(window).astype(np.float32)
    kernel /= kernel.sum()
    shaped = np.convolve(noise, kernel, mode="same").astype(np.float32)
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 4.0 * np.arange(samples) / SAMPLE_RATE))
    return (shaped * envelope * 0.3).astype(np.float32)


def load_audio(path: Path, seconds: float) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        raw = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if handle.getnchannels() > 1:
            raw = raw.reshape(-1, handle.getnchannels()).mean(axis=1)
    wanted = int(seconds * SAMPLE_RATE)
    if len(raw) >= wanted:
        return raw[:wanted]
    return np.tile(raw, int(np.ceil(wanted / len(raw))))[:wanted]


def rss_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / 1024 / 1024


def bench_model(model_name: str, lengths: list[float], repeats: int, audio: Path | None):
    from subbyai.asr.catalogue import spec_or_guess
    from subbyai.asr.engine import engine_for
    from subbyai.asr.models import ModelManager

    manager = ModelManager()
    if not manager.is_downloaded(model_name):
        print(f"  {model_name}: not downloaded, skipping")
        return []

    gc.collect()
    before = rss_mb()
    started = time.perf_counter()
    engine = engine_for(model_name, "auto", manager)
    engine.load()
    load_ms = (time.perf_counter() - started) * 1000
    resident = rss_mb() - before

    results = []
    for seconds in lengths:
        chunk = load_audio(audio, seconds) if audio else speech_shaped(seconds)
        engine.transcribe(chunk)  # warm the graph; never timed
        timings = []
        for _ in range(repeats):
            start = time.perf_counter()
            engine.transcribe(chunk)
            timings.append((time.perf_counter() - start) * 1000)
        median = statistics.median(timings)
        results.append(
            Result(
                model=model_name,
                provider=spec_or_guess(model_name).provider,
                device=engine.device,
                seconds_of_audio=seconds,
                median_ms=round(median, 1),
                p95_ms=round(max(timings), 1),
                realtime_factor=round(seconds / (median / 1000), 2),
                rss_mb=round(resident, 1),
                load_ms=round(load_ms, 1),
            )
        )
        print(
            f"  {model_name} {seconds:5.1f}s -> {median:7.1f} ms  "
            f"({results[-1].realtime_factor:6.2f}x realtime)"
        )
    engine.unload()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="base,small")
    parser.add_argument("--lengths", default="1,2,4,8,15,30")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--audio",
        type=Path,
        default=None,
        help="WAV of real speech. Required for meaningful timings; see speech_shaped.",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    lengths = [float(x) for x in args.lengths.split(",")]
    if args.audio is None:
        print(
            "WARNING: no --audio given, so this times synthetic noise. Whisper\n"
            "decodes noise to nothing and skips the decoder, which understates\n"
            "its real cost by roughly 8x. These numbers are not usable.\n"
        )
    results: list[Result] = []
    for name in args.models.split(","):
        name = name.strip()
        if not name:
            continue
        print(f"{name}:")
        results.extend(bench_model(name, lengths, args.repeats, args.audio))

    if results:
        print("\nCost per second of audio (lower is better):")
        for r in results:
            per_second = r.median_ms / r.seconds_of_audio
            print(f"  {r.model:16} {r.seconds_of_audio:5.1f}s  {per_second:8.1f} ms/s of audio")

    if args.out:
        args.out.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
