# Troubleshooting

SubbyAI tries to tell you what's wrong in the app itself: the Live screen names every state — hearing
silence, hearing music but no speech, running behind, or stopped — and offers the fix next to it.
This page is for what's left.

Logs: `%APPDATA%\SubbyAI\Logs\subbyai.log` (Windows) or `~/Library/Logs/subbyai/subbyai.log` (macOS).
Review and redact it before sharing when [reporting an issue](https://github.com/ChanJianHao/SubbyAI/issues).

## No captions appear

Watch the **level meter** on the Live screen. If it doesn't move, SubbyAI isn't hearing the device
that's playing. Choose **System audio** or **Microphone** directly on Live, then choose its device
under Settings → Everyday. Playback changes to headphones or an HDMI display can change the output.
**Follow my default** follows Windows' default within the chosen source kind. A named selection
waits for that device to reconnect instead of silently opening something else.

For microphone capture, check physical mute switches and OS microphone permission for SubbyAI.
On Windows, also allow microphone access for desktop applications. On macOS, system audio needs
a loopback device and permission for CoreAudio input capture.

Bluetooth calls may change the headset's profile, device ID, rate or channel layout. SubbyAI
refreshes the device list and reopens a changed source. If it disappears, reconnect it or choose
another source. Pressing Stop cancels automatic reconnection. The app captures one source at a
time; it does not mix your microphone and playback.

If the meter moves but nothing appears, SubbyAI is hearing sound without speech: music, effects, or a
voice too quiet against the mix. Try selecting the input language, or lowering "Ignore short noises" if speech is quiet in Settings → Audio & sources.

Some apps play audio in exclusive mode (a few bit-perfect music players); loopback can't see those.

## The first start takes a long time

The caption engine downloads once — roughly 78 MB to 3.1 GB depending on the quality you chose.
Progress, size and speed are shown on the Live screen, and you can cancel or resume. After that,
starting uses the local cache; model loading still takes time.

## Captions lag behind

SubbyAI will say so ("Captions are running behind") and keep them live by dropping the backlog rather
than drifting minutes behind. Choose Fast in Settings → Everyday, or switch the
compute device to your graphics card if you have one.

**Maximum Quality** can use substantially more memory and time. Use Balanced while watching
or gaming if it competes with playback. Advanced settings let you limit CPU threads and choose
how soon idle models release their memory after Stop. Recommendations estimate model fit from
hardware information; they do not promise a measured latency.

## My graphics card isn't being used

SubbyAI checks for a card once and remembers the answer. If you've just installed a driver or added
an eGPU, use "Check again" in Settings → Advanced Mode → Models & providers.

CUDA 12 needs an NVIDIA driver of 525 or newer — no CUDA toolkit install is needed. If the card
can't be used, SubbyAI falls back to the processor and says so rather than failing.

## Captions don't show over my game

Games in **exclusive fullscreen** own the display and nothing can draw over them — no overlay app
can, and SubbyAI won't pretend otherwise. In the game's video settings choose **Borderless** or
**Windowed fullscreen**; it looks identical and captions will appear. Or drag captions to a second
monitor.

SubbyAI does not inject code into games. Compatibility depends on the game and anti-cheat system.

## A shortcut doesn't work

Another app registered it first. Settings → Shortcuts shows which one failed; pick a different
combination. Shortcuts on the `Ctrl+Alt` cluster are least likely to clash.

## Translation is wrong or missing

Captions in the original language keep working when translation fails — that's deliberate. The
status area says which provider is unavailable.

For quality: the built-in translator is fast and completely private but literal. Connecting a local
model through Ollama or LM Studio gives noticeably better idiom and keeps names consistent, because
SubbyAI sends the previous few lines as context.

## The overlay is in the way

Turn on **click-through** (`Ctrl+Alt+T`) so the mouse passes through it. Since you then can't drag
it, use the same shortcut, the tray, or the Live screen to turn it back off. `Ctrl+Alt+O` hides and
shows captions entirely.

Lost the overlay on a monitor you unplugged? It re-centres automatically when its saved position is
no longer on any screen.

Settings → Captions lets you select a display, change width and line limits, choose installed fonts,
and save personal looks. Settings → General offers larger app text. High contrast disables caption
fades, and you can disable animations for other styles. Always-on-top can also be switched off.

## SubbyAI won't start / is already running

Only one copy runs at a time; launching again brings up a notice. Look in the system tray.

## macOS says the app is damaged

That's Gatekeeper on an unsigned build: right-click → Open → Open, or
`xattr -dr com.apple.quarantine "/Applications/SubbyAI.app"`.


## A speech server won't connect

Enable Advanced Mode and check Remote processing: use the server's base URL and exact model ID,
then allow audio to that address and save. Public HTTP and redirected endpoints are refused;
use HTTPS and the final endpoint. A 401/403 usually means the key needs replacing. A busy server
can be retried, but a transport failure isn't replayed automatically because it may already have
received the audio. Switch back to local processing to continue offline.

## Start stays disabled after Stop

The current phrase is still finishing. Native inference cannot be interrupted safely, so SubbyAI
waits before starting another run. The window stays usable. If this repeatedly takes too long,
choose Fast or a smaller exact model, and check the provider's timeout when using remote speech.

## My settings weren't saved

Check free disk space and permissions for the per-user settings folder. The app keeps the new
values for the current session and shows a warning when it cannot write them. A newer settings
schema needs a newer app; SubbyAI refuses to overwrite it during a downgrade.

## Some history couldn't be saved

Live captions continue when history storage fails. A separate warning remains visible even when
audio is healthy. Check free disk space and permissions for the per-user data folder, then start a
new session. A failed batch or an overflowing storage queue may leave gaps in that session's history;
the app does not claim those captions were saved. Copy needed text from Live before clearing it.

History saving is off by default. In General settings, choose original text, translated text, both,
or only session dates/languages. Session-only mode affects new sessions; it does not erase existing
history. Use the explicit delete controls to remove saved sessions.

## Reporting a problem safely

Enable Advanced Mode, open Diagnostics and use **Copy diagnostic information**. This includes
version, platform and bounded status/counters, without transcript text, device names, paths,
server addresses or credentials. Review any separately attached logs or screenshots before sharing.

## Damaged history database

If SQLite detects corruption, SubbyAI preserves the damaged database and journal files
in a `sessions.db.damaged-<timestamp>` folder beside the original database, then opens
a new empty history. Startup shows the recovery location. Keep that backup if you need
to attempt recovery; it may contain private transcripts and must not be attached to issues.
Permission and disk-space errors do not trigger recovery or erase existing data.
