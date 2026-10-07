# SubbyAI 1.0.0 — Your world, subtitled.

Live subtitles and translation for the sound your Windows computer plays.
Watch anime, Korean dramas, films and streams, or follow dialogue in games.
Choose a microphone for conversations, lectures and language practice.

## Start watching

Download **SubbyAI-Setup-1.0.0.exe**, install it for your Windows account and open
SubbyAI. Choose your sound source, spoken language, subtitle language and a
recommended quality setting. Press **Start captions**. Speech models download
once with progress and cancellation; translation packs download as needed.

Prefer a portable app? Extract **SubbyAI-portable-win-x64.zip** and open
**SubbyAI.exe** inside it. Keep the complete folder together.

## Made for everyday watching

- Original captions appear as soon as recognition finishes; translation joins the
  same caption when ready.
- Local recognition and translation, without an account, subscription or API key.
- Guided setup, an audio test meter and hardware-aware quality recommendations.
- Eight subtitle looks, your installed fonts, saved styles and a movable overlay.
- System sound and microphone selection, remembered settings and device recovery.
- Tray controls, Windows shortcuts, searchable settings and quick language changes.
- Optional searchable transcript history and TXT, SRT, VTT or Markdown export.
- Explicit consent for remote speech and optional local, LAN or cloud text providers.

Mochi, our original subtitle companion, ties together the Sakura and Ocean themes,
the installer and the project artwork. Gentle page transitions, switches, progress
feedback and status reactions respect reduced motion. The subtitle overlay stays calm.

## Requirements and privacy

Windows 10 1809 or later, or Windows 11, on an x64 computer. Allow about 3 GB
for the application, plus downloaded speech models and language packs. A processor
is enough for lightweight models; an optional NVIDIA graphics card helps with larger
models. Python and inference runtimes are included. No virtual audio cable is required.

Local live audio stays in memory. Transcript saving is off by default. There is no
telemetry or automatic crash upload. API keys use the operating system's credential
store. Remote processing sends audio or text only to the providers you choose and allow.

## Limitations

- V1 binaries are unsigned; Windows SmartScreen may show a warning.
- AI subtitles and translations can be wrong, especially for music, overlapping speech,
  very short phrases and mixed-language content. They are not professional captions.
- Language coverage depends on the speech model and available translation packs.
- One sound source is captured at a time. Per-application capture is not available.
- Exclusive-fullscreen games can cover the overlay; use borderless mode.
- The interface is in English. macOS packages await separate platform validation.

SHA256SUMS-windows.txt lists checksums for the exact release files.
The matching Qt/PySide source archive and third-party notices accompany the release.

[Windows setup](installation-windows.md) · [Troubleshooting](troubleshooting.md) ·
[Privacy](security.md) · [Report a problem](https://github.com/ChanJianHao/SubbyAI/issues)
