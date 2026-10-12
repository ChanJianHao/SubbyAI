"""Verify recorded capture, preprocessing and model inputs against a source file.

Only reads local opt-in trace content. Prints numeric integrity results without
printing transcripts; metadata-only traces cannot perform sample comparisons.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from evaluate_accuracy import load_source


def main() -> None:
    from subbyai.audio.processing import to_mono_16k

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    records = [
        json.loads(s)
        for s in (args.trace / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    source, rate = load_source(args.audio)
    captured = [
        np.load(args.trace / r["audio_file"], allow_pickle=False)
        for r in records
        if r["stage"] == "capture" and r.get("audio_file")
    ]
    processed = [
        np.load(args.trace / r["audio_file"], allow_pickle=False)
        for r in records
        if r["stage"] == "preprocessed" and r.get("audio_file")
    ]
    if not captured or not processed:
        parser.error("Trace content is unavailable; enable explicit content recording first.")
    original = np.concatenate(captured)
    mono = np.concatenate(processed)
    reference = to_mono_16k(source, rate)
    exact = original.shape == source.shape and np.array_equal(original, source)
    preprocessing = mono.shape == reference.shape and np.allclose(mono, reference, atol=1e-6)
    checks = []
    for event in records:
        if event["stage"] != "asr_input" or not event.get("audio_file"):
            continue
        audio = np.load(args.trace / event["audio_file"], allow_pickle=False)
        start = round(event["start"] * 16000)
        expected = mono[start : start + len(audio)]
        checks.append(expected.shape == audio.shape and np.array_equal(expected, audio))
    result = {
        "capture_matches_source": exact,
        "preprocessing_matches_continuous_reference": preprocessing,
        "captured_frames": len(original),
        "processed_frames": len(mono),
        "asr_chunks_checked": len(checks),
        "exact_asr_chunks": sum(checks),
        "trace_drops": records[-1].get("dropped", 0),
    }
    if args.out:
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not (exact and preprocessing and all(checks)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
