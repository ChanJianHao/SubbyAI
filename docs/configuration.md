# Configuration

Open Settings with `Ctrl+,`. **Everyday** is the default page. Its source, languages,
performance profile and subtitle preset are shortcuts to the same configuration used by
Advanced Mode. Hiding Advanced Mode never resets values, providers or credentials.

## Everyday choices

| Choice | Behavior |
|---|---|
| Audio source | System audio or Microphone; follow that source's default or select a named device |
| Language you'll hear | Automatic detection or an explicit input language |
| Subtitle language | Translation target; disable translation for original-only captions |
| Fast | Lightweight model, 3-second maximum phrases, 0.35-second silence, beam 1 |
| Balanced | Hardware recommendation capped at Detailed, 6 seconds, 0.5-second silence, beam 1 |
| Accurate | Detailed when the computer can support it, 10 seconds, 0.7-second silence, beam 3 |
| Maximum Quality | Strongest supported model, 12 seconds, 0.8-second silence, beam 5; highest resource use |
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
Automatic CPU threads leave about half the physical cores for playback and other apps.
The warm-model timer releases speech RAM/VRAM two minutes after stopping by default.
Set it to zero for immediate release or up to 30 minutes for quicker repeated starts.
Downloaded model files remain on disk until you remove them in Models & providers.

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
Use **Make this look my own** to start customizing a built-in look, then **Save this look**
to name it. Up to 20 saved looks retain appearance and behavior, excluding display names
and geometry. Select a saved look to apply it immediately. Always-on-top is optional.
Settings → General → App text size offers 125% and 150% independently of subtitle size
and operating-system display scaling. An oversized overlay is constrained to the available
display; fewer caption pairs can be visible when there is insufficient vertical space.

## Storage choices

Saving transcripts is opt-in. Settings → General lets you keep originals, translations,
both, or just session dates and language choices. No foreground app names are collected.
Excluded text is removed before the SQLite writer queue. Translation-only storage saves
only successful translations. **Until I stop captioning** writes no session history to disk
and clears Live when stopped; **Clear the Live transcript when I stop** works with any policy.
These choices apply to new captions. Previously saved sessions remain until you delete them
or retention expires. A fresh live-only installation creates no transcript database.
Audio and temporary speech files are never written by the live pipeline, so there is no
audio-recording toggle. Diagnostic logs contain operational events rather than captions.

Choose a display, drag the overlay in placement mode and enable click-through when ready.
Placement uses Qt's logical screen coordinates, including displays left of or above the primary.
Display and DPI changes reflow captions and constrain the panel to the available area.
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
