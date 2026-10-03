"""Release contracts using real local HTTP sockets and isolated credential stores."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from subbyai.asr.engine import EngineError
from subbyai.asr.remote import RemoteASR
from subbyai.core import secrets
from subbyai.core.settings import ProcessingSettings, ProviderSettings, SettingsStore
from subbyai.translation import assistant
from subbyai.translation.base import TranslationError
from subbyai.ui.settings_intelligence import IntelligenceSection
from subbyai.ui.settings_view import SettingsDeps


@pytest.fixture
def endpoint():
    requests = []
    replies = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append((self.path, dict(self.headers), body))
            status, payload = replies.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Location", "https://example.com/never-follow")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_real_http_audio_upload_retries_busy_server(endpoint):
    url, requests, replies = endpoint
    replies.extend([(503, b"{}"), (200, b'{"text":"Public test","language":"en"}')])
    engine = RemoteASR(
        ProcessingSettings(base_url=url, consent_url=url, model="test", retries=1),
        "example-placeholder",
    )
    try:
        result = engine.transcribe(np.zeros(1600, dtype=np.float32), "en")
        assert result.text == "Public test"
        assert len(requests) == 2
        assert requests[0][0] == "/v1/audio/transcriptions"
        assert requests[0][1]["Authorization"] == "Bearer example-placeholder"
        assert b"RIFF" in requests[0][2]
    finally:
        engine.close()


@pytest.mark.parametrize("status,payload", [(302, b"{}"), (401, b"{}"), (200, b"invalid")])
def test_real_http_failure_does_not_retry_or_redirect(endpoint, status, payload):
    url, requests, replies = endpoint
    replies.append((status, payload))
    engine = RemoteASR(ProcessingSettings(base_url=url, consent_url=url, retries=2))
    try:
        with pytest.raises(EngineError):
            engine.transcribe(np.zeros(1600))
        assert len(requests) == 1
    finally:
        engine.close()


def test_assistant_uses_real_chat_protocol_and_closes_request(endpoint):
    url, requests, replies = endpoint
    replies.append((200, b'{"choices":[{"message":{"content":"A summary."}}]}'))
    provider = assistant.TranscriptAssistant(ProviderSettings(base_url=url, model="test"))
    assert provider.ask("Summarize", "Public transcript") == "A summary."
    path, _, data = requests[0]
    assert path == "/v1/chat/completions"
    payload = json.loads(data)
    assert payload["stream"] is False and payload["max_tokens"] == 2048
    assert "Public transcript" in payload["messages"][-1]["content"]


@pytest.mark.parametrize("question,text", [("x" * 2001, "ok"), ("ok", "x" * 32001)])
def test_assistant_rejects_large_input_before_network(endpoint, question, text):
    url, requests, _ = endpoint
    provider = assistant.TranscriptAssistant(ProviderSettings(base_url=url, model="test"))
    with pytest.raises(TranslationError, match="shorter"):
        provider.ask(question, text)
    assert requests == []


def test_endpoint_changes_require_new_key_and_consent(qt_app, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    old = ProviderSettings(
        id="chat", kind="openai", base_url="https://one.example.com", model="test",
        enabled=True, consent_url="https://one.example.com",
    )
    store.settings.intelligence.providers = [old]
    section = IntelligenceSection(store, SettingsDeps())
    section._on_submitted({
        "id": "chat", "kind": "openai", "label": "Chat", "base_url": "https://two.example.com",
        "model": "test", "api_key": "",
    })
    new = section.config_for("chat")
    assert not new.enabled and not new.consent_url
    assert secrets.endpoint_key_id("chat", old.base_url) != secrets.endpoint_key_id(
        "chat", new.base_url
    )
    section.deleteLater()


def test_vault_failure_keeps_provider_configuration(qt_app, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")

    def unavailable(*_args):
        raise secrets.SecretStoreUnavailable("Unavailable")

    section = IntelligenceSection(store, SettingsDeps(api_key_set=unavailable))
    section._on_submitted({
        "id": "", "kind": "openai", "label": "Chat", "base_url": "https://example.com",
        "model": "test", "api_key": "example-placeholder",
    })
    assert store.settings.intelligence.providers == []
    assert "securely" in section.form.note.text()
    section.deleteLater()


def test_credential_delete_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(secrets, "_backend", lambda: None)
    with pytest.raises(secrets.SecretStoreUnavailable, match="not removed"):
        secrets.delete_key("test")


def test_key_scope_is_canonical_and_contains_no_address():
    first = secrets.endpoint_key_id("chat", "https://example.com/v1/")
    assert first == secrets.endpoint_key_id("chat", "https://example.com")
    assert "example.com" not in first and first.startswith("chat:")


def test_corrupt_history_is_preserved_and_app_can_continue(tmp_path):
    from subbyai.storage import SessionStore

    path = tmp_path / "sessions.db"
    damaged = b"damaged database bytes"
    path.write_bytes(damaged)
    store = SessionStore(path)
    try:
        assert (store.recovered_backup / "sessions.db").read_bytes() == damaged
        assert store.list_sessions() == []
        assert store.create_session("Public recovery test") > 0
    finally:
        store.close()
