# Provider setup and development

Speech recognition and translation are independent. Changing one doesn't require replacing the
other. Local processing is the default; models download to the per-user cache.

## Remote speech

Enable Advanced Mode → Remote processing. Choose a server compatible with:

- `POST <base>/v1/audio/transcriptions`, multipart file upload.
- PCM16 mono WAV, 16 kHz, in memory; maximum accepted client phrase is 20 seconds.
- `model`, optional `language` and vocabulary `prompt`, and `response_format=verbose_json`.
- A JSON object containing `text`, with optional `language` and segment confidence metadata.

Enter the base address (with or without `/v1`), model ID and optional key. Credentials in URLs,
queries and fragments are rejected. Internet addresses require HTTPS; localhost/private IP HTTP
is allowed. Keys are sent as a bearer credential and saved in the OS credential store.
Explicitly allow audio to the shown address and save the processing choice. Switching back to
local clears active upload consent. The Live page shows the effective audio destination scope.

A busy server's 429/5xx responses receive at most two short retries. Authorization errors,
redirects and ambiguous transport failures are not retried. Responses are bounded to 1 MiB and
caption text is bounded. HTTP timeouts are per operation; reply reads also have a deadline.
If the provider fails repeatedly, the run stops with an inline recovery message. There is no
silent fallback from local audio to an external server.

Use a server you trust. A LAN server may forward data elsewhere, and address classification
cannot discover that behavior. Consult your provider's retention and billing policy.

## Text translation

Models & providers supports the built-in translator, Ollama, LM Studio and OpenAI-compatible
chat APIs. Enable and order the providers you want; the chain attempts them in order and skips
repeatedly failing providers temporarily. Original captions remain available on failure.
A compatible endpoint receives the current caption and bounded recent context, not PCM audio.
The built-in translator uses installed directional packs; unsupported directions have no promise
of coverage. Packs may pivot through an installed intermediate language.

Transcript assistance is a separate opt-in action in the transcript viewer. Its consent flow
shows what text would be sent. Endpoint badges classify On this device / Local network / Cloud.

## Implementing a provider

Implement `asr/base.py:ASRProvider` for speech and the result contract in `asr/engine.py`:
`load()` prepares resources, `transcribe()` returns a phrase result, and `close()` releases
session-owned resources. The pipeline supplies the frozen `SessionConfig`; never read mutable
settings inside a worker. A local reusable engine can stay in `EngineCache`, while remote
clients belong to a single run. Avoid hidden downloads in local `load()`.

For translation, implement `translation/base.py:TranslationProvider`: identity, availability,
language support, optional preparation, translation result and cleanup. Derive network privacy
scope from the configured URL, not a self-declared label. Return plain text; never render output
as HTML or execute it. Bound queues, requests, responses, context and caches. Do not log user
caption content, audio or credentials.

Add tests using fake engines or `httpx.MockTransport` for malformed replies, authorization,
timeouts, redirects, retries and shutdown. Run the frozen self-check when adding imports or
native libraries; source imports alone don't prove that the package contains them.
