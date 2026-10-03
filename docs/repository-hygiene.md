# Repository hygiene

Only application source, useful tests, current documentation, packaging inputs and intentional
brand assets belong in Git. User settings, credentials, recordings, caches, build output, logs,
personal tooling state and local validation reports are ignored.

Before a release push:

1. Review included files and filenames, including examples, fixtures and image metadata.
2. Run `audit_privacy.py --history` and Gitleaks over the tree and all reachable commits.
3. Inspect binaries, decompressed Python filenames, licenses and dependency metadata.
4. Verify checksums and confirm the destination remains private during preparation.

Use reserved example domains and synthetic fixtures. Never paste a live token to test redaction.
Required third-party copyright/author notices and intentional repository account URLs are retained;
they are not personal application data.

Reports identify rules and file locations without printing matched secrets. Automated scanning
supplements file review; it cannot establish that arbitrary content is harmless. If a real
credential enters Git, revoke it and remove it from affected refs before pushing.
