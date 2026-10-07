# macOS installation

## Requirements

- macOS 15 or newer for the release builds
- Apple silicon or Intel — download the matching DMG

## Install

1. Download `SubbyAI-macos-<arch>.dmg` from
   [Releases](https://github.com/ChanJianHao/SubbyAI/releases).
2. Drag **SubbyAI** to Applications.
3. V1 is unsigned. Attempt to open the app, then use **System Settings → Privacy & Security →
   Open Anyway** if macOS blocks it. Review the app name before confirming. See
   [Apple's opening instructions](https://support.apple.com/guide/mac-help/mh40617/mac).

## Hearing your computer's audio

SubbyAI captures from *input* devices on macOS. To caption system audio you need a
loopback device:

1. Install [BlackHole 2ch](https://existential.audio/blackhole/) (free, open source).
2. In **Audio MIDI Setup**, create a **Multi-Output Device** containing both your speakers and
   BlackHole 2ch, and select it as the system output — so you still hear everything.
3. In SubbyAI, choose **System audio**, then **BlackHole 2ch** as the device.

To caption a microphone instead, choose **Microphone** on Live or in Everyday settings, then
select your default or named microphone. These sources are separate: a missing loopback device
never opens a microphone automatically. V1 captures one source at a time.

V1 uses input/loopback devices; native per-application system-audio capture is not supported.

## Permissions

- **Microphone** — macOS permission for CoreAudio input capture, including a virtual loopback
  input. Allow SubbyAI in System Settings → Privacy & Security → Microphone when prompted.
- No screen recording or accessibility permission is used. Optional remote servers receive audio only after your consent.

## Known gaps on macOS

- Global shortcuts are Windows-only for now. SubbyAI reports them as unavailable rather than
  silently doing nothing.
- Speech recognition uses the processor; Metal acceleration is not supported in V1.

## Where things live

| What | Where |
|---|---|
| Settings | `~/Library/Application Support/subbyai/settings.json` |
| Transcripts | `~/Library/Application Support/subbyai/sessions.db` |
| Caption engines | `~/Library/Application Support/subbyai/models/` |
| Logs | `~/Library/Logs/subbyai/` |
| Exports | `~/Documents/SubbyAI/` |

## Uninstalling

Drag SubbyAI to the Trash; delete the folders above for a clean wipe.
