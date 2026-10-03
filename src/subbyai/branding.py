"""Every brand-bound identifier lives here.

Renaming the product means editing this file plus the installer metadata —
not spelunking through paths, bundle ids, and window titles.
"""

from __future__ import annotations

VERSION = "1.0.0"

#: The mark. Short, used wherever the app refers to itself in running text.
APP_NAME = "SubbyAI"

#: What it is, in the words people actually search for. Paired with the mark on
#: every outward-facing surface — window title, tray tooltip, About, README,
#: website — so the product is discoverable by what it does, not only by its
#: name. "SubbyAI" alone is ambiguous out of context; the descriptor fixes that.
APP_DESCRIPTOR = "Live subtitles"
APP_FULL_NAME = f"{APP_NAME} — {APP_DESCRIPTOR}"

#: The tagline describes the output and the audio source.
APP_TAGLINE = "Live AI captions for anything your computer plays"
APP_DESCRIPTION = (
    "SubbyAI turns the audio your computer plays into live captions and "
    "translations, on your own machine."
)

# Filesystem / OS identifiers. Kept lowercase and ASCII.
APP_ID = "subbyai"
ORG_NAME = "SubbyAI"
BUNDLE_ID = "io.github.chanjianhao.subbyai"
SINGLE_INSTANCE_KEY = "subbyai-single-instance"

SUPPORT_URL = "https://github.com/ChanJianHao/SubbyAI/issues"
HOMEPAGE_URL = "https://github.com/ChanJianHao/SubbyAI"
