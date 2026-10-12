"""Deterministic signal probes; these measure pipeline damage, not ASR quality."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    from subbyai.audio.processing import StreamingResampler
    from subbyai.audio.segmenter import SegmenterConfig, SpeechSegmenter, to_mono_16k
    from subbyai.pipeline.hallucinations import HallucinationFilter
    from subbyai.pipeline.reconciliation import Reconciler

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rate, size, blocks = 44100, 1024, 1000
    converter = StreamingResampler(rate)
    converted = sum(converter.push(np.ones(size, np.float32)).size for _ in range(blocks))
    converted += converter.flush().size
    t = np.arange(48000) / 48000
    alias = to_mono_16k((0.2 * np.sin(2 * np.pi * 10000 * t)).astype(np.float32), 48000)
    quiet = (0.008 * np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)).astype(np.float32)
    segmenter = SpeechSegmenter()
    segments = segmenter.push(np.concatenate([quiet, np.zeros(16000, np.float32)]), 16000)
    tail_seg = SpeechSegmenter(SegmenterConfig(min_speech_seconds=0))
    tail = np.ones(16017, np.float32) * 0.1
    tail_seg.push(tail, 16000)
    flushed = tail_seg.flush()
    reconciler = Reconciler()
    reconciler.accept("Please ask Nakamura", 0, 6)
    repeat = reconciler.accept("Nakamura about the invoice.", 5.8, 3)
    output = {
        "sample_count_error": converted - size * blocks / rate * 16000,
        "resampled_source_seconds": size * blocks / rate,
        "output_samples": converted,
        "above_nyquist_output_rms": float(np.sqrt(np.mean(alias[100:-100] ** 2))),
        "quiet_speech_segments": len(segments),
        "flush_lost_samples": len(tail) - (0 if flushed is None else len(flushed)),
        "single_boundary_word_duplicated": repeat.startswith("Nakamura"),
        "confident_thank_you_dropped": HallucinationFilter(["thank you"]).should_drop(
            "Thank you.", 0.99, 0.99
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
