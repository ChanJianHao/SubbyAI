"""Translation through an OpenAI-compatible chat endpoint.

Why this lane exists at all: OPUS-MT translates one sentence in a vacuum. An LLM
can be handed the last few lines, so pronouns, names, and a running topic stay
consistent across a conversation. That context window is the entire argument for
paying the extra latency, so it is not optional here.

Ollama (11434) and LM Studio (1234) both speak /v1/chat/completions, which means
one implementation covers on-device, local-network, and cloud endpoints. The
privacy tier is derived from the address, never from the ``kind``.
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from ..core.events import PrivacyTier
from .base import (
    TranslationError,
    TranslationProvider,
    TranslationResult,
    language_name,
    tier_for_url,
)

log = logging.getLogger(__name__)

KIND_OLLAMA = "ollama"
KIND_LMSTUDIO = "lmstudio"
KIND_OPENAI = "openai"

DEFAULT_BASE_URLS: dict[str, str] = {
    KIND_OLLAMA: "http://localhost:11434",
    KIND_LMSTUDIO: "http://localhost:1234",
    KIND_OPENAI: "https://api.openai.com",
}


class OpenAiCompatibleProvider(TranslationProvider):
    """Any endpoint exposing /v1/chat/completions."""

    CONNECT_TIMEOUT_LOCAL = 2.0
    CONNECT_TIMEOUT_REMOTE = 5.0
    FIRST_TOKEN_TIMEOUT = 10.0
    TOTAL_TIMEOUT = 20.0
    MAX_CONTEXT_PAIRS = 4

    def __init__(
        self,
        id: str,  # shadows the builtin deliberately: it mirrors ProviderSettings.id
        label: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        kind: str = KIND_OPENAI,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._id = id
        self._label = label or id
        self._root = _api_root(base_url)
        self._model = model
        self._api_key = api_key or None
        self._kind = kind
        self._tier = tier_for_url(self._root)
        self._transport = transport
        self._client: httpx.Client | None = None

    # ---------- identity ----------

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    @property
    def tier(self) -> PrivacyTier:
        return self._tier

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._root

    @property
    def is_available(self) -> bool:
        """Configured enough to try. Probing would cost a round trip per caption
        and the chain's circuit breaker already handles a dead endpoint."""
        if not self._root or not self._model:
            return False
        return not (self._tier is PrivacyTier.CLOUD and not self._api_key)

    def supports(self, source_lang: str, target_lang: str) -> bool:
        # An LLM needs no per-pair model, and can work without a detected source.
        return bool(target_lang) and target_lang != source_lang

    # ---------- translation ----------

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[tuple[str, str]] | None = None,
    ) -> TranslationResult:
        if not self._root:
            raise TranslationError(f"{self._label} has no server address set.")
        if not self._model:
            raise TranslationError(f"{self._label} has no model chosen.")

        payload = {
            "model": self._model,
            "messages": self._messages(text, source_lang, target_lang, context),
            "temperature": 0.2,
            "stream": False,
        }
        data = self._request_json("POST", f"{self._root}/v1/chat/completions", payload)
        return TranslationResult(
            text=clean_response(_content_of(data, self._label)),
            provider_id=self._id,
            provider_label=self._label,
            tier=self._tier,
        )

    def _messages(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[tuple[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self._system_prompt(source_lang, target_lang)}]
        for source, translated in (context or [])[-self.MAX_CONTEXT_PAIRS :]:
            # Replayed as real turns: models follow their own prior style far
            # more reliably than a list of examples pasted into the system text.
            messages.append({"role": "user", "content": source})
            messages.append({"role": "assistant", "content": translated})
        messages.append({"role": "user", "content": text})
        return messages

    def _system_prompt(self, source_lang: str, target_lang: str) -> str:
        source = language_name(source_lang)
        target = language_name(target_lang)
        return (
            f"You are translating live subtitles from {source} into {target}.\n"
            f"- Output ONLY the {target} translation. No notes, no quotes, no explanation.\n"
            "- Preserve names, places, and honorifics exactly as spoken.\n"
            "- Keep the speaker's register and keep it short enough to read as a subtitle.\n"
            "- Earlier turns are context from the same conversation; "
            "translate only the final line.\n"
            f"- If the line is already {target}, repeat it unchanged."
        )

    # ---------- settings UI ----------

    def list_models(self) -> list[str]:
        """Model ids the server offers. Network; for the settings screen."""
        if not self._root:
            raise TranslationError(f"{self._label} has no server address set.")
        names: set[str] = set()
        error: TranslationError | None = None
        try:
            data = self._request_json("GET", f"{self._root}/v1/models")
        except TranslationError as exc:
            error = exc
        else:
            for item in data.get("data") or []:
                if isinstance(item, dict) and item.get("id"):
                    names.add(str(item["id"]))
        if self._kind == KIND_OLLAMA:
            # Older Ollama builds predate the /v1 shim but always had /api/tags.
            try:
                tags = self._request_json("GET", f"{self._root}/api/tags")
            except TranslationError as exc:
                error = error or exc
            else:
                for item in tags.get("models") or []:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("model")
                        if name:
                            names.add(str(name))
        if not names and error is not None:
            raise error
        return sorted(names)

    def test_connection(self) -> tuple[bool, str]:
        """(ok, message) for the settings screen. Never raises."""
        if not self._root:
            return False, "Add the server address first."
        try:
            models = self.list_models()
        except TranslationError as exc:
            return False, str(exc)
        if not models:
            return False, "Connected, but the server has no models loaded."
        if not self._model:
            return False, f"Connected. Now choose one of the {len(models)} models."
        if models and self._model not in models:
            preview = ", ".join(models[:4])
            return False, f"Connected, but '{self._model}' isn't there. Available: {preview}."
        return True, f"Connected. Translating with {self._model}, {self._tier.label.lower()}."

    # ---------- transport ----------

    def _client_for_use(self) -> httpx.Client:
        if self._client is None:
            from ..core.network import validate_endpoint

            try:
                self._root = validate_endpoint(self._root)
            except ValueError as exc:
                raise TranslationError(str(exc)) from exc
            connect = (
                self.CONNECT_TIMEOUT_LOCAL
                if self._tier is PrivacyTier.ON_DEVICE
                else self.CONNECT_TIMEOUT_REMOTE
            )
            timeout = httpx.Timeout(
                connect=connect,
                read=self.FIRST_TOKEN_TIMEOUT,
                write=self.FIRST_TOKEN_TIMEOUT,
                pool=connect,
            )
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.Client(
                timeout=timeout,
                headers=headers,
                transport=self._transport,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    def _request_json(self, method: str, url: str, payload: dict | None = None) -> dict:
        client = self._client_for_use()
        deadline = time.monotonic() + self.TOTAL_TIMEOUT
        try:
            request = client.build_request(method, url, json=payload)
            response = client.send(request, stream=True)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise TranslationError(self._unreachable_message()) from exc
        except httpx.TimeoutException as exc:
            raise TranslationError(f"{self._label} took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise TranslationError(self._unreachable_message()) from exc

        try:
            body = self._read_body(response, deadline)
        finally:
            response.close()

        if response.status_code >= 300:
            raise TranslationError(self._status_message(response.status_code))
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise TranslationError(f"{self._label} sent a reply we couldn't read.") from exc
        if not isinstance(data, dict):
            raise TranslationError(f"{self._label} sent a reply we couldn't read.")
        return data

    def _read_body(self, response: httpx.Response, deadline: float) -> str:
        """Read to the end, enforcing the total budget the read timeout can't.

        httpx's read timeout is per socket read, so a server dribbling one byte
        at a time would never trip it. Captions cannot wait that long.
        """
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 1_048_576:
                    raise TranslationError(f"{self._label} sent a reply that is too large.")
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise TranslationError(f"{self._label} took too long to answer.")
        except httpx.TimeoutException as exc:
            raise TranslationError(f"{self._label} took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise TranslationError(f"{self._label} dropped the connection.") from exc
        return b"".join(chunks).decode("utf-8", "replace")

    def _unreachable_message(self) -> str:
        return (
            f"Couldn't reach {self._label} at {self._root}. "
            "Check that it's running and the address is right."
        )

    def _status_message(self, status: int) -> str:
        if status in (401, 403):
            return f"{self._label} rejected the API key."
        if status == 404:
            return (
                f"{self._label} has no model called '{self._model}' (or the address is incomplete)."
            )
        if status == 429:
            return f"{self._label} is rate-limiting us. Try again in a moment."
        if status >= 500:
            return f"{self._label} had a server error ({status})."
        return f"{self._label} refused the request ({status})."

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _api_root(base_url: str) -> str:
    """Accept both "http://host:1234" and "http://host:1234/v1"."""
    root = (base_url or "").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _content_of(data: dict, label: str) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslationError(f"{label} replied in a shape we didn't expect.") from exc
    if not isinstance(content, str) or not content.strip():
        raise TranslationError(f"{label} returned an empty translation.")
    return content


# Only strips a preamble that names itself ("Translation:", "Here's the
# translation -"). A bare "Sure!" is left alone: it may well be the line.
_PREAMBLE_RE = re.compile(
    r"""^\s*
    (?:sure|certainly|of\ course|okay|ok|alright)?[,.!]?\s*
    (?:
        here(?:'s|’s|\ is)\s+(?:the\s+)?(?:translation|translated\ text)
      | (?:the\s+)?translation(?:\s+is)?
      | translated(?:\s+text)?
      | output
    )
    \s*[:–—-]\s*
    """,  # noqa: RUF001 - curly and dashed variants are the point
    re.IGNORECASE | re.VERBOSE,
)

# Opening character -> its closing partner. Models wrap translations in whatever
# quote style the target language uses, so all of them have to come off.
_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "“": "”",
    "„": "“",
    "‘": "’",  # noqa: RUF001 - curly single quotes, not backticks
    "«": "»",
    "「": "」",
    "『": "』",
}

_EMPHASIS = ("**", "__", "*", "_", "`")


def clean_response(text: str) -> str:
    """Strip the packaging models add around a translation.

    Pure and separately tested: this runs on every caption, and a bug here
    silently corrupts text rather than raising.
    """
    cleaned = _strip_fence(text.strip())
    cleaned = _PREAMBLE_RE.sub("", cleaned, count=1).strip()
    for _ in range(3):
        stripped = _strip_wrapper(cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    # Subtitles are one line: a model's hard wraps become spaces.
    return " ".join(cleaned.split())


def _strip_fence(text: str) -> str:
    if not text.startswith("```") or not text.endswith("```") or len(text) < 6:
        return text
    inner = text[3:-3]
    first, _, rest = inner.partition("\n")
    # ```json / ```text language hint on the opening line
    if rest and not first.strip().count(" ") and len(first.strip()) < 20:
        inner = rest
    return inner.strip()


def _strip_wrapper(text: str) -> str:
    if len(text) < 2:
        return text
    closing = _QUOTE_PAIRS.get(text[0])
    if closing and text.endswith(closing) and len(text) > 1:
        inner = text[1:-1].strip()
        # Don't unwrap a line that is itself quoting something inside it.
        if closing not in inner:
            return inner
    for marker in _EMPHASIS:
        if text.startswith(marker) and text.endswith(marker) and len(text) > 2 * len(marker):
            inner = text[len(marker) : -len(marker)].strip()
            if marker not in inner:
                return inner
    return text
