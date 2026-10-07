# Release process

V1 is version **1.0.0**. Repository, release tag and application metadata must agree.
Releases use the locked Python/dependency baseline and SHA-pinned CI actions. The build
workflow produces reviewed artifacts; repository visibility is managed separately.

## Automated gates

```powershell
uv sync --locked --extra dev
uv run --no-sync ruff check src tests scripts
uv run --no-sync pytest
uv run --no-sync python scripts/check_release_version.py
uv run --no-sync python scripts/audit_privacy.py --history
uv run --no-sync python scripts/audit_dependencies.py
uv run --no-sync python scripts/prepare_release.py
uv run --no-sync python scripts/prepare_smoke_models.py
uv run --no-sync python scripts/build_app.py
uv run --no-sync python scripts/verify_package.py --require-models
uv run --no-sync python scripts/audit_privacy.py --artifacts dist/SubbyAI
./scripts/build_installer.ps1
```

Run Gitleaks over both the release tree and every reachable commit, with redacted reports.
Never suppress a failed privacy or vulnerability gate merely to build. Network audit failures
are failures, not clean results. See [repository hygiene](repository-hygiene.md).

## Distribution

- Windows per-user x64 installer and portable ZIP containing the complete app directory.
- V1.0.0 publishes Windows artifacts. macOS ARM64 and Intel DMGs require separate
  runner and hardware validation before distribution.
- SHA-256 checksums, runtime inventory, dependency notices and corresponding native source.

**Decision: V1 ships unsigned.** Install guides explain SmartScreen and Gatekeeper.
Checksums detect changed downloads; they do not authenticate the publisher.

Native source must match the distributed Qt/PySide build. Run
`uv run --no-sync python scripts/prepare_native_sources.py` and ship its source ZIP
alongside the binaries. Separate Qt libraries remain replaceable; reverse engineering
for debugging library modifications is permitted. See [native libraries](native-libraries.md)
and [Qt's obligations](https://www.qt.io/development/open-source-lgpl-obligations).
The frozen application excludes PyAV and FFmpeg; NumPy audio frames go directly to recognition.

## User acceptance

Record results against each exact binary and checksum:

- Clean install, setup, audio test, model cancellation/retry and portable launch.
- Settings round trip, corrupt/future schema, missing cache and unwritable storage.
- Install over the same app directory; uninstall retains user data and unrelated files.
- Real recognition/translation and repeated start/stop/device changes.
- Bluetooth, USB and HDMI reconnect; changing default output.
- Displays left/above primary, mixed scaling, moving/removing the overlay's display.
- CPU and NVIDIA inference, resource pressure and a long viewing session.
- macOS input/BlackHole capture, permission prompts and unsigned installation flow.
- Local/LAN/HTTPS servers, authentication errors, slow responses and connection loss.

Hardware tests need actual devices. Automated tests and packaging cannot substitute for physical
GPU/audio/display acceptance. Do not claim a tested configuration without evidence.

The Windows compiler bootstrap uses the immutable Inno Setup 6.7.3 release from
its official repository, verifies its pinned SHA-256 and publisher signature, and
keeps the compiler in ignored build tools. GitHub Actions account billing restrictions
can prevent runners from starting; that is an infrastructure failure, not a passed gate.
