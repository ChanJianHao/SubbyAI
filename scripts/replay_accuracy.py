"""Replay a file through real threaded capture/ASR/translation/overlay at live speed.

Uses installed models only. Saves content intentionally to --out; an optional
developer trace can retain lossless input, model chunks and each intermediate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from evaluate_accuracy import error_rate, load_source


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from subbyai.asr.engine import TranscriptionEngine
    from subbyai.audio.base import AudioDevice
    from subbyai.core.events import RecognitionState
    from subbyai.core.settings import SessionConfig, Settings
    from subbyai.pipeline.captioner import Captioner
    from subbyai.translation.builtin import ArgosProvider
    from subbyai.translation.chain import TranslationChain
    from subbyai.ui.overlay import CaptionOverlay

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case", default="jfk")
    parser.add_argument("--model", default="base")
    parser.add_argument("--target", default="")
    parser.add_argument("--translation-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    case = next(
        c for c in json.loads(args.manifest.read_text(encoding="utf-8-sig")) if c["id"] == args.case
    )
    source, rate = load_source(args.manifest.parent / case["audio"])
    finished = threading.Event()

    class FileCapture:
        def start(self, device, callback):
            self.cancel = threading.Event()
            self.started = time.monotonic()

            def feed():
                for begin in range(0, len(source), 1024):
                    block = source[begin : begin + 1024]
                    deadline = self.started + (begin + len(block)) / rate
                    if self.cancel.wait(max(0, deadline - time.monotonic())):
                        break
                    callback(block, rate)
                finished.set()

            self.thread = threading.Thread(target=feed, daemon=True)
            self.thread.start()
            return AudioDevice("replay", "File replay", rate, source.shape[1], True)

        def stop(self):
            self.cancel.set()
            self.thread.join()

    settings = Settings()
    settings.captions.source_language = "" if case.get("auto_detect") else case.get("language", "")
    settings.captions.target_language = args.target
    settings.history.enabled = False
    config = SessionConfig.from_settings(settings)
    engine = TranscriptionEngine(args.model, "cpu")
    engine.cpu_threads = 4
    engine.beam_size = config.beam_size
    engine.load()
    translator = None
    if args.target:
        provider = ArgosProvider(args.translation_dir)
        source_language = case.get("language", "")
        if not provider.supports(source_language, args.target):
            parser.error("Translation pack must already be installed; this replay never downloads.")
        translator = TranslationChain([provider])
    app = QApplication.instance() or QApplication([])
    overlay = CaptionOverlay(settings)
    capture = FileCapture()
    pipeline = Captioner(lambda c: engine, lambda c: translator, capture_factory=lambda: capture)
    captions, events, errors = {}, [], []
    stopped = threading.Event()

    def display(segment, *, update=False):
        (overlay.update_segment if update else overlay.show_segment)(segment)
        captions[segment.id] = segment
        elapsed = time.monotonic() - capture.started
        events.append(
            {
                "caption_id": segment.id,
                "elapsed": elapsed,
                "latency_after_audio_end": elapsed - segment.audio_start - segment.audio_duration,
                **asdict(segment),
            }
        )

    pipeline.segment_ready.connect(display)
    pipeline.segment_updated.connect(lambda s: display(s, update=True))
    pipeline.error_occurred.connect(errors.append)
    pipeline.stopped.connect(stopped.set)
    overlay.subtitle_presented.connect(lambda payload: pipeline.record_rendered(**payload))
    pipeline.start(config, None)
    stop_requested = False
    deadline = time.monotonic() + len(source) / rate + 120
    while not stopped.is_set() and time.monotonic() < deadline:
        app.processEvents()
        if finished.is_set() and not stop_requested:
            pipeline.stop()
            stop_requested = True
        time.sleep(0.005)
    if not stopped.is_set():
        pipeline.stop(drain=False)
        errors.append("Replay exceeded its deadline")
    pipeline.wait(30)
    app.processEvents()
    completed_trace = pipeline._trace
    trace_deadline = time.monotonic() + 2
    while pipeline._trace is not None and time.monotonic() < trace_deadline:
        app.processEvents()
        time.sleep(0.005)
    if completed_trace is not None:
        completed_trace.close()
    final = [s for s in captions.values() if s.recognition_state is RecognitionState.FINAL]
    text = " ".join(s.text for s in final)
    result = {
        "case": args.case,
        "model": args.model,
        "source_seconds": len(source) / rate,
        "text": text,
        "wer": error_rate(case["reference"], text),
        "cer": error_rate(case["reference"], text, characters=True),
        "metrics": asdict(pipeline.metrics),
        "events": events,
        "errors": errors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    overlay.close()
    print(f"{args.case}: WER={result['wer']['rate']:.3f}, {len(final)} finalized captions")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
