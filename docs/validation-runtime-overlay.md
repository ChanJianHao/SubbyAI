# Overlay and runtime UX validation

Validated on Windows on 2026-10-08 and 2026-10-09. Generated evidence is isolated under `build/validation`;
tests and probes do not change the user's installed settings or model cache.

## Root causes

- The default auto-hide path never mapped the overlay window. Caption events updated its
  layout and opacity, but opacity animation cannot show a hidden native window. Live settings
  also failed to apply the requested visibility reliably.
- The onboarding footer's nested aligned grid compressed Start below its style size hint,
  clipping the label under Windows display scaling. Windows also drew a native default-button
  frame over the themed rounded border. The footer now honors normal layout hints and Enter
  navigation belongs to the dialog, preserving the rounded button's own visual states.
- Model UI used persisted selection as runtime truth. Independent download workers in settings
  and the controller allowed divergent feedback and jobs. Installation and activation now have
  distinct observable lifecycles.
- Overlay height changes repeatedly clamped a fixed top edge, causing upward position drift.
  Resetting saved placement also failed to reposition the live window.
- A completed transfer could be mistaken for an installed model before verification, and an
  integrity failure could reuse the same bad cache on retry. Required assets and pending/invalid
  verification markers now govern installation availability.
- Explicit Quit could be rejected by close-to-tray after services had already shut down.

## Executed checks

| Workflow | Evidence and result |
|---|---|
| Real system audio → Whisper → French translation → Live and overlay | `scripts/validate_desktop_flow.py --auto`; actual public spoken-audio fixture through Windows loopback. Matching caption events and translations reached the mapped native overlay. |
| Overlay on/off/on, live font changes, position reset | Immediate changes without restarting; same native window retained. |
| Main window hidden, native stacking and focus | Overlay remained mapped; Win32 topmost, transparent-input and no-activate flags asserted. Overlay ranked above the current foreground window without collecting its identity. |
| Language and Auto Detect | Real session reconnects received English and automatic detection configurations. Search, keyboard selection and unsupported-language restrictions also have regression tests. |
| Model switch and switch back | Tiny → Small → Tiny; Small generated actual captions, old weights were unloaded, and selected model/overlay preferences survived settings reload. |
| Actual installation, cancellation and resume | Public Whisper Tiny English downloaded into a fresh test cache; Checking → Downloading → Cancelling → Cancelled → Checking → Verifying → Installed. GUI timer continued running. Installation did not change selection. |
| Explicit activation after install | Loading → Ready → Active; Tiny English generated real loopback captions in the overlay. Quit completed normally. |
| Failed download/retry, incomplete cache, failed load/rollback | Automated lifecycle tests with controlled failures; corrupt-cache retry forces replacement, failed switching restores the previous engine when possible. |
| Native DPI rendering | Qt Windows backend at effective 100%, 125%, 150%, 200% and 250%; dark/light surfaces rendered without Qt paint errors. Compact layouts and larger app text are covered by layout/button tests. |

The repeatable desktop script writes `desktop-acceptance.json` and the real installation script
writes `model-install.json`. Run `scripts/smoke_ui.py` with the desired `QT_SCALE_FACTOR` to
capture UI variants. `scripts/inspect_onboarding.py --capture` captures the final Start step.
The install probe needs network access; the desktop probe needs the isolated model/translation
fixtures and public WAV referenced in its header. Synthetic layout checks do not establish
actual mixed-monitor compositor behavior.

## Local Windows deployment

The verified build was installed over the existing per-user Windows installation on
2026-10-09 using the project's Inno Setup installer. This local update retains version 1.0.0.

- The final source suite passed all 772 tests. Ruff, dependency advisory checks, source/history
  secret and privacy scans, and the packaged artifact privacy scan also passed.
- The frozen Windows app passed real Whisper and Parakeet speech recognition, French
  translation, and Qt overlay checks without skipped model checks. First-run launch and
  clean shutdown passed with an isolated Unicode profile and a clean PATH; the packaged
  app also passed the 200% effective display scaling launch check.
