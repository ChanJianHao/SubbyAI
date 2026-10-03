"""Entry point: ``python -m subbyai`` or the packaged executable."""

from __future__ import annotations

import sys


def main() -> int:
    # The packaged app re-execs itself to probe for a graphics card, so the
    # CUDA runtime is loaded in a child that exits rather than in the app.
    if "--probe-cuda" in sys.argv:
        from .asr.capability import probe_cuda_in_process

        print(probe_cuda_in_process())
        return 0

    # A fixed self-check, run by scripts/verify_package.py against the frozen
    # build. Deliberately not an "execute this string" flag: a shipped binary
    # should not carry a code-execution entry point just to make testing easier.
    if "--self-check" in sys.argv:
        import argparse
        from pathlib import Path

        from .self_check import run_self_check

        parser = argparse.ArgumentParser(description="Run fixed application diagnostics.")
        parser.add_argument("--self-check", action="store_true")
        parser.add_argument("--require-models", action="store_true")
        parser.add_argument("--require-gpu", action="store_true")
        parser.add_argument("--speech-fixture", type=Path)
        parser.add_argument("--model-cache", type=Path)
        options = parser.parse_args()
        return run_self_check(
            options.require_models, options.require_gpu, options.speech_fixture, options.model_cache
        )

    from .app import run

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
