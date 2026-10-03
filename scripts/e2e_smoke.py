"""Live end-to-end check (Windows): real system audio, real model, real pipeline.

Speaks a sentence through the speakers with Windows TTS, captures it back
through loopback, and asserts captions come out — optionally translated, which
exercises the dual-language path all the way through.

Not part of the pytest suite: it needs audio hardware and downloads a model.

    python scripts/e2e_smoke.py            # captions only
    python scripts/e2e_smoke.py --translate ja  # also translate into Japanese
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QCoreApplication

from subbyai.asr.engine import default_cache
from subbyai.core import logging_setup
from subbyai.core.settings import SessionConfig, Settings
from subbyai.pipeline import Captioner, PipelinePhase

SENTENCE = "The quick brown fox jumps over the lazy dog. This is a test of the caption system."


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform != "win32":
        print("This smoke test drives Windows TTS; run it on Windows.")
        return 0

    translate_to = ""
    if "--translate" in sys.argv:
        index = sys.argv.index("--translate")
        translate_to = sys.argv[index + 1] if len(sys.argv) > index + 1 else "ja"

    logging_setup.setup(verbose=True)
    app = QCoreApplication(sys.argv)

    settings = Settings()
    settings.captions.model_override = "tiny"
    settings.captions.compute_device = "cpu"
    settings.captions.source_language = "en"
    settings.captions.target_language = translate_to
    settings.history.enabled = False
    config = SessionConfig.from_settings(settings)

    originals: list[str] = []
    translations: list[str] = []
    errors: list[str] = []

    def engine_provider(cfg):
        engine = default_cache().get(cfg.model_name, cfg.compute_device)
        engine.load()
        return engine

    def translation_provider(cfg):
        from subbyai.translation.builtin import ArgosProvider
        from subbyai.translation.chain import TranslationChain

        chain = TranslationChain([ArgosProvider()])
        chain.prepare(cfg.source_language or "en", cfg.target_language)
        return chain

    captioner = Captioner(
        engine_provider=engine_provider,
        translation_provider=translation_provider if translate_to else None,
    )
    captioner.segment_ready.connect(lambda seg: originals.append(seg.text))
    captioner.segment_updated.connect(
        lambda seg: translations.append(seg.display_translation or "")
    )
    captioner.error_occurred.connect(errors.append)
    captioner.status_message.connect(lambda m: print(f"[status] {m}", flush=True))

    captioner.start(config, None)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline and captioner.phase is not PipelinePhase.RUNNING:
        app.processEvents()
        if errors:
            print("ERRORS:", errors)
            return 1
        time.sleep(0.05)

    print("[e2e] listening — speaking the test sentence…", flush=True)
    started = time.monotonic()
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = -1; $s.Speak('{SENTENCE}')",
        ],
        check=True,
    )
    spoken_for = time.monotonic() - started

    settle = time.monotonic() + 25
    while time.monotonic() < settle:
        app.processEvents()
        time.sleep(0.05)
        if len(originals) >= 2 and (not translate_to or len(translations) >= 2):
            break

    captioner.stop()
    captioner.wait(timeout=10)
    app.processEvents()

    print(f"\nSpoke for {spoken_for:.1f}s")
    print("Captions:")
    for text in originals:
        print("  >", text)
    if translate_to:
        print(f"Translations ({translate_to}):")
        for text in translations:
            print("  >", text or "(none)")

    joined = " ".join(originals).lower()
    passed = any(word in joined for word in ("fox", "caption", "test"))
    if translate_to:
        passed = passed and any(t.strip() for t in translations)
        passed = passed and all("\u2581" not in t and "<0x" not in t for t in translations)
    print("\nE2E:", "PASS" if passed else "FAIL")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
