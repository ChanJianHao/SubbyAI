# Contributing to SubbyAI

Thanks for looking. Bug reports are as useful as code, and the most useful of all are the ones that
say exactly what you played, what you expected, and what appeared instead.

## Getting set up

```bash
git clone https://github.com/ChanJianHao/SubbyAI.git
cd SubbyAI
python -m pip install --require-hashes --only-binary=:all: -r packaging/bootstrap.txt
uv sync --locked --extra dev
uv run --no-sync python -m subbyai
```

Python 3.13 is the tested release baseline. No compiler, Node, or system audio libraries — everything ships as wheels.

## Before you open a pull request

```bash
uv run --no-sync ruff check src tests scripts
uv run --no-sync pytest
```

Both must pass; CI runs them on Windows and macOS. Tests are offline and hardware-free — audio,
models and credentials are isolated. Protocol tests open only local loopback sockets. Every data
path is redirected to a temporary directory.

For changes to the pipeline or packaging, also run what the tests cannot:

```bash
uv run --no-sync python scripts/e2e_smoke.py --translate fr   # real audio, real model, real translation
uv run --no-sync python scripts/verify_package.py --require-models
```

## House rules

These contracts protect the user experience. Breaking one can cause data loss or unreliable captions,
so each has a test guarding it.

- **No layer imports Qt except `ui/`, `pipeline/` and `system/hotkeys.py`.** Core, audio, asr,
  translation and storage stay framework-free and therefore testable.
- **The overlay is the product; every other window is a guest.** Nothing may call `raise_()`,
  `activateWindow()` or `setFocus()` on itself except in direct response to a user action. A dialog
  over someone's game or call is a bug.
- **No pipeline error becomes a modal dialog.** Errors are inline banners with the action that fixes
  them, or a tray notification when the window is closed.
- **Captions never wait for translation.** `test_original_caption_is_emitted_without_translation`
  must keep passing.
- **Controls must be able to shrink.** No label that reads as a sentence without `wordWrap`, no combo
  box sized to its longest item, no fixed row that cannot stack. `tests/test_layout.py` enforces
  this.
- **No jargon in anything a user reads.** Not WASAPI, VAD, CUDA, inference, endpoint, ISO codes, or
  model names outside Advanced Mode. Say "your computer's audio", "quality", "graphics card",
  and language names. Sentence case throughout.
- **Colours come from `ui/tokens.py`.** Both themes are designed; a change to one needs a matching
  change to the other.
- **Privacy tiers are computed from the endpoint**, never taken from a provider's own label.

## Where things live

```
src/subbyai/
  core/         events, settings, health, logging and credential storage — no Qt
  audio/        capture backends, ring buffer, segmenter
  asr/          capability probe, model manager, engine + warm cache
  translation/  provider interface, built-in translator, LLM endpoints, chain
  pipeline/     the four-stage captioner
  storage/      SQLite + FTS5 session store
  system/       hotkeys, window effects, foreground app, single instance
  ui/           tokens, theme, shell, live, overlay, onboarding, history, settings
  app.py        the only module that knows every layer
```

New behaviour needs a test. A bug fix gets a regression test named after the failure, not the fix.

## Reporting a bug

Use the issue templates. For anything caption-related, the log at
`%APPDATA%\SubbyAI\Logs\subbyai.log` (Windows) or `~/Library/Logs/subbyai/subbyai.log` (macOS) is worth
more than a paragraph of description.
Review and redact logs before sharing; do not attach recordings or private transcripts.

Security issues: see [SECURITY.md](SECURITY.md) — please do not open a public issue.
