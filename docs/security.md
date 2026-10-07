# Privacy and security

## Processing and consent

Local speech recognition and built-in translation are the defaults. Captured audio exists in
bounded memory buffers, not live recording files. After required model downloads, these paths
work offline. There is no telemetry, account or automatic crash-report upload.

Optional **remote speech** uploads short PCM WAV buffers made in memory. It is enabled only when
the user chooses a compatible server and allows audio to that exact normalized address. Editing
an address clears consent. The Live page identifies where audio is processed. The remote server
controls its own retention and may charge for requests.

Optional **text providers** receive captions and a bounded amount of recent context for live
translation. Transcript assistance sends selected transcript text after its existing consent flow.
These choices are separate from remote speech. Local-network HTTP is supported but unencrypted;
public endpoints require HTTPS. Address badges describe configured host scope, not the trustworthiness
of the operator, DNS resolution or what the server forwards elsewhere.

## Local data

New installs do not save transcripts until the user opts in. A fresh live-only install does not
create a transcript database. Original text, translated text and session metadata have separate
storage choices; excluded text is removed before entering the disk writer. Turning off both text
choices saves only session dates and languages. Failed translations are not saved.
Session-only retention keeps new captions in memory, and **Clear Live when stopping** also removes
the in-app transcript after Stop. Existing saved history is retained until explicitly deleted or
expired by its policy. Audio is never recorded to disk by the live pipeline.

Enabled history uses local SQLite/FTS5. Retention runs at startup, when its settings change and
hourly while the app remains open, protecting the active session. Storage failures produce a
separate persistent warning while live captions continue. Settings and history
are not encrypted by SubbyAI; other programs with access to the user account may read them.
The installer keeps user data on uninstall so reinstalling can restore it.

API keys are stored by the OS credential backend. They are not included in settings JSON or copied
diagnostics. Logs redact common credential formats, authorization values and home paths, including
tracebacks. Caption text is not deliberately logged. Redaction is a mitigation, not proof that every
possible third-party error string is harmless: review a log before sharing it.

Deletion uses SQLite secure-delete and flushes queued writes before deleting. If queued writes
fail, deletion reports the failure so it can be retried rather than claiming success. Filesystem snapshots,
backups and storage-device behavior can retain copies. Deleting app data cannot erase copies already
sent to a server or exported by the user.

## Downloads and parsing boundaries

Speech model repositories and revisions come from a curated catalog. The app validates repository
paths, checks containment of cache paths, disables implicit Hugging Face authentication and verifies
SHA-256 when the downloaded blob filename supplies its hash. Cached models load without network.
This does not create an independent signature for all copied files or all upstream model content.

Language packs use a catalog pinned to a Git commit, fetched from GitHub, and HTTPS downloads
restricted to the official `argos-net.com/v1/` host. Response size and elapsed time are bounded.
A supplied SHA-256 is checked; many upstream entries don't supply one, so TLS and the pinned catalog
remain the trust boundary. Model files are untrusted inputs to native runtimes, not executable plugins.

Before language-pack extraction, every member is checked for traversal, absolute paths, Windows
paths, symlinks, unreasonable size and count. A complete pack is staged and atomically replaces its
installed pack with rollback on failure. Storage targets reject symbolic links/junctions.
Downloads use temporary files. Normal shutdown cancels and joins installers off the GUI thread; a forcibly killed process may still leave a partial file in the private cache.

Remote speech and text clients verify TLS, ignore environment proxies, refuse redirects, bound
response bodies, validate response shapes and close session clients. Public HTTP is rejected.
Remote speech retries only bounded 429/5xx responses; ambiguous transport failures are not replayed
because the server may already have billed or processed the upload. Per-operation timeouts and read
deadlines limit blocking, but Python cannot forcibly interrupt a hung native inference call.

Settings are bounded and migrated defensively. Captions are rendered as plain text; provider output
cannot become HTML that fetches a remote image. Hallucination phrases are escaped as literals.
Exports constrain paths. GPU discovery uses a subprocess to isolate driver failures. Installation
is per-user with no elevated privileges and no code injection into other applications.

## Release security

Runtime constraints, SHA-pinned CI actions, an OSV dependency check, collected license notices,
real-model frozen checks and source/history/binary privacy scans are release gates. An OSV result
is a dated check of known advisories, not a guarantee about undiscovered bugs. Release checksums
help verify downloads; unsigned V1 builds do not provide publisher authentication.

See [repository hygiene](repository-hygiene.md) for source/history/artifact privacy gates.

Report vulnerabilities via the repository's private security-reporting channel when available.
For public issues, omit credentials, private transcripts, recordings and identifying paths.
