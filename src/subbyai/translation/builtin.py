"""On-device translation using compatible CTranslate2/SentencePiece language packs.

Packs are validated and installed separately. Inference imports are lazy and
translations are bounded to short phrases already segmented by the pipeline."""

from __future__ import annotations

import logging
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from .. import paths
from ..core.events import PrivacyTier
from .base import (
    TranslationError,
    TranslationProvider,
    TranslationResult,
    language_name,
    normalize_code,
)

log = logging.getLogger(__name__)

BUILTIN_PROVIDER_ID = "builtin"

#: Language code meaning "we do not know yet"; mirrors ``languages.AUTO_DETECT``.
AUTO_DETECT = "auto"

# Pack installation is process-wide: two provider instances must not race to
# download the same pair, and a pair that cannot be installed must not be
# retried on every caption.
_install_lock = threading.Lock()
_installing: set[tuple[str, str]] = set()
_failed_installs: dict[tuple[str, str], str] = {}
_failed_since: dict[tuple[str, str], float] = {}
_install_threads: set[threading.Thread] = set()


def shutdown_downloads() -> None:
    """Cancel pack requests and join cleanup on the app's shutdown thread."""
    from .packages import cancel_downloads

    cancel_downloads()
    with _install_lock:
        threads = list(_install_threads)
    for thread in threads:
        thread.join()


def _argos(models_dir: Path) -> Any:
    """Open the compatible pack store. No process environment is mutated."""
    from .packages import PackageManager

    models_dir.mkdir(parents=True, exist_ok=True)
    manager = PackageManager(models_dir)
    manager.downloads_dir.mkdir(parents=True, exist_ok=True)
    return manager


def cache_dir_for(models_dir: Path) -> Path:
    """Where archives and any argos scratch files live. Never inside packages."""
    return models_dir.parent / f"{models_dir.name}-cache"


