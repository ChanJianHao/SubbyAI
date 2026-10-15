# Shortcut and Settings audit · 1.0.3

Issue #2 exposed a mismatch between setup and later navigation: setup introduced global shortcuts,
but normal Settings hid their editor and direct navigation silently enabled Advanced Mode.

## Changes and boundaries

| Area | Finding and resolution |
|---|---|
| Shortcuts | Always visible/searchable in normal Settings; direct navigation preserves the mode. Clear edit instructions distinguish global, focused-window and saved-transcript controls. |
| Editing | Reset was hover-only. Resets now remain keyboard reachable and named; conflicting individual resets use the same confirmation as rebinding. Escape, leaving the page and losing focus cancel capture. |
| Runtime | A settings edit registered everything twice. One controller now owns registration and persistence, including live store notifications. Failed edits restore previous bindings; unavailable saved keys remain marked and can be repaired independently. |
| Capture | Window accelerators were intercepted, but Windows global registrations remained active. Capture now releases those registrations, commits after key release and discards stale queued activations. Numpad/AltGr-specific chords are rejected instead of being misrepresented. |
| Definitions/help | Defaults were copied between storage and UI, and missing saved actions could appear bound without being registered. One framework-free catalogue and missing-value policy now supply storage, runtime, editor and the installed window/transcript accelerators. Explicit clears remain cleared. Removed the unimplemented Ctrl+Space claim; transcript actions have an explicit focus scope. |
| Settings selection | Search could hide every item while retaining the old page. An explicit empty state clears selection and preview; clearing search or opening a destination restores a visible selection. One advanced-category set governs filtering and direct navigation. |
| Setup and help | Setup points to Settings → Shortcuts and Settings → Speech models. Corrected stale Models & providers and Advanced-only General paths in installation, troubleshooting and provider help. |
| Platform support | macOS setup no longer advertises functioning Windows global keys. Settings retains the page, explains availability and lists usable window/transcript keys. |
| Disabled controls | Startup captioning explains its start-with-computer dependency; transcript retention explains its saving dependency. Existing app-language, custom-style, model compatibility and language controls already explain their availability. |

## Information architecture review

Normal Settings contains Everyday, Languages & translation, Subtitle overlay, Audio & sources,
Speech models, Shortcuts, General and About. Advanced Mode adds Translation providers,
Transcription & performance, Remote Processing and Diagnostics. Engine tuning, endpoint/server
configuration, provider credentials and diagnostic internals remain advanced. Toggling visibility
preserves the effective settings and stored mode; it does not reset engine/provider choices.

Setup's source, languages, quality, subtitle appearance and saving choices all have normal Settings
destinations. Speech models already has a normal navigation entry and searchable catalogue.
Live, History, transcript export and setup replay have ordinary navigation/actions. Configuring a
translation provider from Languages is an explicit action that opens its advanced configuration.
No additional ordinary feature was found stranded solely behind Advanced Mode.

## Follow-up boundaries

Transcript assistance remains alongside provider configuration because it requires a configured
text provider and explicit consent. A broader presentation change there needs its own product work.
macOS global hotkeys, app-interface localization, combined audio sources and per-application capture
are unsupported capabilities, not hidden controls; this patch does not implement them. Physical
multi-monitor, special keyboard-layout and assistive-technology hardware coverage remains separate
from native Windows input, registration, DPI and Qt accessibility checks.

## Regression gates

Behavioral tests cover normal/advanced navigation, empty search and recovery, restart persistence,
edit/clear/reset/Undo/takeover, rejected edits, recovery, keyboard cancellation and capture isolation,
platform explanation, actual accelerator/help agreement and narrow layouts at larger text sizes.
The Windows-only acceptance script uses real RegisterHotKey competition, release, duplicate
ownership and restart; both CI and the existing release workflow run it on their Windows job.
The frozen self-check exercises discoverability, persistence and Settings selection in the shipped UI.
Native smoke tests exercise light/dark mode, keyboard capture, Settings search and narrow larger-text
layouts at multiple display scales. This evidence does not claim testing every physical keyboard
layout, audio device or monitor arrangement.

The full formatter check was compared with untouched main: 53 files originally required formatting;
47 unchanged legacy files still do. Every Python file changed for this fix is formatter-clean.
Formatting is not an existing CI/release gate; the configured full Ruff lint gate remains enforced.
