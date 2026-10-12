"""Publish numeric evaluation evidence without audio, transcripts or local paths."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    def read(name):
        return json.loads((args.directory / name).read_text(encoding="utf-8-sig"))

    reports = {
        lane: read(filename)
        for lane, filename in (
            ("before", "before-live.json"),
            ("after", "after-live.json"),
            ("offline", "offline.json"),
        )
    }
    manifest = read("corpus/manifest.json")
    cases = []
    for reference in manifest:
        case = {"id": reference["id"], "tags": reference.get("tags", [])}
        case["audio_sha256"] = hashlib.sha256(
            (args.directory / "corpus" / reference["audio"]).read_bytes()
        ).hexdigest()
        for lane, report in reports.items():
            result = next(item for item in report["cases"] if item["id"] == case["id"])
            case[lane] = {
                key: result[key]
                for key in ("wer", "cer", "language_detection_errors", "language_detection_windows")
                if key in result
            }
        cases.append(case)
    replays = {}
    for name in ("jfk", "rapid"):
        data = read(f"replay-{name}.json")
        replays[name] = {
            "wer": data["wer"],
            "cer": data["cer"],
            "metrics": data["metrics"],
            "errors": len(data["errors"]),
            "provisional_events": sum(
                e["recognition_state"] == "provisional" for e in data["events"]
            ),
            "revised_caption_ids": len({e["id"] for e in data["events"] if e["revision"] > 0}),
            "source_seconds": data["source_seconds"],
            "final_source_latency_after_buffer_end_seconds": [
                e["latency_after_audio_end"]
                for e in data["events"]
                if e["recognition_state"] == "final" and e["translation_state"] == "pending"
            ],
            "translation_latency_after_buffer_end_seconds": [
                e["latency_after_audio_end"]
                for e in data["events"]
                if e["translation_state"] == "done"
            ],
        }
    output = {
        "date": "2026-10-12",
        "model": reports["after"]["model"],
        "beam": reports["after"]["beam"],
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 4,
        "chunk_seconds": reports["after"]["chunk_seconds"],
        "versions": {
            package: importlib.metadata.version(package)
            for package in (
                "faster-whisper",
                "ctranslate2",
                "numpy",
                "av",
                "onnx-asr",
                "onnxruntime",
            )
        },
        "method": (
            "Same installed weights, fresh processes per lane; literal normalized references."
        ),
        "cases": cases,
        "audio_integrity_before": read("before-integrity.json"),
        "audio_integrity_after": read("after-integrity.json"),
        "threaded_replays": replays,
        "exact_trace_checks": {
            "jfk": read("trace-integrity.json"),
            "rapid": read("trace-rapid-integrity.json"),
        },
        "limitations": [
            "Small smoke corpus; derived signals are correlated with their originals.",
            "Japanese/Chinese WER is unsuitable without a language-specific tokenizer; use CER.",
            "Literal numeric spellings are counted as errors, even if numerals preserve meaning.",
            "Latency is an illustrative file replay, not a hardware-capture performance benchmark.",
            "Translation observed for meaning/completeness; no bilingual quality score is claimed.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved numeric evidence for {len(cases)} cases")


if __name__ == "__main__":
    main()
