"""Ordered translation providers with a failure policy.

Two things this exists to guarantee:

1. A dead provider never stalls captions. Each failure is counted; after three
   in a row the provider is skipped for a minute instead of being retried on
   every single line.
2. Recent lines travel with the request. The LLM lane is only worth its latency
   if it can see what was said a moment ago, and the chain is the only component
   that sees a whole session's worth of lines.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .base import TranslationError, TranslationProvider, TranslationResult

log = logging.getLogger(__name__)


@dataclass
class _Breaker:
    consecutive_failures: int = 0
    open_until: float = 0.0

    def record_failure(self, now: float, threshold: int, cooldown: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= threshold:
            self.open_until = now + cooldown

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.open_until = 0.0

    def is_open(self, now: float) -> bool:
        return now < self.open_until


class TranslationChain:
    """Tries providers in order and remembers what was said before."""

    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 60.0
    CONTEXT_PAIRS = 4

    def __init__(
        self,
        providers: Iterable[TranslationProvider],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.providers: list[TranslationProvider] = list(providers)
        self.clock = clock
        self.last_error: str | None = None
        self.last_error_provider: str | None = None
        self._breakers: dict[str, _Breaker] = {}
        self._context: list[tuple[str, str]] = []

    # ---------- state ----------

    @property
    def context_pairs(self) -> list[tuple[str, str]]:
        """Recent (source, target) pairs handed to context-aware providers."""
        return list(self._context)

    def reset(self) -> None:
        """Forget context and reopen every provider. Use when a session ends."""
        self._context.clear()
        self._breakers.clear()
        self.last_error = None
        self.last_error_provider = None

    def close(self) -> None:
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                log.exception("Closing provider %s failed", provider.id)

    def is_paused(self) -> bool:
        """True when nothing in the chain can currently take a line."""
        now = self.clock()
        return not any(
            provider.is_available and not self._breaker(provider).is_open(now)
            for provider in self.providers
        )

    # ---------- work ----------

    def prepare(self, source_lang: str, target_lang: str) -> None:
        """Warm every provider that can serve the pair, skipping those that
        can't. One missing language pack must not block the other lanes."""
        for provider in self.providers:
            try:
                provider.prepare(source_lang, target_lang)
            except TranslationError as exc:
                log.info("Provider %s is not ready: %s", provider.id, exc)
                self._note_error(provider, str(exc))
            except Exception:
                log.exception("Preparing provider %s failed", provider.id)
                self._note_error(provider, f"{provider.label} couldn't be started.")

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[tuple[str, str]] | None = None,
    ) -> TranslationResult:
        """The first provider that succeeds wins.

        Raises:
            TranslationError: only when every provider failed or was skipped.
        """
        pairs = list(context if context is not None else self._context)[-self.CONTEXT_PAIRS :]
        now = self.clock()
        skipped: list[str] = []
        failure: str | None = None
        paused = False

        for provider in self.providers:
            breaker = self._breaker(provider)
            if breaker.is_open(now):
                paused = True
                skipped.append(f"{provider.label} is paused after repeated failures.")
                continue
            if not provider.is_available and provider.id != "builtin":
                # e.g. a cloud provider with no key: three 20s round trips to
                # learn what one attribute already knew.
                skipped.append(f"{provider.label} isn't set up yet.")
                continue
            if not provider.supports(source_lang, target_lang) and provider.id != "builtin":
                skipped.append(f"{provider.label} doesn't handle this language pair.")
                continue
            try:
                result = provider.translate(text, source_lang, target_lang, pairs)
            except TranslationError as exc:
                failure = str(exc)
                self._fail(provider, breaker, failure)
                continue
            except Exception:
                # A provider bug is still just a failed line; captions go on.
                log.exception("Provider %s raised an unexpected error", provider.id)
                failure = f"{provider.label} hit an unexpected error."
                self._fail(provider, breaker, failure)
                continue
            breaker.record_success()
            self._remember(text, result.text)
            self.last_error = None
            self.last_error_provider = None
            return result

        detail = failure or (skipped[0] if skipped else "No translator is set up.")
        # While a breaker is open, last_error still holds the reason it opened —
        # more useful to the user than "it is paused".
        if failure is None and not paused:
            self.last_error = detail
            self.last_error_provider = None
        raise TranslationError(detail)

    # ---------- internals ----------

    def _breaker(self, provider: TranslationProvider) -> _Breaker:
        return self._breakers.setdefault(provider.id, _Breaker())

    def _fail(self, provider: TranslationProvider, breaker: _Breaker, message: str) -> None:
        breaker.record_failure(self.clock(), self.FAILURE_THRESHOLD, self.COOLDOWN_SECONDS)
        self._note_error(provider, message)
        log.warning(
            "Translation via %s failed (%d in a row): %s",
            provider.id,
            breaker.consecutive_failures,
            message,
        )

    def _note_error(self, provider: TranslationProvider, message: str) -> None:
        self.last_error = message
        self.last_error_provider = provider.label

    def _remember(self, source: str, translated: str) -> None:
        self._context.append((source, translated))
        del self._context[: -self.CONTEXT_PAIRS]
