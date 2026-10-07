# Windows installation

## Requirements

- Windows 10 1809+ x64 or Windows 11
- ~3 GB for the installed app (allow 4 GB while downloading/installing), plus the caption engine you choose (about 78 MB to 3.1 GB)
- Optional: an NVIDIA graphics card for the fastest, most accurate captions

## Install

1. Download `SubbyAI-Setup-<version>.exe` from
   [Releases](https://github.com/ChanJianHao/SubbyAI/releases).
2. Run it. The installer is per-user — **no administrator rights**.
3. Launch SubbyAI. Guided setup chooses a source, quality, languages and subtitle appearance.
   The audio meter helps confirm your selected device. Every step can be skipped.
4. Press Start. Your first model download may take several minutes; progress and cancellation
   are available in Live. Later starts use the downloaded cache.

A portable `SubbyAI-portable-win-x64.zip` is also published — extract anywhere and run `SubbyAI.exe`.

Unsigned builds show a SmartScreen warning: **More info → Run anyway**.

## How it hears your computer

SubbyAI uses **WASAPI loopback** on the output device you pick. Whatever plays through that device
gets captioned, whichever app produced it. No cables, no "stereo mix", no virtual drivers, and no
microphone permission — nothing is recorded from a microphone unless you explicitly choose one.

For conversations or lectures, choose **Microphone** on Live or in Everyday settings. Choose
your default microphone or a named device. Enable microphone access for desktop applications
in Windows Settings → Privacy & security → Microphone if the meter stays still. V1 captures one
source at a time. It never switches from missing system audio to a microphone on its own.

The release includes Python and native inference libraries. Local use does not require installing
Python, Git, FFmpeg, a compiler, a CUDA toolkit or a model manager separately.

## Graphics card

If an NVIDIA card is present SubbyAI uses it automatically. The CUDA libraries are bundled; there is
nothing to install and no DLLs to copy. If the card cannot be used, SubbyAI falls back to the
processor and tells you.

The check runs once in a separate short-lived process and is remembered, so it costs nothing on
later launches. If you change hardware, use "Check again" in Settings → Advanced Mode → Models & providers.

## Running in the background

Closing the window keeps SubbyAI in the tray so shortcuts and captions keep working; you'll be told
the first time. Settings → Advanced Mode → General can start SubbyAI with Windows (off by default)
and start captioning automatically.

## Where things live

| What | Where |
|---|---|
| Settings | `%APPDATA%\SubbyAI\settings.json` |
| Transcripts | `%APPDATA%\SubbyAI\sessions.db` |
| Caption engines | `%APPDATA%\SubbyAI\models\` |
| Logs | `%APPDATA%\SubbyAI\Logs\` |
| Exports | `Documents\SubbyAI\` |

## Upgrading and uninstalling

Installing an update over SubbyAI keeps your settings, transcripts and downloaded models.
Versioned configuration backups protect supported schema upgrades.

Uninstall from Windows Settings → Apps. Transcripts, settings and downloaded engines are kept
deliberately, so reinstalling restores your setup — delete the folders above for a clean wipe.
