"""Provider API keys stored exclusively in the operating-system credential store.

Unavailable or insecure backends fail explicitly; keys are never written to
settings JSON or substituted with a plaintext file backend."""

from __future__ import annotations

import hashlib
import logging

from ..branding import APP_ID

log = logging.getLogger(__name__)

_SERVICE = f"{APP_ID}-providers"


class SecretStoreUnavailable(RuntimeError):
    """No usable credential store on this machine."""


def is_available() -> bool:
    """Whether a real credential store is present."""
    return _backend() is not None


def store_name() -> str:
    """Human-readable name of the store, for telling the user where the key went."""
    backend = _backend()
    if backend is None:
        return ""
    name = type(backend).__name__
    return {
        "WinVaultKeyring": "Windows Credential Manager",
        "Keyring": "macOS Keychain",
        "SecretService": "your system keyring",
        "KWallet": "KWallet",
    }.get(name, "your system keyring")


def set_key(provider_id: str, key: str) -> None:
    """Save (or clear, when ``key`` is empty) a provider's API key."""
    if not key:
        delete_key(provider_id)
        return
    backend = _backend()
    if backend is None:
        raise SecretStoreUnavailable("A secure operating-system credential store is unavailable.")
    try:
        backend.set_password(_SERVICE, provider_id, key)
    except Exception as exc:
        raise SecretStoreUnavailable(
            "This computer has no place to store secrets safely, so the key was "
            "not saved."
        ) from exc


def get_key(provider_id: str) -> str | None:
    backend = _backend()
    if backend is None:
        return None
    try:
        return backend.get_password(_SERVICE, provider_id)
    except Exception:
        log.debug("Could not read a key from the credential store", exc_info=True)
        return None


def delete_key(provider_id: str) -> None:
    backend = _backend()
    if backend is None:
        raise SecretStoreUnavailable(
            "The credential store is unavailable; the key was not removed."
        )
    try:
        if backend.get_password(_SERVICE, provider_id) is None:
            return
        backend.delete_password(_SERVICE, provider_id)
    except Exception as exc:
        raise SecretStoreUnavailable(
            "The stored key could not be removed. Retry in your OS vault."
        ) from exc


def endpoint_key_id(provider_id: str, endpoint: str) -> str:
    """Bind credentials to the normalized destination, without storing its URL."""
    from .network import validate_endpoint

    digest = hashlib.sha256(validate_endpoint(endpoint).encode("utf-8")).hexdigest()
    return f"{provider_id}:{digest}"


def _backend():
    """Use supported OS vaults; never accept a plaintext/plugin fallback."""
    try:
        import keyring
        backend = keyring.get_keyring()
    except Exception:
        return None
    if type(backend).__module__ in {"keyring.backends.Windows", "keyring.backends.macOS"}:
        return backend
    return None
