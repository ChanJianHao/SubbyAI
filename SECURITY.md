# Security policy

## Supported releases

Security fixes target the latest V1 release. Use the most recent patch version.

## Report privately

Use [private vulnerability reporting](https://github.com/ChanJianHao/SubbyAI/security/advisories/new).
Include the app version, affected platform, reproducible steps and expected impact.
Do not include credentials, recordings, private transcripts or identifying logs.
If private reporting is unavailable while the repository is private, contact its maintainer
through the repository's access channel rather than opening a public issue.

## Security boundaries

Local recognition and translation are the defaults; model files are native-runtime inputs.
Optional speech servers receive audio only after endpoint-specific consent. Optional translation
servers receive captions when enabled, and cloud consent is bound to their address.
Transcript assistance sends text only when requested and confirmed for a cloud destination.

Keys use Windows Credential Manager or macOS Keychain. Plaintext keyring backends are refused.
Network clients verify TLS, refuse redirects and ignore environment proxies. Public HTTP is
refused; private-network HTTP is permitted with an unencrypted-data indication.

Settings, archives, response bodies and queues are bounded. Saved transcripts are opt-in and
remain local unless exported or sent to a chosen assistant. There is no telemetry or automatic
crash upload. The app installs per-user and does not inject code into other applications.

See [privacy and security](docs/security.md) for retention, deletion and trust limitations.
Caption accuracy and an attacker already controlling the user account are outside this policy.
