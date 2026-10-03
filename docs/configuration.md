# Configuration

Open Settings with `Ctrl+,`. **Everyday** is the default page. Its source, languages,
performance profile and subtitle preset are shortcuts to the same configuration used by
Advanced Mode. Hiding Advanced Mode never resets values, providers or credentials.

## Everyday choices

| Choice | Behavior |
|---|---|
| Audio source | Follow the default output or select a named device |
| Language you'll hear | Automatic detection or an explicit input language |
| Subtitle language | Translation target; disable translation for original-only captions |
| Fast | Lightweight model, 3-second maximum phrases, 0.35-second silence, beam 1 |
| Balanced | Hardware recommendation capped at Detailed, 6 seconds, 0.5-second silence, beam 1 |
| Accurate | Detailed when the computer can support it, 10 seconds, 0.7-second silence, beam 3 |
| Custom | Your technical changes remain active; choose a profile explicitly to replace them |
| Save my transcripts | Off by default; explicit preferences are preserved |

Phrase duration is a maximum, not a promised latency. Speech pauses can finish a caption sooner;
inference and translation add their own time. Overloaded queues drop older phrases to stay live.

Choosing a profile also restores automatic device/precision and clears an exact-model override.
A profile change is intentional; an Advanced Mode toggle has no such effect.

## Advanced categories

**Models & providers** manages exact Whisper/Parakeet models, downloads, vocabulary hints,
translation fallback order and optional transcript assistance. **Engine & performance** exposes
device, precision, beam size, CPU threads, maximum phrase length and silence duration.
These changes reconnect a running session after the previous workers finish.
Device, precision and beam size apply to Whisper; Parakeet uses CPU/int8 and
honors the CPU thread setting. Vocabulary hints apply to Whisper. Parakeet has
no automatic language detection: select the input language when translating.

**Remote processing** keeps local speech as the default. A remote choice needs a validated
URL, server model and explicit consent for that address. Keys use the OS credential store.
Changing the address clears consent. This form has a Save button because these values must be
validated together. Timeouts are 2–60 seconds per network operation, with 0–2 retries for a busy
server. A slow or unreachable server leaves a recoverable inline error.

**Diagnostics** shows phase, queue sizes, drops and timing measurements. Copying the report
includes no caption content, keys, server URLs or home paths. Diagnostics don't upload anything.

## Subtitles and appearance

The preset picker previews Clean, Cinema, Anime, Cute, Minimal, Gaming, Large text and Accessibility, plus Custom.
Custom controls include installed font family and weight, size, original and translation colours,
outline width, shadow, background opacity, padding, corner radius, alignment, width, caption
history depth and a 1–8 line limit per language. Clipped overlay text ends in an ellipsis; the
complete caption remains in the Live page and saved history when enabled.

Choose a display, drag the overlay in placement mode and enable click-through when ready.
Placement uses Qt's logical screen coordinates, including displays left of or above the primary.
If a display disappears, the overlay moves back onto an available screen. Mixed-DPI behavior
still needs the physical-monitor checks in the release checklist.

General settings offer System / Light / Dark, Sakura / Ocean accents, tray behavior, startup,
transcript storage and setup. All controls have keyboard focus states. High contrast disables
caption motion. No mascot animation runs continuously.

## Stored configuration

On Windows settings and history live under `%APPDATA%\SubbyAI`; on macOS under
`~/Library/Application Support/subbyai`. Logs use the platform's per-user log folder.
API keys use Windows Credential Manager or macOS Keychain. See `src/subbyai/paths.py`
for exact paths and export locations.

The versioned schema receives a backup before supported format upgrades and preserves
explicit privacy choices. The loader bounds file size, strings, geometry, language codes and
numeric values, and rejects non-finite numbers. Corrupt JSON is backed up. Unknown fields are
ignored; a newer schema is refused without overwriting it, so upgrading is safer than downgrading.

Writes use a temporary file and atomic replace. If disk space or permissions prevent saving,
the app reports it and your changes remain usable for the current session.
