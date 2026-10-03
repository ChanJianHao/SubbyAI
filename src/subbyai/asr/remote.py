"""Opt-in compatible /v1/audio/transcriptions provider, with in-memory WAVs."""

import io
import json
import time
import wave

import httpx
import numpy as np

from ..core.network import read_response, validate_endpoint
from ..core.settings import ProcessingSettings
from .engine import EngineError, Transcript


class RemoteASR:
    session_scoped = True

    def __init__(self, config: ProcessingSettings, api_key: str | None = None, transport=None):
        self.config = config
        try:
            self.root = validate_endpoint(config.base_url)
            consent = validate_endpoint(config.consent_url)
        except ValueError as exc:
            raise EngineError(str(exc)) from exc
        if consent != self.root:
            raise EngineError("Allow audio processing for this server in Remote Processing first.")
        self._client = httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds, connect=min(5.0, config.timeout_seconds)),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def load(self) -> None:
        pass  # Never upload test audio merely to probe a connection.

    def transcribe(self, audio, language=None, vocabulary="") -> Transcript:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size > 16000 * 20:
            raise EngineError("This audio phrase exceeds the remote processing limit.")
        data = {"model": self.config.model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        if vocabulary:
            data["prompt"] = vocabulary[:400]
        payload = pcm_wav(audio)
        for attempt in range(self.config.retries + 1):
            try:
                deadline = time.monotonic() + self.config.timeout_seconds
                request = self._client.build_request(
                    "POST",
                    f"{self.root}/v1/audio/transcriptions",
                    data=data,
                    files={"file": ("phrase.wav", payload, "audio/wav")},
                )
                response = self._client.send(request, stream=True)
                try:
                    if response.status_code in (401, 403):
                        raise EngineError("The speech server rejected your API key.")
                    if (
                        response.status_code == 429 or response.status_code >= 500
                    ) and attempt < self.config.retries:
                        response.close()
                        time.sleep(0.25 * 2**attempt)
                        continue
                    if not 200 <= response.status_code < 300:
                        raise EngineError(
                            "The speech server refused this phrase. Check its model and address."
                        )
                    body = json.loads(read_response(response, deadline=deadline))
                finally:
                    response.close()
                if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                    raise ValueError("Invalid response")
                # Some compatible APIs report full names, not language codes.
                from ..languages import LANGUAGES

                detected = body.get("language")
                if not isinstance(detected, str):
                    detected = language
                codes = {name.casefold(): code for code, name in LANGUAGES.items()}
                detected = codes.get(str(detected).casefold(), detected)
                if detected not in LANGUAGES:
                    detected = language
                return Transcript(
                    text=body["text"].strip()[:16000],
                    language=detected,
                    confidence_known=False,
                    speech_probability=1.0,
                    duration=audio.size / 16000,
                )
            except (httpx.HTTPError, ValueError) as exc:
                # Ambiguous failed POSTs are not replayed: the provider may have
                # processed and billed them already. Only explicit 429/5xx retry.
                raise EngineError(
                    "The speech server did not return a usable reply. Check your connection."
                ) from exc
        raise EngineError("The speech server is busy. Try again in a moment.")

    def close(self) -> None:
        self._client.close()

    def unload(self) -> None:
        self.close()


def pcm_wav(audio: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        clean = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
        output.writeframes((np.clip(clean, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()
