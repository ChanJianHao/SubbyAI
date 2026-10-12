"""Opt-in, bounded local pipeline evidence. Normal logs never receive content."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np


class PipelineTrace:
    """Disk I/O and hashing run on a separate writer, never on capture.

    SUBBYAI_TRACE_DIR enables numeric metadata. SUBBYAI_TRACE_CONTENT=1 also
    saves transcripts and lossless float32 .npy audio. A fresh subdirectory is
    created per session. Data is never included in exported normal diagnostics.
    """

    def __init__(
        self,
        directory: Path,
        *,
        content: bool = False,
        max_bytes: int = 256 * 1024**2,
        max_seconds: float = 1200,
    ):
        self.content = content
        self.directory = directory / f"session-{time.time_ns()}"
        self.directory.mkdir(parents=True, mode=0o700)
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self._started = time.monotonic()
        self._bytes = 0
        self._queue: queue.Queue = queue.Queue(256)
        self._closing = threading.Event()
        self.dropped = 0
        self._thread = threading.Thread(target=self._write, name="subbyai-trace", daemon=True)
        self._thread.start()

    @classmethod
    def from_environment(cls):
        directory = os.environ.get("SUBBYAI_TRACE_DIR")
        return (
            cls(Path(directory), content=os.environ.get("SUBBYAI_TRACE_CONTENT") == "1")
            if (directory)
            else None
        )

    def record(self, stage: str, *, audio=None, rate: int = 16000, content=None, **metadata):
        if self._closing.is_set() or time.monotonic() - self._started > self.max_seconds:
            return
        # Strip arbitrary strings: metadata-only traces must stay content-free.
        safe = {k: v for k, v in metadata.items() if isinstance(v, (int, float, bool))}
        item = (
            stage,
            safe,
            audio,
            rate,
            content if self.content else None,
            time.monotonic() - self._started,
        )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def close(self):
        self._closing.set()
        self._thread.join(timeout=5)

    def _write(self):
        sequence = 0
        try:
            with (self.directory / "events.jsonl").open("w", encoding="utf-8") as stream:
                while not self._closing.is_set() or not self._queue.empty():
                    try:
                        stage, metadata, audio, rate, content, elapsed = self._queue.get(
                            timeout=0.1
                        )
                    except queue.Empty:
                        continue
                    sequence += 1
                    event = {
                        "sequence": sequence,
                        "stage": stage,
                        "elapsed": elapsed,
                        **metadata,
                    }
                    if audio is not None:
                        event.update(
                            rate=rate,
                            frames=len(audio),
                            channels=(audio.shape[1] if audio.ndim == 2 else 1),
                            rms=float(np.sqrt(np.mean(audio**2))) if len(audio) else 0,
                            peak=float(np.max(np.abs(audio))) if len(audio) else 0,
                            clipped_samples=int(np.count_nonzero(np.abs(audio) >= 1)),
                            sha256=hashlib.sha256(audio.tobytes()).hexdigest(),
                        )
                        if self.content:
                            filename = f"{sequence:06d}-{stage}.npy"
                            if self._bytes + audio.nbytes + 256 > self.max_bytes:
                                self.dropped += 1
                                continue
                            np.save(self.directory / filename, audio, allow_pickle=False)
                            self._bytes += (self.directory / filename).stat().st_size
                            event["audio_file"] = filename
                    if content is not None:
                        event["content"] = content
                    line = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
                    if self._bytes + len(line.encode("utf-8")) > self.max_bytes:
                        self.dropped += 1
                        continue
                    stream.write(line)
                    stream.flush()
                    self._bytes += len(line.encode("utf-8"))
                stream.write(
                    json.dumps({"stage": "trace_complete", "dropped": self.dropped}) + "\n"
                )
        except (OSError, ValueError):
            # Debug recording is best-effort and can never stop captions.
            self.dropped += 1
            self._closing.set()