class ArgosProvider(TranslationProvider):
    """Local translation with no network at translate time."""

    def __init__(
        self,
        models_dir: Path | None = None,
        label: str = "Built-in translator",
    ) -> None:
        self._models_dir = models_dir
        self._label = label
        self._translations: dict[tuple[str, str], Any] = {}
        self._pairs: list[tuple[str, str]] | None = None

    # ---------- identity ----------

    @property
    def id(self) -> str:
        return BUILTIN_PROVIDER_ID

    @property
    def label(self) -> str:
        return self._label

    @property
    def tier(self) -> PrivacyTier:
        return PrivacyTier.ON_DEVICE

    @property
    def models_dir(self) -> Path:
        # Resolved late so tests (and a settings change) can move the directory.
        return self._models_dir or paths.translation_models_dir()

    @property
    def is_available(self) -> bool:
        try:
            return bool(self.installed_pairs())
        except TranslationError:
            return False

    # ---------- packages ----------

    def installed_pairs(self) -> list[tuple[str, str]]:
        """Language pairs installed on this machine. Never touches the network.

        Cached: this is consulted for every caption, and each miss re-reads one
        metadata file per installed pack. ``prepare`` is the only thing that can
        change the answer, and it clears the cache.
        """
        if self._pairs is not None:
            return list(self._pairs)
        package = _argos(self.models_dir)
        try:
            installed = package.get_installed_packages()
        except Exception:
            # Argos enumerates all-or-nothing: one directory without a
            # metadata.json — an install interrupted by a crash or a lost power
            # cable — raises, and every working pack becomes invisible along
            # with it. Fall back to reading the packs one at a time so a single
            # bad directory costs that pair and nothing else.
            installed = self._installed_packages_individually(package)
        pairs = {
            (pkg.from_code, pkg.to_code)
            for pkg in installed
            if getattr(pkg, "type", "translate") == "translate" and pkg.from_code and pkg.to_code
        }
        self._pairs = sorted(pairs)
        return list(self._pairs)

    def _installed_packages_individually(self, package: Any) -> list[Any]:
        """Read each pack directory on its own, skipping the unreadable ones."""
        found = []
        for entry in sorted(self.models_dir.iterdir()):
            if not entry.is_dir() or not (entry / "metadata.json").is_file():
                continue
            try:
                found.append(package.Package(entry))
            except Exception:
                log.warning("ignoring unreadable language pack at %s", entry)
        return found

    def available_pairs(self) -> list[tuple[str, str]]:
        """Pairs downloadable from the index. Network — settings UI only."""
        package = _argos(self.models_dir)
        try:
            package.update_package_index()
            available = package.get_available_packages()
        except Exception as exc:
            raise TranslationError(
                "Couldn't reach the language pack list. Check your connection and try again."
            ) from exc
        pairs = {(pkg.from_code, pkg.to_code) for pkg in available if pkg.from_code and pkg.to_code}
        return sorted(pairs)

    def supports(self, source_lang: str, target_lang: str) -> bool:
        source, target = normalize_code(source_lang), normalize_code(target_lang)
        if not source or not target or source == target:
            return False
        try:
            pairs = set(self.installed_pairs())
        except TranslationError:
            return False
        if (source, target) in pairs:
            return True
        # Argos composes a route through an intermediate language (usually
        # English) when no direct pack exists, so ja->en + en->de covers ja->de.
        outbound = {to for frm, to in pairs if frm == source}
        inbound = {frm for frm, to in pairs if to == target}
        return bool(outbound & inbound)

    def prepare(self, source_lang: str, target_lang: str) -> None:
        """Install the pack for a pair if it isn't already here. May download.

        An unknown source language is not an error. The app's default is
        "Detect automatically", so at session start we genuinely do not know the
        pair yet — the real language arrives with the first recognised segment,
        and ``translate`` installs the pack then. Treating that as a failure is
        what made on-device translation appear broken on a fresh install.
        """
        source, target = normalize_code(source_lang), normalize_code(target_lang)
        if not target:
            raise TranslationError("Choose a language to show captions in.")
        if not source or source == AUTO_DETECT:
            return  # nothing to install until we know what is being spoken
        if source == target:
            raise TranslationError("Source and target languages are the same.")
        self._pairs = None  # another window may have installed a pack since
        if self.supports(source, target):
            return

        package = _argos(self.models_dir)
        try:
            package.update_package_index()
            available = package.get_available_packages()
        except Exception as exc:
            raise TranslationError(
                "Couldn't reach the language pack list. Check your connection and try again."
            ) from exc

        match = next(
            (p for p in available if p.from_code == source and p.to_code == target),
            None,
        )
        if match is None:
            raise TranslationError(
                f"{language_name(source)} to {language_name(target)} isn't available "
                "as an on-device language pack yet."
            )
        try:
            archive = match.download()
            validate_archive(archive)
            package.install_from_path(archive)
        except Exception as exc:
            raise TranslationError(
                f"The {language_name(source)} to {language_name(target)} language pack "
                "couldn't be downloaded. Check your connection and try again."
            ) from exc
        # The archive has been unpacked; keeping it doubles the disk cost of
        # every language pair for no benefit.
        _discard_archive(archive)
        self._translations.clear()
        self._pairs = None
        log.info("Installed Argos pack %s->%s", source, target)

    # ---------- translation ----------

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[tuple[str, str]] | None = None,
    ) -> TranslationResult:
        # context is ignored: these models translate one sentence in isolation.
        source, target = normalize_code(source_lang), normalize_code(target_lang)
        if not target:
            raise TranslationError("Choose a language to show captions in.")
        if not source or source == AUTO_DETECT:
            raise TranslationError("Still working out which language is being spoken.")
        # The usual path on a fresh install: the user left the source on
        # "Detect automatically", so this is the first time we have known which
        # pack to fetch. Start it, and say so instead of failing silently.
        if not self.supports(source, target):
            progress = self.ensure_pack(source, target)
            if progress:
                raise TranslationError(progress)
        try:
            output = text
            for hop_source, hop_target in self._route(source, target):
                output = self._translate_hop(output, hop_source, hop_target)
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError(
                f"The built-in {language_name(source)} to {language_name(target)} "
                "translator couldn't handle that line."
            ) from exc
        output = (output or "").strip()
        if not output:
            raise TranslationError("The built-in translator returned nothing.")
        return TranslationResult(
            text=output,
            provider_id=self.id,
            provider_label=self.label,
            tier=self.tier,
        )

    def ensure_pack(self, source: str, target: str) -> str:
        """Start installing a pair in the background; report what is happening.

        Returns "" once the pair is usable. Called with the language Whisper
        actually detected, which is the first moment we can know what to fetch
        when the user left the source on "Detect automatically".
        """
        source, target = normalize_code(source), normalize_code(target)
        if not source or not target or source in (target, AUTO_DETECT):
            return ""
        if self.supports(source, target):
            return ""

        key = (source, target)
        with _install_lock:
            if key in _failed_installs:
                if time.monotonic() - _failed_since.get(key, time.monotonic()) < 120:
                    return _failed_installs[key]
                _failed_installs.pop(key, None)
                _failed_since.pop(key, None)
            if key in _installing:
                return (
                    f"Getting the {language_name(source)} to {language_name(target)} "
                    "language pack ready…"
                )
            if len(_installing) >= 2:
                return "Getting other language packs ready. This pair will be tried shortly."
            _installing.add(key)

        def install() -> None:
            try:
                self.prepare(source, target)
            except TranslationError as exc:
                with _install_lock:
                    _failed_installs[key] = str(exc)
                    _failed_since[key] = time.monotonic()
                log.info("Could not install %s->%s: %s", source, target, exc)
            except Exception:
                with _install_lock:
                    _failed_installs[key] = (
                        f"{language_name(source)} to {language_name(target)} couldn't be set up."
                    )
                    _failed_since[key] = time.monotonic()
                log.exception("Unexpected failure installing %s->%s", source, target)
            finally:
                with _install_lock:
                    _installing.discard(key)
                    _install_threads.discard(threading.current_thread())

        worker = threading.Thread(
            target=install, name=f"subbyai-pack-{source}-{target}", daemon=True
        )
        with _install_lock:
            _install_threads.add(worker)
        worker.start()
        return f"Downloading the {language_name(source)} to {language_name(target)} language pack…"

    def _route(self, source: str, target: str) -> list[tuple[str, str]]:
        """The hops needed for this pair.

        OPUS-MT packs are one-directional pairs, so an uncommon combination is
        reached through an intermediate language — almost always English. Argos
        did this internally; doing it here keeps ``supports()`` honest.
        """
        pairs = set(self.installed_pairs())
        if (source, target) in pairs:
            return [(source, target)]
        outbound = {to for frm, to in pairs if frm == source}
        inbound = {frm for frm, to in pairs if to == target}
        pivots = outbound & inbound
        if not pivots:
            raise TranslationError(
                f"The {language_name(source)} to {language_name(target)} language pack "
                "isn't installed yet."
            )
        pivot = "en" if "en" in pivots else sorted(pivots)[0]
        return [(source, pivot), (pivot, target)]

    def _translate_hop(self, text: str, source: str, target: str) -> str:
        model, tokenizer = self._translation_for(source, target)
        tokens = tokenizer.encode(text, out_type=str)
        results = model.translate_batch(
            [tokens], beam_size=2, max_batch_size=1, max_decoding_length=512
        )
        # Target pieces absent from the source tokenizer's vocabulary may keep
        # the boundary marker even after decoding. Preserve byte fallback and
        # real underscores while converting that marker into normal spacing.
        return tokenizer.decode(results[0].hypotheses[0]).replace("\u2581", " ").strip()

    def _translation_for(self, source: str, target: str) -> tuple[Any, Any]:
        """Load (and cache) the CTranslate2 model and tokenizer for a pair."""
        cached = self._translations.get((source, target))
        if cached is not None:
            return cached

        missing = TranslationError(
            f"The {language_name(source)} to {language_name(target)} language pack "
            "isn't installed yet."
        )
        package_dir = self._package_dir(source, target)
        if package_dir is None:
            raise missing
        try:
            import ctranslate2
            import sentencepiece
        except Exception as exc:  # pragma: no cover - only if the build is broken
            raise TranslationError(
                "The built-in translator isn't installed with this copy of the app."
            ) from exc
        try:
            model = ctranslate2.Translator(
                str(package_dir / "model"), device="cpu", compute_type="int8"
            )
            tokenizer = sentencepiece.SentencePieceProcessor(
                str(package_dir / "sentencepiece.model")
            )
        except Exception as exc:
            raise TranslationError(
                f"The {language_name(source)} to {language_name(target)} language pack "
                "looks damaged. Reinstalling it in Settings should fix this."
            ) from exc
        # Automatic language changes must not keep an unlimited number of
        # translation models resident for a long viewing session.
        if len(self._translations) >= 3:
            self._translations.pop(next(iter(self._translations)))
        self._translations[(source, target)] = (model, tokenizer)
        return model, tokenizer

    def _package_dir(self, source: str, target: str) -> Path | None:
        """Where the installed pack for this pair lives, if it is installed."""
        package = _argos(self.models_dir)
        try:
            installed = package.get_installed_packages()
        except Exception:  # pragma: no cover - reported by installed_pairs()
            return None
        for pkg in installed:
            if pkg.from_code != source or pkg.to_code != target:
                continue
            path = Path(getattr(pkg, "package_path", "") or "")
            if (path / "model").is_dir() and (path / "sentencepiece.model").is_file():
                return path
        return None

    def close(self) -> None:
        self._translations.clear()
        self._pairs = None


