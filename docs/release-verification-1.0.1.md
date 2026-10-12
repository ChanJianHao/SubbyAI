# SubbyAI 1.0.1 verification

This patch addresses [issue #1, overlay problem](https://github.com/ChanJianHao/SubbyAI/issues/1)
and verifies the audio/translation changes against actual model inputs. It supersedes earlier
local validation claims. Windows 11 25H2 was the available native test environment; the owner
accepted that environment for the issue's Windows 11 acceptance. No 24H2-specific claim is made.

## Overlay root causes and evidence

| Root cause | Reproduction/evidence | Correction |
|---|---|---|
| Enabled preference was confused with a visible, painted window | Installed 1.0.0 could have a checked control and no visible bar before speech; an empty mapped window painted no acknowledgment | Map the existing independent native window immediately; paint an enabled/waiting notice without starting ASR |
| Opacity changes did not map a hidden window | Caption wake and start/stop paths could retain a hidden QWidget | Explicit Qt mapping and verified non-activating Win32 show; retain the same HWND |
| Disk persistence ran in the click handler | Injected slow storage blocked an Overlay click for 606 ms | Apply feedback synchronously, then coalesce immutable settings snapshots on one writer; prevent stale saves overwriting newer settings |
| Opt-in history performed database lifecycle I/O on the GUI | Blocked connections delayed caption start/stop by 678/651 ms; existing database initialization also ran before UI construction | Defer schema initialization, session creation and finalization to the existing FIFO writer; resolve opaque session owners only after successful commit |
| Stopping captions and disabling the overlay were conflated | Stop hid/cleared the bar while the persisted control remained on | Keep display preference independent of capture; preserve the final drained caption unless the user's clear-on-stop policy applies |
| Late health events could reopen a closing application | OFF, STARTING and ERROR callbacks after Quit reopened the overlay in three failing tests | Ignore late lifecycle updates during shutdown |
| Model-install continuation did not fully communicate/clear state | Download start lacked overlay progress state; failure retained a pending start | Explicit Downloading/Error feedback, cancel/failure cleanup and actionable recovery |

The control represents Off, Starting, Downloading, Waiting, Visible, Idle, Preview and Error.
The selected accent supplies the active color (pink with the default Sakura theme). Auto-hide
reports Idle rather than claiming the control is off. Mapping failures return the preference,
button and bar to Off with a settings action. Expensive loading remains outside the GUI thread.

Native candidate verification exercised 47 clicks, slow settings/history I/O, blocked model loading,
initialization failure/retry, four pipeline cycles, external native hiding/recovery, foreground
retention and a stable HWND. Maximum synchronous click duration was 8.5 ms in the final pre-release
candidate. Native rendering/navigation passed at 125%, 187.5% and 250% effective DPI without paint
errors. The exact committed release and installed binary must repeat the frozen checks and actual
button cycles before publishing or closing the issue; results belong in the release notes.

## Additional release-review defects

- A silence-closed following chunk could reach reconciliation before the preceding forced-cut
  revision. A failing source-order test reproduced it. Defer following chunks until that revision
  finishes, including at EOF; preserve their exact samples and timing.
- A detected 140 ms pause was ignored when choosing a duration-limited cut. Remember short pause
  candidates separately from hard sentence-close thresholds. Use a retained candidate when the
  limit is reached, with overlap and all subsequent audio preserved. A breath alone does not
  finalize speech. This removed the Japanese regression found during release review.
- Word timestamps drift between overlapping decodes. Combine timestamp comparison with bounded
  multiword text alignment; reject repeated covered tails that add almost no audio. Regression
  controls preserve genuine later repetitions. Do not concatenate overlapping model text.
- Stop erased the last buffered words before their paint. Retain final captions through normal
  capture shutdown; the next session starts with a fresh display.
- Translation-only history retained a superseded fragment alongside its completed sentence.
  Queue event retraction in write order, update corrected source/timing by event ID, and update
  the FTS index. Excluded source text never enters the storage queue.
- Late translation corrections could cross an opt-in history policy change. Bound event ownership
  to its original session and ignore late storage updates belonging to a different session. FIFO
  lifecycle tests also verify failed creation, transaction rollback and recovery without mixing rows.
- Quit ignored queued final recognition results before closing history. Join producers, process their
  queued final results without reopening UI, then finalize and flush storage off the GUI. A real
  threaded Quit regression verifies final buffered speech and process shutdown cancellation.
- Remote ASR null/malformed/incomplete timestamps could crash recognition or replace valid text
  with a subset. Preserve the complete reply and fall back to text reconciliation as a unit.
- Unavailable ASR confidence was passed as zero into hallucination filtering. Actual recognition-
  lane tests reproduced valid remote greetings disappearing. Propagate an explicit unavailable
  value; require available weak evidence before blacklisting ordinary dialogue.
- A successful process launch was insufficient package proof. The verifier now requires the
  diagnostic completion marker and can require an exact clean embedded Git revision.
- The setup displayed 1.0.1 while its compiled fixed file/product versions remained 0.0.0.0.
  A native PE resource check reproduced the mismatch. Inno defaults those binary fields to zero;
  derive both from the release version and make every installer build verify the compiled installer
  and executable resources. Regression cases reject missing, old and inconsistent binary metadata.

## Objective accuracy results

Fourteen fixed fixtures cover conversation, rapid speech, boundary placement, names/numbers,
quiet speech, noise/tone background, antiphase stereo, competing voices, and human Korean,
Japanese and Chinese speech with a multilingual sequence. The model, beam, precision and fixture
bytes were held constant within each comparison; lanes ran in separate processes. The before
lane uses preserved pre-change implementation snapshots. Derived fixtures are correlated;
this is a regression corpus, not a population-wide quality estimate.

Production Balanced model: **small, CPU/int8, beam 1, four CPU threads**.

| Fixture | Old live | 1.0.1 live | Same-model whole file |
|---|---:|---:|---:|
| Conversation WER | 3.33% | 0% | 0% |
| Rapid WER | 3.03% | 0% | 0% |
| Boundary WER | 5.88% | 2.94% | 2.94% |
| Human English WER | 13.64% | 0% | 0% |
| Quiet / antiphase WER | 100% / 100% | 0% / 0% | 0% / 0% |
| Noise/tone WER | 36.67% | 0% | 0% |
| Competing-voice stress WER | 4.55% | 0% | 36.36% |
| Korean CER | 5.66% | 5.66% | 7.55% |
| Japanese CER | 4.44% | 4.44% | 4.44% |
| Chinese literal CER | 52.17% | 47.83% | 17.39% |
| Chinese script-equivalent CER | 39.13% | 17.39% | 17.39% |

No fixture regressed against the old production-model pipeline in either literal WER or CER.
Names/numbers remained at 32.26% literal WER, including numeral/spelling differences; this is not
a claim that rare names are solved. The multilingual sequence improved literal CER from 14.05%
to 13.22%, while whole-file inference reached 95.04% on this constructed sequence. It contains
separate languages, not a representative within-sentence code-switch benchmark.

Chinese's apparent live/offline CER gap was seven traditional/simplified character differences.
Both lanes made four remaining character errors after script mapping. Literal scores are retained;
the separate mapping check uses Windows NLS `LCMapStringEx(LCMAP_SIMPLIFIED_CHINESE)` and does not
modify captions, references or primary evaluation gates. The old pipeline omitted eight reference
characters; the new pipeline omitted none in this fixture. See
[Microsoft's API definition](https://learn.microsoft.com/en-us/windows/win32/api/winnls/nf-winnls-lcmapstringex).

The smaller base-model control also improved against its original pipeline on all fixture metrics.
Its competing-voice WER is 18.18%, compared with 36.36% originally and 13.64% in an earlier draft
of the repair. That trade-off remains visible: greedy recognition on a shorter pause-aligned window
can repeat words within the model's own output. The production small-model result did not exhibit
that regression. There is no diarization or source separation.

Threaded ASR/translation/display replays used public files without playback or physical capture.
Both English replays had 0% WER, zero dropped audio/ASR/translation work and no pipeline errors.
The diagnostic recording was compared, not merely inferred correct:

| File | Captured native frames | Processed 16 kHz frames | Exact ASR chunks |
|---|---:|---:|---:|
| Human English, 44.1 kHz | 485,100 | 176,000 | 4 / 4 |
| Rapid, 22.05 kHz | 143,719 | 104,286 | 3 / 3 |

Capture matched the original source bit-for-bit. Continuous preprocessing matched the reference;
every ASR input matched its recorded source slice bit-for-bit. Trace drops were zero.

## Verification and release gates

- Fresh environment synchronized from the lockfile with Python 3.13.16 and all production/GPU
  dependencies. The 1.0.1 lock change updates only the project's version, not dependency versions.
- Final full suite: **867 passed**, 295.17 seconds. Five failing reproductions cover the last
  absent-confidence fix; an additional test preserves explicit user phrase exclusions as a
  separate policy. No tests were disabled or newly skipped. Ruff,
  Python compilation and version consistency passed. No separate type checker is configured.
  The new real-Quit test initially left the process-wide cancellation flag set in pytest, causing
  five later package tests to fail. Its shutdown event is now isolated and cancellation asserted;
  the affected 74-test sequence and the complete suite were repeated successfully.
- Native real-loopback recognition/translation, language reconnects, model switching in both
  directions, persisted settings, overlay positioning, main-window hiding and non-activating
  topmost/click-through checks passed. Fresh download/cancel/resume/duplicate-request/activation
  checks passed. These physical-audio checks finished before the owner requested silent testing;
  all subsequent recognition checks used files or simulated capture without speaker playback.
- Mandatory frozen CUDA Whisper, CPU Parakeet and local translation passed. One preliminary test
  profile lacked its translation fixture and correctly failed; the profile was supplied the public
  fixture and the mandatory check was repeated successfully. No gate was weakened.
- Source/history privacy and Gitleaks scans found zero findings. The candidate artifact scan checked
  436 files with zero findings. OSV found no advisories among the 61 locked dependency packages.
- Native Qt/PySide source archives were verified against pinned hashes. Compiler checksum/publisher,
  installer version, bundle contents, checksums and installed revision remain final publication gates.
- GitHub Actions did not execute the repository CI/release jobs because of account-level runner
  billing/quota restrictions. This is recorded as unavailable, not passed. Windows validation runs
  locally; unverified macOS artifacts must not be distributed with this Windows patch.

## Latency and remaining limits

Forced windows can display provisional source text, then correct the same caption ID after 0.6 s
of real lookahead and inference. Translation waits for final source text and permits at most 450 ms
of clause accumulation; late continuation can replace a fragment translation. Context is bounded
and resets after discontinuity, language change or substantial gaps.

Under concurrent CPU validation load, latest-word subtitle latency was approximately 2.08 s for
the English replay and 5.38 s for rapid speech, including 3.81 s of queue wait in the latter. These
are illustrative CPU observations, not a GPU latency promise. Bounded overload remains observable
speech loss, with an actionable faster-quality warning. Reading pages add display dwell separately.

Real USB/Bluetooth/HDMI reconnects, multiple physical monitors, exclusive fullscreen, hours-long
hardware sessions, native-speaker translation ratings and a larger independent multilingual corpus
remain follow-up coverage. Automatic Chinese script choice is not a user preference yet. Quiet VAD
fallback, severe stereo cancellation, simultaneous speakers and scene changes without pauses have
documented limits. Debug recording is opt-in, bounded and local; normal logs contain no transcripts
or audio. See [the complete accuracy investigation](accuracy-investigation.md) for the original
signal-integrity faults, model invocation review, diagnostic stages and reproduction commands.