- All 436 installed application files matched the tested bundle's SHA-256 manifest.
  No obsolete files remained in the installed runtime. Installer exit code was zero.
- The installed executable independently passed the native self-check, real speech and
  translation checks, first-run launch, and clean shutdown using isolated test profiles.
- The user's settings checksum was unchanged after installation and validation.
  A copy of the previous installed program files is retained under
  `build/deployment-backup/installed-before-overlay-fix` for rollback.

Evidence: `build/validation/local-deployment.json`, `deployment-bundle-manifest.json`,
`installed-self-check.log`, `installed-native-launch.log`, and `local-install.log`.
The deployment report contains checksums and results without the user's installation path
or settings contents. This local deployment does not resolve the browser and physical-display
validation gaps below.

### Version and build date update

The subsequent local update on 2026-10-09 adds the version and build datetime to
the main window's bottom-right footer and Settings → About. Build metadata is generated
by the PyInstaller specification and embedded in the executable. The UI displays local
time with its UTC offset; the tooltip also includes UTC. Restarting preserves the timestamp.
The deployed identifier is `v1.0.0 · Built 2026-10-09 23:23:48 UTC+08:00`.

All 64 focused branding, layout, settings and self-check tests passed. Native rendering
passed in dark/light modes at effective 125% and 200% Windows scaling, with normal and
150% app text, at the minimum window width. The packaged and installed executables passed
build-identity, real speech, translation, overlay, first-run launch and clean shutdown checks.
All 436 installed file checksums matched; settings were unchanged. The source and changed
artifact privacy scan reported no findings.

The local deployment replaced the executable and regenerated standard-library archive;
all other bundled files were unchanged. Previous files were preserved for rollback.
Evidence is in `build/validation/version-local-deployment.json` and
`build/validation/version-bundle-manifest.json`.

### Tray restore update

Deployed locally on 2026-10-10. The previous Open handler only called `show()`,
which retained a minimized window's state and could leave a natively hidden window
absent while Qt still reported it as visible. Both failures were reproduced through
the tray QAction. Double-click activation also lacked an Open handler.

Open now closes the tray popup, coalesces click notifications, and restores on the
next GUI turn. A shared shell method handles startup and reopening: it clears the
minimized flag while preserving maximized state, explicitly maps/restores the Windows
window, then raises and activates it. Quit suppresses pending Open requests. The
subtitle overlay is not involved and captioning is not restarted.

- All 780 regression tests passed, including tray signals, popup timing, repeated
  restoration, maximized-state retention, quit behavior and controller wiring.
- Source and frozen native Windows checks at 200% scaling passed for close-to-tray,
  minimized, maximized/minimized and externally hidden windows, retaining the same HWND.
- The installed executable independently passed those tray checks, real speech,
  translation, overlay checks, first-run launch and clean shutdown.
- All 436 installed files matched the tested bundle; settings were unchanged.
  The source and changed-artifact privacy scan found no issues. Rollback files are retained.
- The real user-profile launch was verified with Win32: one main window, natively visible
  and not minimized. Computer Use also found the window and read its accessibility tree.

The current identifier is `v1.0.0 · Built 2026-10-09 23:55:04 UTC+08:00`.
Evidence: `build/validation/tray-local-deployment.json`, `tray-all-tests.log`,
`tray-installed-self-check.log`, and `tray-real-profile-launch.json`.

## Release checks still required

Browser automation stopped because the computer-use provider could not determine Brave's
current URL. No browser input was attempted after that stop. Consequently this validation
does **not** establish spoken video playback in foreground/maximized/fullscreen Brave, Chrome,
Edge or Firefox, nor playback in external desktop media players. The native audio and stacking
checks are useful evidence but do not substitute for that application matrix.

Physical multiple monitors with mixed DPI, unplug/replug, GPU inference, exclusive fullscreen
and extended-duration playback also remain unverified here. Run the user's requested browser
and monitor matrix before release sign-off. The P0 fix is implemented and works in the native
end-to-end probe; this report is not complete release approval.