def _discard_archive(archive: Any) -> None:
    """Delete a downloaded .argosmodel once it has been unpacked."""
    try:
        path = Path(str(archive))
        if path.is_file() and path.suffix == ".argosmodel":
            path.unlink()
            log.debug("Removed language pack archive %s", path.name)
    except OSError:
        log.debug("Could not remove the language pack archive", exc_info=True)


def validate_archive(archive: Path) -> None:
    """Reject unsafe paths, links and unreasonable unpacked sizes before installing."""
    from pathlib import PurePosixPath, PureWindowsPath

    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 10000 or sum(item.file_size for item in members) > 2 * 1024**3:
            raise ValueError("Language pack is too large")
        for item in members:
            path = PurePosixPath(item.filename.replace("\\", "/"))
            windows = PureWindowsPath(item.filename)
            if path.is_absolute() or ".." in path.parts or windows.drive or ":" in item.filename:
                raise ValueError("Unsafe language pack path")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Language packs cannot contain symbolic links")


def installed_bytes(models_dir: Path | None = None) -> int:
    """Disk used by language packs, for the storage figure in Settings.

    Counts the download cache too. It sits outside the packages directory, and
    a figure that ignored it would under-report whenever an archive outlived
    its install.
    """
    root = models_dir or paths.translation_models_dir()
    total = 0
    for directory in (root, cache_dir_for(root)):
        try:
            total += sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())
        except OSError:
            continue
    return total


def remove_all_packs(models_dir: Path | None = None) -> int:
    """Delete every installed language pack. Returns the bytes reclaimed."""
    import shutil

    root = models_dir or paths.translation_models_dir()
    freed = installed_bytes(root)
    try:
        # The cache goes too: "Delete all my data" must not leave hundreds of
        # megabytes of .argosmodel archives sitting next to the packs.
        shutil.rmtree(cache_dir_for(root), ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("Could not remove language packs", exc_info=True)
        return 0
    return freed
