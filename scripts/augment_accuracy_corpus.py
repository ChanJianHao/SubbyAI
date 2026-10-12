"""Add controlled signal variants to a local manifest without changing reference text."""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
from evaluate_accuracy import load_source


def write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1 if audio.ndim == 1 else audio.shape[1])
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def main() -> None:
    root = Path(sys.argv[1])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    manifest = [c for c in manifest if "derived" not in c.get("tags", [])]
    jfk = {
        "id": "jfk",
        "audio": "jfk.flac",
        "language": "en",
        "reference": "And so my fellow Americans ask not what your country can do for you "
        "ask what you can do for your country",
        "tags": ["human", "historical"],
    }
    if not any(c["id"] == "jfk" for c in manifest):
        manifest.append(jfk)
    source, rate = load_source(root / "conversation.wav")
    mono = source.mean(axis=1)
    rng = np.random.default_rng(872)
    t = np.arange(len(mono)) / rate
    variants = {
        "quiet": mono * 0.012,
        "noise_music": mono + rng.normal(0, 0.009, len(mono)) + 0.025 * np.sin(2 * np.pi * 220 * t),
        "antiphase": np.stack([mono, -mono], axis=1),
        "boundary_shift": np.concatenate([np.zeros(int(rate * 0.37)), mono]),
    }
    reference = manifest[0]["reference"]
    for name, audio in variants.items():
        write_wav(root / f"{name}.wav", audio, rate)
        manifest.append(
            {
                "id": name,
                "audio": f"{name}.wav",
                "reference": reference,
                "language": "en",
                "tags": [name, "derived", "synthetic"],
            }
        )
    if all((root / f"fleurs-{code}.wav").exists() for code in ("ko", "ja", "zh")):
        parts, spans, refs = [], [], []
        cursor = 0.0
        for code in ("ko", "ja", "zh"):
            audio, native_rate = load_source(root / f"fleurs-{code}.wav")
            if native_rate != 16000:
                raise ValueError("FLEURS fixtures must be 16 kHz")
            parts.append(audio.mean(axis=1))
            spans.append({"start": cursor, "end": cursor + len(audio) / 16000, "language": code})
            cursor += len(audio) / 16000 + 0.8
            parts.append(np.zeros(12800, np.float32))
            refs.append(next(c["reference"] for c in manifest if c["id"] == f"fleurs-{code}"))
        write_wav(root / "code_switch.wav", np.concatenate(parts), 16000)
        manifest.append(
            {
                "id": "code_switch",
                "audio": "code_switch.wav",
                "reference": " ".join(refs),
                "auto_detect": True,
                "language_spans": spans,
                "tags": ["derived", "human", "code_switch"],
            }
        )
        audio, native_rate = load_source(root / "jfk.flac")
        overlap = audio.mean(axis=1)
        delayed = np.concatenate([np.zeros(native_rate), overlap])[: len(overlap)]
        write_wav(root / "overlap.wav", (overlap + delayed * 0.5) * 0.7, native_rate)
        manifest.append(
            {
                "id": "overlap",
                "audio": "overlap.wav",
                "reference": jfk["reference"],
                "language": "en",
                "tags": ["derived", "overlapping_speech"],
                "note": (
                    "Dominant-speaker reference; delayed same-speaker interference is a stress "
                    "fixture, not diarization ground truth."
                ),
            }
        )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
