# Development

Use the Python version in `.python-version` and the complete `uv.lock` dependency lock.
Build targets are Windows x64 and macOS 15 ARM64/Intel. ONNX Runtime is pinned separately
on Intel macOS because newer versions do not provide compatible wheels.

```powershell
python -m pip install --require-hashes --only-binary=:all: -r packaging/bootstrap.txt
uv sync --locked --extra dev
uv run --no-sync python -m subbyai
uv run --no-sync ruff check src tests scripts
uv run --no-sync pytest
```

Add `--extra gpu` to sync NVIDIA runtime wheels on Windows. `uv lock --check` rejects a
stale lock; install/sync verifies locked distribution hashes. Dependency updates require
source tests, vulnerability checks and frozen-app checks.
`packaging/constraints.txt` records the native baseline; `uv.lock` is authoritative for setup.

Tests use temporary application data and fake credential stores. Unit tests never send data
to external services. Local protocol tests may open loopback sockets. Layout tests use installed
fonts, which are not bundled assets.

## Validation and build tools

| Script | Purpose |
|---|---|
| `capture_ui.py` | Render actual widgets with fictional captions and no audio |
| `smoke_ui.py` | Exercise navigation, switches, notices, progress and reduced motion |
| `smoke_windows_launch.py` | Launch/close the frozen app with an isolated Unicode profile |
| `make_icon.py` | Generate original Qt artwork and application icons |
| `e2e_smoke.py --translate fr` | Exercise Windows loopback, recognition and local translation |
| `audit_privacy.py --history` | Inspect source, reachable history and optional binary payloads |
| `audit_secrets.py` | Run hash-pinned Gitleaks over source and history |
| `audit_dependencies.py` | Check all locked versions against OSV |
| `prepare_release.py` | Collect license texts and dependency versions |
| `prepare_smoke_models.py` | Download pinned real-model validation fixtures |
| `build_app.py` | Freeze the app with an isolated native-library search path |
| `verify_package.py --require-models` | Verify frozen UI, recognition and translation without skips |

Real audio checks can download models and play a public sentence through your output.
Run them without private calls or confidential media playing. They use local inference.

## Engineering contracts

UI motion lives in `ui.motion` and `ui.motion_widgets`; use their interruptible
tweens, page stack, reveal helpers and painted controls. Timings and distances
come from theme tokens. Actions and accessible values update immediately;
animations only follow their visual state. Hidden surfaces stop their animation
clocks, and page snapshots are released on completion, hiding or resizing.
Windows' Animation effects preference and General → Reduce motion settle active
transitions. Check native rendering with `QT_QPA_PLATFORM=windows` and repeat
`smoke_ui.py` at `QT_SCALE_FACTOR=1`, `1.5` and `2`. Screenshots and timing results
are written to ignored build output with fictional content and temporary data.

- Keep heavy work off the GUI and audio callback. Stop returns promptly; completion comes later.
- Emit original captions without waiting for translation; update them by stable event ID.
- Respect frozen session settings. Hiding Advanced preserves the effective configuration.
- Bound requests, queues, buffers and caches; make overload visible.
- Avoid modal pipeline errors and unexpected focus changes during playback.
- Use theme tokens; technical controls belong in Advanced Mode.
- Keep credentials, captions and identifying device/server details out of diagnostics.
- Test dangerous state transitions and failure boundaries, rather than merely mirroring wiring.

See [architecture](architecture.md), [providers](providers.md) and [release](release.md).
