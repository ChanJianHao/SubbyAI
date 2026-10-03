"""Translation providers and the chain that orders them.

Nothing here imports Qt: the chain runs on a worker thread and must stay usable
from the CLI and the tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..core.events import PrivacyTier
from ..core.network import validate_endpoint
from ..core.secrets import endpoint_key_id
from ..core.settings import IntelligenceSettings, ProviderSettings
from .base import (
    TranslationError,
    TranslationProvider,
    TranslationResult,
    language_name,
    normalize_code,
    tier_for_url,
)
from .builtin import BUILTIN_PROVIDER_ID, ArgosProvider
from .chain import TranslationChain
from .llm import DEFAULT_BASE_URLS, OpenAiCompatibleProvider, clean_response

__all__ = [
    "BUILTIN_PROVIDER_ID",
    "DEFAULT_BASE_URLS",
    "ArgosProvider",
    "OpenAiCompatibleProvider",
    "TranslationChain",
    "TranslationError",
    "TranslationProvider",
    "TranslationResult",
    "build_chain",
    "clean_response",
    "language_name",
    "normalize_code",
    "provider_from_settings",
    "tier_for_url",
]


def provider_from_settings(
    config: ProviderSettings, api_key: str | None = None
) -> TranslationProvider:
    """Build one provider from its stored configuration."""
    if config.kind == "builtin" or config.id == BUILTIN_PROVIDER_ID:
        return ArgosProvider(label=config.label or "Built-in translator")
    return OpenAiCompatibleProvider(
        id=config.id,
        label=config.label or config.id,
        base_url=config.base_url or DEFAULT_BASE_URLS.get(config.kind, ""),
        model=config.model,
        api_key=api_key,
        kind=config.kind,
    )


def build_chain(
    intelligence: IntelligenceSettings,
    api_key_for: Callable[[str], str | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TranslationChain:
    """Assemble the chain in the user's configured order.

    API keys are fetched through ``api_key_for`` so the OS keychain stays out of
    this layer. A cloud endpoint is left out entirely until consent was given —
    computed tier, not the provider's ``kind``, decides what counts as cloud.
    """
    by_id = {p.id: p for p in intelligence.providers}
    providers: list[TranslationProvider] = []
    for provider_id in intelligence.translation_order:
        if provider_id == BUILTIN_PROVIDER_ID and provider_id not in by_id:
            providers.append(ArgosProvider())
            continue
        config = by_id.get(provider_id)
        if config is None or not config.enabled:
            continue
        if config.kind == "builtin":
            providers.append(ArgosProvider())
            continue
        try:
            endpoint = validate_endpoint(config.base_url or DEFAULT_BASE_URLS.get(config.kind, ""))
        except ValueError:
            continue
        if tier_for_url(endpoint) is PrivacyTier.CLOUD and config.consent_url != endpoint:
            continue
        provider = provider_from_settings(
            config,
            api_key_for(endpoint_key_id(config.id, endpoint)) if api_key_for is not None else None,
        )
        providers.append(provider)
    return TranslationChain(providers, clock=clock)
