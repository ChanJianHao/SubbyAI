"""Shared endpoint validation and bounded response handling for providers."""

import time
from urllib.parse import urlsplit, urlunsplit

import httpx


def validate_endpoint(url: str) -> str:
    from ..core.events import PrivacyTier
    from ..translation.base import tier_for_url

    if any(ord(char) < 32 for char in url):
        raise ValueError("The server address contains invalid characters.")
    text = url.strip().rstrip("/")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid server address including http:// or https://.") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a server address including http:// or https://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Put credentials in the API key field; remove URL credentials, queries and fragments."
        )
    if any(ord(char) < 33 for char in text) or port == 0:
        raise ValueError("The server address contains invalid characters or a port.")
    if parsed.scheme != "https" and tier_for_url(text) is PrivacyTier.CLOUD:
        raise ValueError("Internet servers require HTTPS to protect audio, text and API keys.")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def read_response(
    response: httpx.Response, max_bytes: int = 1_048_576, deadline: float | None = None
) -> bytes:
    """Bound decoded bytes as well as socket reads, including compressed replies."""
    chunks = []
    size = 0
    for chunk in response.iter_bytes():
        if deadline is not None and time.monotonic() > deadline:
            raise ValueError("The server took too long to finish its reply.")
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("The server reply is too large for a subtitle request.")
        chunks.append(chunk)
    return b"".join(chunks)
