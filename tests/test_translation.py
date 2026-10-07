"""Translation layer: privacy tiers, chain policy, LLM transport, Argos wrapper.

Everything runs offline. The LLM lane is exercised through httpx.MockTransport
and Argos through a stub module, so no packet leaves and no model downloads.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from subbyai.core.events import PrivacyTier
from subbyai.core.settings import IntelligenceSettings, ProviderSettings
from subbyai.translation import build_chain
from subbyai.translation.base import (
    TranslationError,
    TranslationProvider,
    TranslationResult,
    language_name,
    normalize_code,
    tier_for_url,
)
from subbyai.translation.builtin import ArgosProvider
from subbyai.translation.chain import TranslationChain
from subbyai.translation.llm import OpenAiCompatibleProvider, clean_response


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StubProvider(TranslationProvider):
    """A provider whose behaviour each test dictates."""

    def __init__(
        self,
        id: str,
        *,
        output: str | None = None,
        error: str | None = None,
        supports_pair: bool = True,
        available: bool = True,
        tier: PrivacyTier = PrivacyTier.ON_DEVICE,
    ) -> None:
        self._id = id
        self._output = output
        self._error = error
        self._supports = supports_pair
        self._available = available
        self._tier = tier
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []
        self.prepared: list[tuple[str, str]] = []
        self.closed = False

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._id.title()

    @property
    def tier(self) -> PrivacyTier:
        return self._tier

    @property
    def is_available(self) -> bool:
        return self._available

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return self._supports

    def prepare(self, source_lang: str, target_lang: str) -> None:
        self.prepared.append((source_lang, target_lang))

    def translate(self, text, source_lang, target_lang, context=None) -> TranslationResult:
        self.calls.append((text, list(context or [])))
        if self._error is not None:
            raise TranslationError(self._error)
        return TranslationResult(
            text=self._output or f"[{self._id}]{text}",
            provider_id=self._id,
            provider_label=self.label,
            tier=self._tier,
        )

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------
# The privacy guarantee
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434", PrivacyTier.ON_DEVICE),
        ("http://LOCALHOST:1234/v1", PrivacyTier.ON_DEVICE),
        ("http://127.0.0.1:11434", PrivacyTier.ON_DEVICE),
        ("http://127.5.4.3:8080", PrivacyTier.ON_DEVICE),
        ("http://[::1]:8080/v1", PrivacyTier.ON_DEVICE),
        ("localhost:1234", PrivacyTier.ON_DEVICE),
        ("http://ollama.localhost", PrivacyTier.ON_DEVICE),
        ("http://192.168.1.50:11434", PrivacyTier.LOCAL_NETWORK),
        ("http://10.0.0.7:1234/v1", PrivacyTier.LOCAL_NETWORK),
        ("http://172.16.3.9", PrivacyTier.LOCAL_NETWORK),
        ("http://169.254.10.10", PrivacyTier.LOCAL_NETWORK),
        ("http://studio.local:1234", PrivacyTier.LOCAL_NETWORK),
        ("http://nas", PrivacyTier.LOCAL_NETWORK),
        ("http://workstation:11434", PrivacyTier.LOCAL_NETWORK),
        ("https://api.openai.com/v1", PrivacyTier.CLOUD),
        ("https://example.com", PrivacyTier.CLOUD),
        ("http://172.32.0.1", PrivacyTier.CLOUD),  # just outside RFC1918
        ("https://8.8.8.8", PrivacyTier.CLOUD),
        ("", PrivacyTier.CLOUD),  # unknown shapes are assumed to be the worst
        ("not a url at all", PrivacyTier.CLOUD),
    ],
)
def test_tier_is_computed_from_the_endpoint(url, expected):
    assert tier_for_url(url) is expected


def test_provider_cannot_declare_its_own_tier():
    """A "Local AI" label pointing at a hosted endpoint is still CLOUD."""
    provider = OpenAiCompatibleProvider(
        id="sneaky",
        label="Totally Local AI",
        base_url="https://api.example-ai.com/v1",
        model="m",
        kind="ollama",
    )
    assert provider.tier is PrivacyTier.CLOUD


def test_language_names_and_codes():
    assert language_name("ja") == "Japanese"
    assert language_name("zh-CN") == "Chinese"
    assert language_name("xx") == "xx"
    assert normalize_code("en_GB") == "en"


# --------------------------------------------------------------------------
# Chain policy
# --------------------------------------------------------------------------


def test_chain_falls_through_to_the_second_provider():
    first = StubProvider("first", error="offline")
    second = StubProvider("second", output="hola")
    chain = TranslationChain([first, second])

    result = chain.translate("hello", "en", "es")

    assert result.text == "hola"
    assert result.provider_id == "second"
    assert chain.last_error is None  # a recovered line is not an error state


def test_chain_skips_providers_that_do_not_support_the_pair():
    unsupported = StubProvider("argos", supports_pair=False)
    llm = StubProvider("llm", output="bonjour")
    chain = TranslationChain([unsupported, llm])

    assert chain.translate("hello", "en", "fr").provider_id == "llm"
    assert unsupported.calls == []


def test_chain_skips_providers_that_are_not_set_up():
    """A cloud provider with no key must not cost three round trips per line."""
    unconfigured = StubProvider("cloud", available=False)
    local = StubProvider("local", output="hola")
    chain = TranslationChain([unconfigured, local])

    assert chain.translate("hello", "en", "es").provider_id == "local"
    assert unconfigured.calls == []


def test_chain_raises_only_when_everything_fails():
    chain = TranslationChain(
        [StubProvider("a", error="no pack"), StubProvider("b", error="connection refused")]
    )
    with pytest.raises(TranslationError, match="connection refused"):
        chain.translate("hello", "en", "es")
    assert chain.last_error == "connection refused"
    assert chain.last_error_provider == "B"


def test_chain_with_no_providers_says_so():
    chain = TranslationChain([])
    with pytest.raises(TranslationError, match="No translator"):
        chain.translate("hello", "en", "es")


def test_circuit_breaker_opens_after_three_failures_and_closes_after_the_window():
    clock = FakeClock()
    flaky = StubProvider("flaky", error="connection refused")
    backup = StubProvider("backup", output="ok")
    chain = TranslationChain([flaky, backup], clock=clock)

    for _ in range(3):
        chain.translate("hi", "en", "es")
    assert len(flaky.calls) == 3

    # Fourth line: the breaker is open, so the dead provider is not even tried.
    chain.translate("hi", "en", "es")
    assert len(flaky.calls) == 3

    clock.advance(59)
    chain.translate("hi", "en", "es")
    assert len(flaky.calls) == 3

    clock.advance(2)  # past the 60s cooldown
    chain.translate("hi", "en", "es")
    assert len(flaky.calls) == 4


def test_open_breaker_keeps_the_underlying_reason_for_the_ui():
    clock = FakeClock()
    provider = StubProvider("ollama", error="Ollama isn't responding.")
    chain = TranslationChain([provider], clock=clock)
    for _ in range(3):
        with pytest.raises(TranslationError):
            chain.translate("hi", "en", "es")

    with pytest.raises(TranslationError, match="paused"):
        chain.translate("hi", "en", "es")
    assert chain.last_error == "Ollama isn't responding."
    assert chain.last_error_provider == "Ollama"
    assert chain.is_paused() is True

    clock.advance(61)
    assert chain.is_paused() is False


def test_a_success_resets_the_failure_count():
    clock = FakeClock()
    provider = StubProvider("p", error="boom")
    chain = TranslationChain([provider], clock=clock)
    for _ in range(2):
        with pytest.raises(TranslationError):
            chain.translate("hi", "en", "es")

    provider._error = None
    chain.translate("hi", "en", "es")
    provider._error = "boom"

    # Two more failures must not be enough to open the breaker.
    for _ in range(2):
        with pytest.raises(TranslationError):
            chain.translate("hi", "en", "es")
    assert len(provider.calls) == 5


def test_provider_exceptions_are_contained():
    class Exploding(StubProvider):
        def translate(self, text, source_lang, target_lang, context=None):
            raise RuntimeError("bug in a provider")

    backup = StubProvider("backup", output="fine")
    chain = TranslationChain([Exploding("boom"), backup])
    assert chain.translate("hi", "en", "es").text == "fine"


def test_context_pairs_are_passed_and_capped_at_four():
    provider = StubProvider("p")
    chain = TranslationChain([provider])
    for line in ("one", "two", "three", "four", "five", "six"):
        chain.translate(line, "en", "es")

    _, context = provider.calls[-1]
    assert context == [
        ("two", "[p]two"),
        ("three", "[p]three"),
        ("four", "[p]four"),
        ("five", "[p]five"),
    ]
    assert len(chain.context_pairs) == 4


def test_failed_lines_never_enter_the_context():
    good = StubProvider("good")
    chain = TranslationChain([StubProvider("bad", error="nope"), good])
    chain.translate("kept", "en", "es")
    assert chain.context_pairs == [("kept", "[good]kept")]


def test_explicit_context_overrides_the_memory_and_is_capped():
    provider = StubProvider("p")
    chain = TranslationChain([provider])
    supplied = [(f"s{i}", f"t{i}") for i in range(6)]
    chain.translate("now", "en", "es", context=supplied)
    assert provider.calls[0][1] == supplied[-4:]


def test_reset_clears_context_and_breakers():
    clock = FakeClock()
    provider = StubProvider("p", error="down")
    chain = TranslationChain([provider], clock=clock)
    for _ in range(3):
        with pytest.raises(TranslationError):
            chain.translate("hi", "en", "es")

    chain.reset()
    assert chain.last_error is None
    assert chain.context_pairs == []
    with pytest.raises(TranslationError):
        chain.translate("hi", "en", "es")
    assert len(provider.calls) == 4  # tried again immediately after the reset


def test_prepare_and_close_reach_every_provider():
    a, b = StubProvider("a"), StubProvider("b")
    chain = TranslationChain([a, b])
    chain.prepare("en", "es")
    chain.close()
    assert a.prepared == b.prepared == [("en", "es")]
    assert a.closed and b.closed


# --------------------------------------------------------------------------
# LLM lane
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bonjour", "Bonjour"),
        ("  Bonjour le monde \n", "Bonjour le monde"),
        ('"Bonjour"', "Bonjour"),
        ("“Bonjour”", "Bonjour"),
        ("「こんにちは」", "こんにちは"),
        ("Sure! Here's the translation: Bonjour", "Bonjour"),
        ("Here is the translation:\nBonjour", "Bonjour"),
        ("Here’s the translation: Bonjour", "Bonjour"),  # noqa: RUF001 - curly apostrophe
        ("Translation: Bonjour", "Bonjour"),
        ("Translated text - Bonjour", "Bonjour"),
        ('Certainly, the translation is: "Bonjour"', "Bonjour"),
        ("**Bonjour**", "Bonjour"),
        ("```\nBonjour\n```", "Bonjour"),
        ("```text\nBonjour\n```", "Bonjour"),
        ("Bonjour\nle monde", "Bonjour le monde"),
        # Left alone: not packaging, just speech.
        ("Sure, I'll be there.", "Sure, I'll be there."),
        ('He said "hello" and left.', 'He said "hello" and left.'),
        ("The translation of that word escapes me.", "The translation of that word escapes me."),
    ],
)
def test_clean_response(raw, expected):
    assert clean_response(raw) == expected


def _provider(handler, **kwargs) -> OpenAiCompatibleProvider:
    options = {
        "id": "ollama",
        "label": "Ollama",
        "base_url": "http://localhost:11434",
        "model": "qwen2.5:7b",
        "kind": "ollama",
    }
    options.update(kwargs)
    return OpenAiCompatibleProvider(transport=httpx.MockTransport(handler), **options)


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_llm_request_shape_and_context_turns():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = httpx.Response(200, content=request.content).json()
        return _chat_reply('  "Bonjour, Maître Tanaka."  ')

    provider = _provider(handler)
    result = provider.translate(
        "Hello, Master Tanaka.",
        "en",
        "fr",
        context=[("a", "A"), ("b", "B"), ("c", "C"), ("d", "D"), ("e", "E")],
    )

    assert seen["method"] == "POST"
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == "qwen2.5:7b"
    assert body["stream"] is False

    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert "English" in messages[0]["content"] and "French" in messages[0]["content"]
    # Only the last four pairs, replayed as user/assistant turns.
    assert [m["role"] for m in messages[1:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert [m["content"] for m in messages[1:5]] == ["b", "B", "c", "C"]
    assert messages[-1]["content"] == "Hello, Master Tanaka."

    assert result.text == "Bonjour, Maître Tanaka."
    assert result.provider_id == "ollama"
    assert result.tier is PrivacyTier.ON_DEVICE


def test_llm_sends_the_api_key_only_when_one_is_set():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return _chat_reply("hola")

    _provider(handler).translate("hi", "en", "es")
    assert seen["auth"] is None

    _provider(handler, api_key="sk-test").translate("hi", "en", "es")
    assert seen["auth"] == "Bearer sk-test"


def test_llm_timeout_becomes_a_translation_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TranslationError, match="too long"):
        _provider(handler).translate("hi", "en", "es")


def test_llm_connection_refused_names_the_address():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TranslationError) as excinfo:
        _provider(handler).translate("hi", "en", "es")
    message = str(excinfo.value)
    assert "Ollama" in message and "http://localhost:11434" in message
    assert "httpx" not in message.lower()


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "API key"), (404, "no model called"), (429, "rate-limiting"), (503, "server error")],
)
def test_llm_http_errors_are_plain_language(status, fragment):
    provider = _provider(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(TranslationError, match=fragment):
        provider.translate("hi", "en", "es")


def test_llm_malformed_reply_is_an_error_not_a_crash():
    provider = _provider(lambda request: httpx.Response(200, json={"nope": True}))
    with pytest.raises(TranslationError, match="shape we didn't expect"):
        provider.translate("hi", "en", "es")

    provider = _provider(lambda request: httpx.Response(200, content=b"<html>bad gateway"))
    with pytest.raises(TranslationError, match="couldn't read"):
        provider.translate("hi", "en", "es")


def test_llm_base_url_accepts_a_trailing_v1():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _chat_reply("ok")

    provider = _provider(handler, base_url="http://localhost:1234/v1/", kind="lmstudio")
    provider.translate("hi", "en", "es")
    assert seen["url"] == "http://localhost:1234/v1/chat/completions"


def test_llm_lists_models_from_both_ollama_endpoints():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b"}]})
        return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})

    assert _provider(handler).list_models() == ["gemma3:4b", "qwen2.5:7b"]


def test_test_connection_reports_a_missing_model():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "other-model"}], "models": []})

    ok, message = _provider(handler).test_connection()
    assert ok is False
    assert "qwen2.5:7b" in message and "other-model" in message

    ok, message = _provider(handler, model="other-model").test_connection()
    assert ok is True
    assert "on this device" in message


def test_test_connection_never_raises_when_the_server_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    ok, message = _provider(handler).test_connection()
    assert ok is False
    assert "Couldn't reach" in message


def test_llm_availability_requires_a_key_only_in_the_cloud():
    local = OpenAiCompatibleProvider("l", "Local", "http://localhost:11434", "m", kind="ollama")
    assert local.is_available is True
    cloud = OpenAiCompatibleProvider("c", "Cloud", "https://api.openai.com", "gpt-4o-mini")
    assert cloud.is_available is False
    assert cloud.__class__("c", "Cloud", "https://api.openai.com", "m", "sk-x").is_available is True
    assert OpenAiCompatibleProvider("n", "No model", "http://localhost:1", "").is_available is False


# --------------------------------------------------------------------------
# Argos lane (stubbed: no packages, no downloads)
# --------------------------------------------------------------------------


class FakePackage:
    def __init__(self, from_code: str, to_code: str) -> None:
        self.from_code = from_code
        self.to_code = to_code
        self.type = "translate"


class FakeArgos:
    """Stands in for argostranslate.package.

    The translation itself is faked separately (see the ``argos`` fixture): the
    provider now runs CTranslate2 directly rather than going through
    argostranslate.translate, so routing and pivoting are exercised for real and
    only the model is replaced.
    """

    def __init__(self, installed=(), available=(), output="translated") -> None:
        self.installed = [FakePackage(*pair) for pair in installed]
        self.available = [FakePackage(*pair) for pair in available]
        self.output = output
        self.index_updated = 0
        self.installs: list[tuple[str, str]] = []

    # package module
    def get_installed_packages(self):
        return list(self.installed)

    def get_available_packages(self):
        return list(self.available)

    def update_package_index(self):
        self.index_updated += 1

    def install_from_path(self, path):
        self.installs.append(path)
        self.installed.append(FakePackage(*path))

    def translate(self, text):
        return self.output


class FakeAvailable(FakePackage):
    def download(self):
        return (self.from_code, self.to_code)


@pytest.fixture
def argos(monkeypatch):
    """A fake pack index plus a fake model, with the real routing in between."""
    fake = FakeArgos()
    from types import SimpleNamespace

    from subbyai.translation import builtin

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

        def join(self):
            pass

    # Own scheduling/state per fixture; never leave a worker running after its
    # temporary pack store and fake backend have been torn down.
    monkeypatch.setattr(builtin, "_installing", set())
    monkeypatch.setattr(builtin, "_failed_installs", {})
    monkeypatch.setattr(builtin, "_failed_since", {})
    monkeypatch.setattr(builtin, "_install_threads", set())
    monkeypatch.setattr(
        builtin, "threading",
        SimpleNamespace(Thread=InlineThread, current_thread=lambda: None),
    )
    fake.hops: list[tuple[str, str]] = []
    monkeypatch.setattr("subbyai.translation.builtin._argos", lambda models_dir: fake)
    # This fixture substitutes pack identifiers for downloaded ZIP files.
    # Archive validation is exercised with real ZIPs in test_safety_contracts.
    monkeypatch.setattr("subbyai.translation.builtin.validate_archive", lambda archive: None)

    def fake_hop(self, text, source, target):
        installed = {(p.from_code, p.to_code) for p in fake.installed}
        if (source, target) not in installed:
            from subbyai.translation.base import TranslationError

            raise TranslationError(f"The {source} to {target} language pack isn't installed yet.")
        fake.hops.append((source, target))
        return fake.output

    monkeypatch.setattr("subbyai.translation.builtin.ArgosProvider._translate_hop", fake_hop)
    return fake


def test_argos_reports_installed_pairs_without_network(argos):
    argos.installed = [FakePackage("ja", "en"), FakePackage("en", "de")]
    provider = ArgosProvider()
    assert provider.installed_pairs() == [("en", "de"), ("ja", "en")]
    assert provider.is_available is True
    assert provider.tier is PrivacyTier.ON_DEVICE
    assert argos.index_updated == 0


def test_argos_caches_the_pack_list_between_captions(argos):
    argos.installed = [FakePackage("ja", "en")]
    provider = ArgosProvider()
    assert provider.supports("ja", "en") is True

    argos.installed = []  # a rescan would now say no
    assert provider.supports("ja", "en") is True
    provider.close()
    assert provider.supports("ja", "en") is False


def test_argos_supports_direct_and_pivoted_pairs(argos):
    argos.installed = [FakePackage("ja", "en"), FakePackage("en", "de")]
    provider = ArgosProvider()
    assert provider.supports("ja", "en") is True
    assert provider.supports("ja-JP", "de") is True  # pivots through English
    assert provider.supports("ja", "ko") is False
    assert provider.supports("", "de") is False  # auto-detect gives it nothing
    assert provider.supports("de", "de") is False


def test_argos_pivots_through_english_when_there_is_no_direct_pack(argos):
    """supports() promises pivot routing, so translate() must actually do it."""
    argos.installed = [FakePackage("ja", "en"), FakePackage("en", "de")]
    result = ArgosProvider().translate("おはよう", "ja", "de")
    assert argos.hops == [("ja", "en"), ("en", "de")]
    assert result.text == "translated"


def test_argos_uses_a_direct_pack_in_one_hop(argos):
    argos.installed = [FakePackage("ja", "en"), FakePackage("ja", "de")]
    ArgosProvider().translate("おはよう", "ja", "de")
    assert argos.hops == [("ja", "de")]


def test_argos_translates_and_ignores_context(argos):
    argos.installed = [FakePackage("ja", "en")]
    argos.output = "  Good morning.  "
    result = ArgosProvider().translate("おはよう", "ja", "en", context=[("a", "b")])
    assert result.text == "Good morning."
    assert result.provider_id == "builtin"
    assert result.tier is PrivacyTier.ON_DEVICE


def test_argos_missing_pack_starts_fetching_it(argos):
    """A pair we have not got yet is a download, not a dead end.

    This asserted "isn't installed yet" before. That was the observable half of
    the bug: nothing ever installed the pack, so the message was permanent.
    """
    with pytest.raises(TranslationError, match=r"[Dd]ownload|isn't available"):
        ArgosProvider().translate("hi", "en", "ko")


def test_argos_empty_output_fails_so_the_chain_moves_on(argos):
    argos.installed = [FakePackage("en", "es")]
    argos.output = "   "
    with pytest.raises(TranslationError):
        ArgosProvider().translate("hi", "en", "es")


def test_argos_prepare_installs_the_missing_pair(argos):
    argos.available = [FakeAvailable("en", "es")]
    provider = ArgosProvider()
    provider.prepare("en", "es")
    assert argos.installs == [("en", "es")]
    assert provider.supports("en", "es") is True

    # Already installed: no second download, no index fetch.
    provider.prepare("en", "es")
    assert len(argos.installs) == 1


def test_argos_prepare_explains_an_unavailable_pair(argos):
    argos.available = [FakeAvailable("en", "es")]
    with pytest.raises(TranslationError, match="isn't available"):
        ArgosProvider().prepare("en", "ko")


def test_argos_prepare_tolerates_an_unknown_source_language(argos):
    """Auto-detect is the default, so "we don't know yet" is not an error.

    The previous assertion (that this raises "both languages") encoded the bug:
    the app calls prepare at session start, before anything has been spoken.
    """
    ArgosProvider().prepare("", "es")  # must not raise


def test_argos_network_failure_stays_a_translation_error(monkeypatch):
    class Offline(FakeArgos):
        def get_available_packages(self):
            raise OSError("no route to host")

    fake = Offline()
    monkeypatch.setattr("subbyai.translation.builtin._argos", lambda models_dir: (fake, fake))
    provider = ArgosProvider()
    with pytest.raises(TranslationError, match="Check your connection"):
        provider.prepare("en", "es")
    with pytest.raises(TranslationError, match="Check your connection"):
        provider.available_pairs()


def test_argos_import_failure_is_survivable(monkeypatch):
    def explode(models_dir):
        raise TranslationError("The built-in translator isn't installed.")

    monkeypatch.setattr("subbyai.translation.builtin._argos", explode)
    provider = ArgosProvider()
    assert provider.is_available is False
    assert provider.supports("en", "es") is False


def test_argos_uses_the_app_translation_directory(monkeypatch, isolated_dirs):
    from subbyai import paths

    assert ArgosProvider().models_dir == paths.translation_models_dir()


# --------------------------------------------------------------------------
# Wiring from settings
# --------------------------------------------------------------------------


def test_build_chain_follows_the_configured_order():
    intelligence = IntelligenceSettings(
        translation_order=["local-llm", "builtin"],
        providers=[
            ProviderSettings(
                id="local-llm",
                kind="ollama",
                label="Ollama",
                base_url="http://localhost:11434",
                model="qwen2.5:7b",
                enabled=True,
            )
        ],
    )
    chain = build_chain(intelligence)
    assert [p.id for p in chain.providers] == ["local-llm", "builtin"]
    assert chain.providers[0].tier is PrivacyTier.ON_DEVICE


def test_build_chain_leaves_out_disabled_and_unconsented_cloud_providers():
    cloud = ProviderSettings(
        id="openai",
        kind="openai",
        label="OpenAI",
        base_url="https://api.openai.com",
        model="gpt-4o-mini",
        enabled=True,
    )
    disabled = ProviderSettings(id="off", kind="lmstudio", base_url="http://localhost:1234")
    intelligence = IntelligenceSettings(
        translation_order=["openai", "off", "builtin"], providers=[cloud, disabled]
    )

    assert [p.id for p in build_chain(intelligence).providers] == ["builtin"]

    from subbyai.core.secrets import endpoint_key_id

    cloud.consent_url = "https://api.openai.com"
    keys = {endpoint_key_id("openai", cloud.base_url): "sk-test"}
    chain = build_chain(intelligence, api_key_for=keys.get)
    assert [p.id for p in chain.providers] == ["openai", "builtin"]
    assert chain.providers[0].is_available is True


# ---------------------------------------------------------------------------
# Auto-detection starts pack preparation only after a real language is known.


def test_prepare_accepts_an_unknown_source_language(argos):
    """Session start with auto-detect is normal, not a failure."""
    provider = ArgosProvider()
    provider.prepare("", "en")  # must not raise
    provider.prepare("auto", "en")  # must not raise
    assert argos.index_updated == 0, "nothing should be downloaded before we know the pair"


def test_prepare_still_reports_a_missing_target(argos):
    with pytest.raises(TranslationError, match="language to show captions in"):
        ArgosProvider().prepare("ja", "")


def test_translation_never_points_the_user_at_an_llm(argos):
    """The built-in translator is the default; its errors must not advertise a rival."""
    provider = ArgosProvider()
    messages = []
    for source, target in (("", "en"), ("auto", "en"), ("ja", "ko")):
        try:
            provider.translate("text", source, target)
        except TranslationError as exc:
            messages.append(str(exc))
    assert messages, "expected these pairs to fail"
    for message in messages:
        lowered = message.lower()
        assert "ai translator" not in lowered
        assert "llm" not in lowered
        assert "ollama" not in lowered


def test_detected_language_starts_the_pack_download(argos, monkeypatch):
    """The first recognised segment is when we learn which pack to fetch."""
    started: list[tuple[str, str]] = []

    def fake_thread(target=None, name=None, daemon=None):
        started.append((name or "", ""))

        class _T:
            def start(self_inner):
                target()

        return _T()

    argos.available = [FakeAvailable("es", "en")]
    monkeypatch.setattr("subbyai.translation.builtin.threading.Thread", fake_thread)

    provider = ArgosProvider()
    with pytest.raises(TranslationError, match=r"[Dd]ownloading"):
        provider.translate("hola", "es", "en")
    assert started, "a download should have been kicked off"
    assert argos.installs, "the pack for the detected pair should be installed"


def test_a_pack_download_is_not_retried_per_caption(argos, monkeypatch):
    """A pair with no pack must fail cheaply, not hammer the network."""
    import subbyai.translation.builtin as builtin

    monkeypatch.setattr(builtin, "_installing", set())
    monkeypatch.setattr(builtin, "_failed_installs", {})
    monkeypatch.setattr(
        builtin.threading,
        "Thread",
        lambda target=None, **kw: type("_T", (), {"start": lambda self_inner: target()})(),
    )
    argos.available = []  # no pack exists for this pair

    provider = ArgosProvider()
    for _ in range(3):
        with pytest.raises(TranslationError):
            provider.translate("text", "xx", "en")
    assert argos.index_updated <= 1, "the index should be fetched once, not per caption"


# --- pack directory layout -------------------------------------------------
#
# The tests above stub _argos out entirely, so none of them touch the real
# directory wiring. That is how a released build shipped with the download
# cache nested inside the packages directory: argos treats every subdirectory
# of the packages directory as a pack and raises if one has no metadata.json,
# so translation broke the moment the first pack was fetched. These exercise
# the real thing.


@pytest.fixture
def argos_dirs(tmp_path, monkeypatch):
    """Force the real _argos to configure a throwaway directory."""
    return tmp_path / "translation"


def test_the_download_cache_never_sits_inside_the_packages_directory(argos_dirs):
    from subbyai.translation.builtin import _argos

    manager = _argos(argos_dirs)

    downloads = manager.downloads_dir.resolve()
    packages = argos_dirs.resolve()
    assert not downloads.is_relative_to(packages), (
        "argos scans every subdirectory of the packages directory as a language "
        "pack; anything else living in there takes translation down with it"
    )
    assert not manager.cache_dir.resolve().is_relative_to(packages)


def test_a_half_installed_pack_does_not_hide_the_working_ones(argos_dirs, monkeypatch):
    """A crash mid-install leaves a directory with no metadata.json."""
    good = argos_dirs / "ja_en"
    good.mkdir(parents=True)
    (good / "metadata.json").write_text('{"from_code": "ja", "to_code": "en"}', encoding="utf-8")
    (argos_dirs / "interrupted").mkdir()

    class RealisticPackage:
        """Mirrors argos: reads metadata.json, raises when there isn't one."""

        def __init__(self, path):
            meta = json.loads((Path(path) / "metadata.json").read_text(encoding="utf-8"))
            self.from_code = meta["from_code"]
            self.to_code = meta["to_code"]
            self.type = "translate"

    class FakeModule:
        Package = RealisticPackage

        @staticmethod
        def get_installed_packages():
            raise FileNotFoundError("no metadata.json")

    monkeypatch.setattr("subbyai.translation.builtin._argos", lambda models_dir: FakeModule)
    provider = ArgosProvider(models_dir=argos_dirs)

    assert provider.installed_pairs() == [("ja", "en")]
    assert provider.is_available is True


def test_deleting_everything_reclaims_the_download_cache(argos_dirs):
    from subbyai.translation.builtin import cache_dir_for, installed_bytes, remove_all_packs

    pack = argos_dirs / "ja_en"
    pack.mkdir(parents=True)
    (pack / "model.bin").write_bytes(b"x" * 1000)
    cache = cache_dir_for(argos_dirs) / "downloads"
    cache.mkdir(parents=True)
    (cache / "ja_en.argosmodel").write_bytes(b"y" * 500)

    assert installed_bytes(argos_dirs) == 1500, "the storage figure must include the cache"
    assert remove_all_packs(argos_dirs) == 1500
    assert not cache.exists(), "archives must not survive 'delete all my data'"
    assert installed_bytes(argos_dirs) == 0
