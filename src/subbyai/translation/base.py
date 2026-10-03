"""The contract every translator implements, and the privacy rule they obey.

The tier is *computed from the endpoint*, never taken from the provider's word
for it. A provider labelled "Local AI" whose address quietly points at a hosted
endpoint would otherwise let a user's speech leave the machine while the UI
promised it hadn't. ``tier_for_url`` is the one place that judgement is made.
"""

from __future__ import annotations

import ipaddress
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..core.events import PrivacyTier


class TranslationError(Exception):
    """A translation failure, phrased for a person rather than a log file."""


@dataclass(frozen=True, slots=True)
class TranslationResult:
    text: str
    provider_id: str
    provider_label: str
    tier: PrivacyTier
    """Where the text actually went. Carried into the caption for disclosure."""


class TranslationProvider(ABC):
    """One way to translate. Providers are used from a worker thread."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Stable identifier, matching ``ProviderSettings.id``."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Human name, shown in the UI and in failure messages."""

    @property
    @abstractmethod
    def tier(self) -> PrivacyTier: ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Usable right now, checked without touching the network."""

    def prepare(self, source_lang: str, target_lang: str) -> None:  # noqa: B027
        """Get ready for a pair — may download or load models, so it can block.

        Optional: a provider with nothing to warm up inherits the no-op.

        Raises:
            TranslationError: if the pair can never be served by this provider.
        """

    @abstractmethod
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[tuple[str, str]] | None = None,
    ) -> TranslationResult:
        """Translate one line. ``context`` holds recent (source, target) pairs.

        Raises:
            TranslationError: on any failure, including transport errors.
        """

    def supports(self, source_lang: str, target_lang: str) -> bool:
        """Whether this provider can serve the pair without further downloads."""
        return bool(target_lang)

    def close(self) -> None:  # noqa: B027 - optional; stateless providers need nothing
        """Release sockets and models. Safe to call more than once."""


# Names that resolve inside the machine or the building, not on the internet.
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet")


def tier_for_url(url: str) -> PrivacyTier:
    """Classify where an endpoint lives. Unknown shapes are treated as CLOUD."""
    host = _hostname(url)
    if not host:
        return PrivacyTier.CLOUD

    # RFC 6761 reserves localhost (and anything under it) for the loopback.
    if host == "localhost" or host.endswith(".localhost"):
        return PrivacyTier.ON_DEVICE

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if address.is_loopback:
            return PrivacyTier.ON_DEVICE
        if address.is_private or address.is_link_local:
            return PrivacyTier.LOCAL_NETWORK
        return PrivacyTier.CLOUD

    if host.endswith(_LOCAL_SUFFIXES):
        return PrivacyTier.LOCAL_NETWORK
    if "." not in host:
        # A dotless name only resolves through a local suffix search — an
        # intranet host such as "workstation" or "nas".
        return PrivacyTier.LOCAL_NETWORK
    return PrivacyTier.CLOUD


_HOSTNAME_RE = re.compile(r"^[a-z0-9:._-]+$")


def _hostname(url: str) -> str:
    """The host part, or "" if this isn't something we can judge."""
    text = url.strip()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text  # tolerate "localhost:11434" and "example.com/v1"
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    host = host.rstrip(".").lower()
    # Anything that isn't a plausible hostname (or IPv6 literal) is unjudgeable,
    # and unjudgeable means cloud.
    return host if _HOSTNAME_RE.match(host) else ""


_LANGUAGE_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


def language_name(code: str) -> str:
    """English name for an ISO code, falling back to the code itself."""
    if not code:
        return "the source language"
    return _LANGUAGE_NAMES.get(normalize_code(code), code)


def normalize_code(code: str) -> str:
    """"en-GB", "zh_CN" -> "en", "zh". Providers key models on the base code."""
    return code.strip().lower().replace("_", "-").split("-")[0]
