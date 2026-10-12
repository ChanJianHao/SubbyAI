"""Compare the same local model on whole-file and production-segmented audio.

Run each lane in a fresh process so global decoder modifications cannot infect
the offline control. Outputs contain content; keep them in a private directory.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def error_rate(reference: str, hypothesis: str, *, characters: bool = False) -> dict:
    def units(text):
        text = unicodedata.normalize("NFKC", text).casefold()
        text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
        return list("".join(text.split())) if characters else text.split()

    ref, hyp = units(reference), units(hypothesis)
    # Store edit categories as well as their sum; ties prefer substitutions.
    row = [(i, 0, i, 0) for i in range(len(hyp) + 1)]
    for i, expected in enumerate(ref, 1):
        new = [(i, 0, 0, i)]
        for j, actual in enumerate(hyp, 1):
            if expected == actual:
                new.append(row[j - 1])
            else:
                cost, sub, ins, delete = row[j - 1]
                a = (cost + 1, sub + 1, ins, delete)
                cost, sub, ins, delete = new[j - 1]
                b = (cost + 1, sub, ins + 1, delete)
                cost, sub, ins, delete = row[j]
                c = (cost + 1, sub, ins, delete + 1)
                new.append(min((a, b, c), key=lambda v: v[0]))
        row = new
    cost, sub, ins, delete = row[-1]
    return {
        "rate": cost / max(1, len(ref)),
        "reference_units": len(ref),
        "substitutions": sub,
        "insertions": ins,
        "deletions": delete,
    }


def load_source(path: Path) -> tuple[np.ndarray, int]:
    import av

    # Decode to float at the native rate and preserve channels. A separate
    # stock FFmpeg conversion supplies the independent offline control.
    blocks = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        converter = av.AudioResampler(format="fltp", layout=stream.layout, rate=stream.rate)
        for frame in container.decode(stream):
            blocks.extend(f.to_ndarray().T for f in converter.resample(frame))
        blocks.extend(f.to_ndarray().T for f in converter.resample(None))
        rate = stream.rate
    return np.concatenate(blocks).astype(np.float32), rate


def main() -> None:
    from subbyai.asr.engine import TranscriptionEngine
    from subbyai.asr.models import ModelManager
    from subbyai.audio.segmenter import CLOSE_REVISION, SegmenterConfig, SpeechSegmenter
    from subbyai.audio.vad import SileroSpeechDetector
    from subbyai.pipeline.captioner import AudioWork
    from subbyai.pipeline.context import RecognitionContext
    from subbyai.pipeline.hallucinations import HallucinationFilter
    from subbyai.pipeline.reconciliation import Reconciler

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lane", choices=("offline", "live", "legacy"), required=True)
    parser.add_argument(
        "--legacy-dir",
        type=Path,
        help="Pre-change segmenter.py, reconciliation.py and snug_window.py snapshots",
    )
    parser.add_argument("--model", default="base")
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--beam", type=int, default=1)
    parser.add_argument("--chunk-seconds", type=float, default=6)
    parser.add_argument("--block-samples", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    manager = ModelManager(args.models_dir) if args.models_dir else ModelManager()
    model_path = manager.local_path(args.model)
    if model_path is None:
        parser.error("Model must already be installed; this harness never downloads.")
    if args.lane in ("offline", "legacy"):
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(model_path), device="cpu", compute_type="int8", local_files_only=True, cpu_threads=4
        )
        if args.lane == "legacy":
            if not args.legacy_dir:
                parser.error("--legacy-dir is required for the original pipeline control")
            legacy = {}
            for name in ("segmenter", "reconciliation", "snug_window"):
                module_name = f"accuracy_legacy_{name}"
                spec = importlib.util.spec_from_file_location(
                    module_name, args.legacy_dir / f"{name}.py"
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                legacy[name] = module
            legacy["snug_window"].apply()
    else:
        model = TranscriptionEngine(args.model, "cpu", manager)
        model.beam_size = args.beam
        model.cpu_threads = 4
        model.load()
    records = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    report = {
        "lane": args.lane,
        "model": args.model,
        "model_path": str(model_path),
        "beam": args.beam,
        "chunk_seconds": args.chunk_seconds,
        "cases": [],
    }
    for case in records:
        import ctranslate2

        ctranslate2.set_random_seed(args.seed)
        audio_path = args.manifest.parent / case["audio"]
        language = None if case.get("auto_detect") else case.get("language")
        begin = time.monotonic()
        raw = []
        if args.lane == "offline":
            import av

            source, rate = load_source(audio_path)
            # The antiphase fixture deliberately has equivalent opposite-polarity
            # channels; the independent reference uses the intact left channel.
            mono = source[:, 0] if case["id"] == "antiphase" else source.mean(axis=1)
            converter = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            frame = av.AudioFrame.from_ndarray(
                mono[None, :].astype(np.float32), format="fltp", layout="mono"
            )
            frame.sample_rate = rate
            frames = converter.resample(frame) + converter.resample(None)
            audio = np.concatenate([f.to_ndarray().reshape(-1) for f in frames])
            segments, info = model.transcribe(
                audio,
                language=language,
                beam_size=args.beam,
                vad_filter=False,
                condition_on_previous_text=True,
                multilingual=bool(case.get("language_spans")),
            )
            hypothesis = " ".join(s.text.strip() for s in segments)
            detected = info.language
        elif args.lane == "legacy":
            from subbyai.asr.engine import summarize_segments
            from subbyai.pipeline.hallucinations import _normalize

            source, rate = load_source(audio_path)
            segmenter = legacy["segmenter"].SpeechSegmenter(
                legacy["segmenter"].SegmenterConfig(
                    max_segment_seconds=args.chunk_seconds, close_after_silence=0.5
                )
            )
            work = []
            for offset in range(0, len(source), args.block_samples):
                chunks = segmenter.push(source[offset : offset + args.block_samples], rate)
                work.extend(zip(chunks, segmenter.completed_times, strict=True))
            tail = segmenter.flush()
            if tail is not None:
                work.append((tail, segmenter._emitted_time))
            reconciler = legacy["reconciliation"].Reconciler()
            gate = HallucinationFilter()
            output = []
            detected = None
            for chunk, (start, duration) in work:
                tick = time.monotonic()
                segments, info = model.transcribe(
                    np.concatenate([chunk, np.zeros(6400, np.float32)]),
                    task="transcribe",
                    language=language,
                    beam_size=args.beam,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    initial_prompt=None,
                )
                result = summarize_segments(segments, info)
                # Original phrase-list behavior rejected matches unconditionally.
                text = result.text
                if _normalize(text) in gate._phrases or (
                    result.speech_probability < 0.6 and result.confidence < 0.5
                ):
                    text = ""
                stitched = reconciler.accept(text, start, duration)
                output.append(stitched)
                detected = result.language
                raw.append(
                    {
                        "start": start,
                        "duration": duration,
                        "raw": result.text,
                        "stitched": stitched,
                        "language": result.language,
                        "inference_seconds": time.monotonic() - tick,
                    }
                )
            hypothesis = " ".join(output).strip()
        else:
            source, rate = load_source(audio_path)
            segmenter = SpeechSegmenter(
                SegmenterConfig(max_segment_seconds=args.chunk_seconds, close_after_silence=0.5),
                detector=SileroSpeechDetector(),
            )
            work = []
            for offset in range(0, len(source), args.block_samples):
                chunks = segmenter.push(source[offset : offset + args.block_samples], rate)
                work.extend(
                    AudioWork(
                        chunk,
                        start,
                        duration,
                        0,
                        final=final,
                        revision=reason == CLOSE_REVISION,
                        reason=reason,
                    )
                    for chunk, (start, duration), reason, final in zip(
                        chunks,
                        segmenter.completed_times,
                        segmenter.completed_reasons,
                        segmenter.completed_final,
                        strict=True,
                    )
                )
            tail = segmenter.flush()
            if segmenter.flushed_revision:
                chunk, start, duration = segmenter.flushed_revision
                work.append(AudioWork(chunk, start, duration, 0, revision=True))
            work.extend(
                AudioWork(chunk, start, duration, 0, final=final, reason=reason)
                for chunk, (start, duration), reason, final in segmenter.flushed_following
            )
            if tail is not None:
                start, duration = segmenter._emitted_time
                work.append(AudioWork(tail, start, duration, 0))
            reconciler, gate = Reconciler(), HallucinationFilter()
            context = RecognitionContext(language or "")
            output = []
            detected = None
            for item in work:
                chunk, start, duration = item.audio, item.start, item.duration
                tick = time.monotonic()
                hint = context.prepare(model, item)
                result = model.transcribe(chunk, language=hint, context=context.text)
                text = (
                    ""
                    if gate.should_drop(result.text, result.speech_probability, result.confidence)
                    else result.text
                )
                stitched = (reconciler.accept if item.final else reconciler.preview)(
                    text, start, duration, result.words
                )
                if item.final:
                    output.append(stitched)
                    context.remember(stitched, result)
                detected = result.language
                raw.append(
                    {
                        "start": start,
                        "duration": duration,
                        "raw": result.text,
                        "stitched": stitched,
                        "language": result.language,
                        "final": item.final,
                        "revision": item.revision,
                        "words": [
                            {
                                "text": w.text,
                                "start": start + w.start,
                                "end": start + w.end,
                                "probability": w.probability,
                            }
                            for w in result.words
                        ],
                        "inference_seconds": time.monotonic() - tick,
                    }
                )
            hypothesis = " ".join(output).strip()
        item = {
            "id": case["id"],
            "tags": case.get("tags", []),
            "text": hypothesis,
            "language": detected,
            "language_error": bool(language and detected != language),
            "wer": error_rate(case.get("scoring_reference", case["reference"]), hypothesis),
            "literal_wer": error_rate(case["reference"], hypothesis),
            "cer": error_rate(case["reference"], hypothesis, characters=True),
            "compute_seconds": time.monotonic() - begin,
            "chunks": raw,
        }
        if case.get("language_spans") and raw:
            errors = []
            for chunk in raw:
                if not chunk.get("final", True):
                    continue
                span = max(
                    case["language_spans"],
                    key=lambda s: max(
                        0,
                        min(s["end"], chunk["start"] + chunk["duration"])
                        - max(s["start"], chunk["start"]),
                    ),
                )
                errors.append(chunk["language"] != span["language"])
            item["language_detection_errors"] = sum(errors)
            item["language_detection_windows"] = len(errors)
        report["cases"].append(item)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"{case['id']}: WER={item['wer']['rate']:.3f}, CER={item['cer']['rate']:.3f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
