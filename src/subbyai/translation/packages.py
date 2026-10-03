"""Bounded client for Argos packs, without the unused PyTorch dependency tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..core.network import read_response

INDEX_REVISION = "ff90de60728f7c1338ff6b75974e4c89b2442d22"
INDEX_URL = (
    f"https://raw.githubusercontent.com/argosopentech/argospm-index/{INDEX_REVISION}/index.json"
)
_MAX_DOWNLOAD = 512 * 1024**2
_install_lock = threading.Lock()
_shutdown_cancel = threading.Event()


def cancel_downloads() -> None:
    """Called once at application shutdown, before joining installation workers."""
    _shutdown_cancel.set()


class Package:
    def __init__(self, package_path: Path):
        self.package_path = package_path
        metadata = package_path / "metadata.json"
        if metadata.stat().st_size > 128 * 1024:
            raise ValueError("Language pack metadata is too large")
        self.metadata = _metadata(json.loads(metadata.read_text(encoding="utf-8")))
        self.from_code, self.to_code = self.metadata["from_code"], self.metadata["to_code"]
        self.type = self.metadata.get("type", "translate")


class AvailablePackage:
    def __init__(self, metadata: dict, manager: PackageManager):
        self.metadata = _metadata(metadata)
        self.from_code, self.to_code = self.metadata["from_code"], self.metadata["to_code"]
        self.manager = manager

    def download(self) -> Path:
        self.manager.check_cancelled()
        links = self.metadata.get("links", [])
        url = next((url for url in links if _trusted_link(url)), None)
        if url is None:
            raise ValueError("This language pack has no trusted HTTPS download")
        self.manager.downloads_dir.mkdir(parents=True, exist_ok=True)
        _safe_child(self.manager.cache_dir, self.manager.downloads_dir)
        target = self.manager.downloads_dir / f"{self.from_code}_{self.to_code}.argosmodel"
        fd, scratch = tempfile.mkstemp(dir=target.parent, suffix=".partial")
        digest, size = hashlib.sha256(), 0
        deadline = time.monotonic() + 600
        try:
            with (
                os.fdopen(fd, "wb") as output,
                httpx.Client(
                    timeout=httpx.Timeout(30, connect=10),
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                for chunk in response.iter_bytes(64 * 1024):
                    self.manager.check_cancelled()
                    size += len(chunk)
                    if size > _MAX_DOWNLOAD or time.monotonic() > deadline:
                        raise ValueError("Language pack download exceeded its size or time limit")
                    output.write(chunk)
                    digest.update(chunk)
            expected = self.metadata.get("sha256")
            if expected and digest.hexdigest().lower() != str(expected).lower():
                raise ValueError("Language pack checksum did not match")
            os.replace(scratch, target)
            return target
        finally:
            Path(scratch).unlink(missing_ok=True)


class PackageManager:
    Package = Package

    def __init__(self, models_dir: Path, cancel_event: threading.Event | None = None):
        self.cancel_event = cancel_event if cancel_event is not None else _shutdown_cancel
        self.models_dir = models_dir
        self.cache_dir = models_dir.parent / f"{models_dir.name}-cache"
        self.downloads_dir = self.cache_dir / "downloads"
        for directory in (models_dir, self.cache_dir):
            if _is_link(directory):
                raise ValueError("Language pack folders cannot be symbolic links")
        self._available: list[AvailablePackage] = []

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Language pack setup was cancelled")

    def get_installed_packages(self) -> list[Package]:
        if not self.models_dir.exists():
            return []
        packs = []
        for folder in sorted(self.models_dir.iterdir()):
            if folder.is_symlink() or not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                pack = Package(folder)
                if (folder / "model" / "model.bin").is_file() and (
                    folder / "sentencepiece.model"
                ).is_file():
                    packs.append(pack)
            except (OSError, ValueError, TypeError):
                continue
        return packs

    def update_package_index(self) -> None:
        self.check_cancelled()
        with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:  # noqa: SIM117
            with client.stream("GET", INDEX_URL) as response:
                response.raise_for_status()
                raw = json.loads(
                    read_response(
                        response,
                        max_bytes=2 * 1024**2,
                        deadline=time.monotonic() + 30,
                    )
                )
        if not isinstance(raw, list) or len(raw) > 1000:
            raise ValueError("Invalid language pack catalog")
        available = []
        for metadata in raw:
            try:
                available.append(AvailablePackage(metadata, self))
            except (ValueError, TypeError):
                continue
        self._available = available
        self.check_cancelled()

    def get_available_packages(self) -> list[AvailablePackage]:
        return list(self._available)

    def install_from_path(self, archive: Path) -> None:
        from .builtin import validate_archive

        self.check_cancelled()
        validate_archive(archive)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        with (
            _install_lock,
            tempfile.TemporaryDirectory(
                dir=self.models_dir.parent, prefix="language-install-"
            ) as staging,
        ):
            root = Path(staging)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)  # Members validated before extraction.
            metadata_files = list(root.glob("*/metadata.json"))
            if (root / "metadata.json").is_file():
                metadata_files.append(root / "metadata.json")
            if len(metadata_files) != 1:
                raise ValueError("A language archive must contain one complete pack")
            folder = metadata_files[0].parent
            pack = Package(folder)
            if (
                not (folder / "model" / "model.bin").is_file()
                or not (folder / "sentencepiece.model").is_file()
            ):
                raise ValueError("Language pack is incomplete or uses an unsupported tokenizer")
            target = self.models_dir / f"translate-{pack.from_code}_{pack.to_code}"
            backup = self.models_dir / f".previous-{pack.from_code}_{pack.to_code}"
            _safe_child(self.models_dir, target)
            _safe_child(self.models_dir, backup)
            self.check_cancelled()
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                target.rename(backup)
            try:
                folder.rename(target)
            except OSError:
                if backup.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                shutil.rmtree(backup)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _safe_child(root: Path, path: Path) -> None:
    if _is_link(root) or _is_link(path) or path.resolve().parent != root.resolve():
        raise ValueError("Language pack path escaped its storage folder")


def _metadata(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("type", "translate") != "translate":
        raise ValueError("Invalid language pack metadata")
    for field in ("from_code", "to_code"):
        if not isinstance(raw.get(field), str) or not re.fullmatch(r"[a-z]{2,3}", raw[field]):
            raise ValueError("Invalid language pair")
    if not isinstance(raw.get("links", []), list):
        raise ValueError("Invalid language pack links")
    return raw


def _trusted_link(url: str) -> bool:
    if not isinstance(url, str) or any(ord(char) < 33 for char in url):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "argos-net.com"
            and parsed.path.startswith("/v1/")
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False
