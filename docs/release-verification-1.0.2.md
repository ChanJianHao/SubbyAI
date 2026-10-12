# Windows release verification: 1.0.2

The 1.0.1 source candidate passed 868 local Windows tests, frozen model and
native-window checks, clean installation and upgrade byte comparisons, and
six ordinary installed Overlay ON/OFF cycles. Publication was held when newly
available GitHub runners exposed failures. No 1.0.1 binary release was published
and the reported Overlay issue was left open.

## Hosted failures and their causes

- A history test waited for a worker's failed-session flag. That flag is set
  before the worker emits the queued UI warning. Processing the event queue
  once cannot guarantee the worker has emitted it. The test now waits for both
  the failed session and its visible warning, with a bounded deadline. It still
  verifies two failed retries and that flush reports the original write loss.
- The language reconnect test slept 130 ms for a 100 ms Qt timer, which may
  fire later under runner load. It now waits for each new session start before
  checking the actual language configuration and asserts both restart requests.
- An interrupted expansion test assumed its animation finished after 280 ms.
  It now waits for animation completion and actual hidden state, retaining the
  checks for unclipped content, reversal and reduced-motion settlement.
- The macOS system-audio test expected microphone permission guidance. System
  audio requires a loopback input; the implementation already describes that
  requirement correctly. Recovery tests now exercise Windows, macOS and Linux
  system audio explicitly, including the device picker, and separately verify
  microphone permission guidance on Windows and macOS.
- The compiled-version test used a fixed 1.0.1 tuple for its passing case. It now
  derives that case from the release version while retaining rejection of
  zero, mismatched and old file/product resources.

These changes correct the test synchronization and source-specific
expectations; they do not suppress failures, skip coverage, extend production
delays or change the Overlay/ASR implementation. The only production-code change
since the installed 1.0.1 candidate is its version constant. Dependencies remain
unchanged. Version 1.0.2 avoids moving the existing 1.0.1 tag.

## Release gates

Repeat the complete local suite and hosted checks, rebuild from a clean committed
revision, verify frozen native lifecycle and prerecorded model inference,
compiled Windows version metadata, distribution privacy, asset integrity,
clean installation and the actual installed binary. Publish only the tested
Windows assets after their GitHub digests match. Final results and the exact
commit are recorded in the release notes after these gates pass.

The [1.0.1 investigation](release-verification-1.0.1.md) contains the Overlay
root causes, comprehensive audio evidence, before/after accuracy metrics and
remaining limitations. Its hosted-runner unavailability describes the earlier
verification period; runners subsequently became available and exposed the
failures above. The [accuracy investigation](accuracy-investigation.md) remains
applicable. Windows 11 25H2 is the owner-approved test environment; there is no
24H2-specific claim. Verification uses prerecorded audio and fake capture,
without playback through the owner's speakers or physical recording.
