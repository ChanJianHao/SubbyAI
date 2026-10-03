"""Curated, revision-pinned speech model downloads and cache management.

Downloads are explicit, cancellable and progress-reporting. Cached models load
from a local snapshot without contacting the model host. User-selected external
folders are never deleted by the model manager."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..core.settings import TIER_MODELS
from .catalogue import all_specs, spec_or_guess

log = logging.getLogger(__name__)

PHASE_CHECKING = "checking"
PHASE_DOWNLOADING = "downloading"
PHASE_DONE = "done"
PHASE_CANCELLED = "cancelled"
PHASE_ERROR = "error"

# Reserve temporary download and filesystem overhead above the model size.
DOWNLOAD_HEADROOM = 1.25


# Emit at most one progress callback per megabyte; the hub reports every chunk.
_PROGRESS_STEP_BYTES = 1 << 20


class _Cancelled(Exception):
    """Internal signal raised out of the progress shim to abort a download."""


class ModelInUse(RuntimeError):
    """A model's files could not be removed. Carries a sentence for the user."""


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    downloaded_bytes: int = 0
    total_bytes: int = 0
    phase: str = PHASE_CHECKING
    message: str = ""
    """User-facing text, set on the ERROR phase."""

    @property
    def fraction(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self.downloaded_bytes / self.total_bytes)


def known_models() -> list[str]:
    """Every model name the app can name a repository and a size for."""
    return sorted({s.id for s in all_specs()} | set(_tier_model_names()))


def canonical_models() -> list[str]:
    """Known models, tier models first.

    Preference order, not just a list: some names are aliases for one
    repository, and whichever comes first is the one the app calls it.
    """
    tiers = _tier_model_names()
    return tiers + [name for name in known_models() if name not in tiers]


def _tier_model_names() -> list[str]:
    return [model_id for model_id, _, _ in TIER_MODELS.values()]


def repo_id_for(model_name: str) -> str:
    """Hugging Face repository holding a CTranslate2 build of this model."""
    name = model_name.strip()
    if "/" in name or "\\" in name:
        return name  # already a repo id or a local directory
    return spec_or_guess(name).repo


def revision_for(model_name: str) -> str:
    """The pinned commit to download, or "main" for something unrecognised."""
    return spec_or_guess(model_name).revision


def estimated_size_mb(model_name: str) -> int:
    """Download size. Used for disk checks and for telling the user."""
    return spec_or_guess(model_name).size_mb


def required_free_mb(model_name: str) -> float:
    """Free space a download of this model needs, including transient copies."""
    return estimated_size_mb(model_name) * DOWNLOAD_HEADROOM


class ModelManager:
    """Finds, sizes, fetches and removes model files under the app's cache.

    Engine-agnostic: what to fetch, where from and how to recognise usable
    weights all come from the model's catalogue entry, because an ONNX model
    has no ``model.bin`` and would otherwise look permanently missing.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        # disk_bytes walks a whole model directory, and the catalogue asks for
        # every model each time it refreshes. Sizes only change when we
        # download or delete, both of which clear this.
        self._size_cache: dict[str, int] = {}

    @property
    def cache_dir(self) -> Path:
        # Resolved per access so tests (and a relocated data dir) are honoured.
        return self._cache_dir if self._cache_dir is not None else paths.models_dir()

    # ---------- inspection ----------

    def is_downloaded(self, model_name: str) -> bool:
        return self.local_path(model_name) is not None

    def local_path(self, model_name: str) -> Path | None:
        """Directory holding usable weights, or None if the model is not cached.

        A snapshot counts only when the weights are really there: an interrupted
        download can leave the directory and metadata behind, which would
        happily "load" that and fail deep inside CTranslate2.
        """
        name = model_name.strip()
        globs = spec_or_guess(name).weights_globs
        direct = Path(name)
        if direct.is_dir():
            return direct if _has_weights(direct, globs) else None

        repo = repo_id_for(name)
        root = self._safe_repo_root(repo)
        snapshots = root / "snapshots"
        if not snapshots.is_dir():
            return None
        candidates = _snapshot_candidates(root, snapshots)
        pinned = snapshots / revision_for(name)
        if pinned in candidates:
            candidates.remove(pinned)
            candidates.insert(0, pinned)
        for candidate in candidates:
            if _has_weights(candidate, globs):
                return candidate
        return None

    def estimated_size_mb(self, model_name: str) -> int:
        return estimated_size_mb(model_name)

    def disk_bytes(self, model_name: str) -> int:
        """What this model really occupies, blobs and snapshot together.

        Measured rather than estimated. Older hub versions kept a second copy
        under blobs/ on filesystems without symlinks; current ones do not. A
        screen offering to reclaim space has to quote what is actually there
        either way.
        """
        cached = self._size_cache.get(model_name)
        if cached is not None:
            return cached
        root = self._repo_root(model_name)
        if root is None or not root.is_dir():
            return 0
        try:
            total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        except OSError:
            log.debug("Could not size %s", root, exc_info=True)
            return 0
        self._size_cache[model_name] = total
        return total

    def forget_sizes(self) -> None:
        """Drop cached disk sizes after anything that changes them."""
        self._size_cache.clear()

    def installed(self) -> list[str]:
        """Every model with usable weights on disk, by model name.

        Directories that cannot be mapped back to a known name are reported by
        their repo id, so a model fetched through the override field still shows
        up as something the user can select and delete.
        """
        try:
            entries = [p for p in self.cache_dir.iterdir() if p.name.startswith("models--")]
        except OSError:
            return []
        # Several names can share a repository ("turbo" is an alias for
        # "large-v3-turbo"). Tier models are registered first and never
        # overwritten, so the catalogue shows the name the rest of the app uses.
        by_repo: dict[str, str] = {}
        for name in canonical_models():
            by_repo.setdefault(repo_id_for(name), name)
        found = []
        for entry in entries:
            repo = entry.name.removeprefix("models--").replace("--", "/")
            name = by_repo.get(repo, repo)
            if self.is_downloaded(name):
                found.append(name)
        return sorted(found)

    def _repo_root(self, model_name: str) -> Path | None:
        name = model_name.strip()
        if Path(name).is_dir():
            return None  # a directory the user pointed at; not ours to manage
        repo = repo_id_for(name)
        return self._safe_repo_root(repo)

    def _safe_repo_root(self, repo: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError(
                "Use a model name, a trusted owner/model repository, or a local model folder."
            )
        root = self.cache_dir / f"models--{repo.replace('/', '--')}"
        if root.resolve().parent != self.cache_dir.resolve():
            raise ValueError("The model cache entry points outside SubbyAI's cache.")
        return root

    # ---------- removing ----------

    def delete(self, model_name: str) -> int:
        """Remove a downloaded model. Returns the bytes reclaimed, 0 if none.

        Raises ``ModelInUse`` when the files are locked, which on Windows is
        exactly what happens if the engine still holds the model open — a
        silent partial delete would leave weights that fail to load later.
        """
        root = self._repo_root(model_name)
        if root is None or not root.is_dir():
            return 0
        freed = self.disk_bytes(model_name)
        self.forget_sizes()
        try:
            shutil.rmtree(root)
        except PermissionError as exc:
            raise ModelInUse(
                "That model is in use right now. Stop captions and try again."
            ) from exc
        except OSError as exc:
            log.warning("Could not remove %s", root, exc_info=True)
            raise ModelInUse("Couldn't remove that model's files.") from exc
        return freed

    def space_error(self, model_name: str) -> str:
        """Empty when there is room to download, otherwise a sentence to show."""
        needed_mb = required_free_mb(model_name)
        try:
            free_mb = shutil.disk_usage(self.cache_dir).free / 1024 / 1024
        except OSError:
            log.debug("Could not measure free space", exc_info=True)
            return ""
        if free_mb >= needed_mb:
            return ""
        return (
            f"Not enough free space. This download needs about "
            f"{needed_mb / 1024:.1f} GB and there's {free_mb / 1024:.1f} GB left."
        )

    # ---------- fetching ----------

    def download(
        self,
        model_name: str,
        progress: Callable[[DownloadProgress], None],
        cancel: threading.Event,
    ) -> bool:
        """Fetch a model, reporting bytes as they land. True when it is ready.

        Resumable: partial blobs stay in the hub cache, so a cancelled or failed
        attempt costs only what has not been fetched yet.
        """
        self.forget_sizes()
        if self.is_downloaded(model_name):
            total = estimated_size_mb(model_name) * 1024 * 1024
            progress(DownloadProgress(total, total, PHASE_DONE))
            return True

        space_error = self.space_error(model_name)
        if space_error:
            progress(DownloadProgress(0, 0, PHASE_ERROR, space_error))
            return False
        if cancel.is_set():
            progress(DownloadProgress(0, 0, PHASE_CANCELLED))
            return False

        estimate = estimated_size_mb(model_name) * 1024 * 1024
        progress(DownloadProgress(0, estimate, PHASE_CHECKING))
        tracker = _ProgressTracker(progress, cancel, estimate)
        cache_dir = self.cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            from huggingface_hub import snapshot_download

            wanted = spec_or_guess(model_name)
            snapshot = Path(
                snapshot_download(
                    wanted.repo,
                    # A pinned snapshot prevents upstream updates changing a download.
                    revision=wanted.revision,
                    cache_dir=str(cache_dir),
                    allow_patterns=list(wanted.allow_patterns),
                    tqdm_class=tracker.tqdm_class(),
                    max_workers=4,
                    token=False,  # public models never need a developer's hub credentials
                )
            )
        except Exception as exc:
            if cancel.is_set():
                log.info("Download of %s cancelled", model_name)
                progress(tracker.snapshot(PHASE_CANCELLED))
                return False
            log.warning("Download of %s failed: %s", model_name, exc, exc_info=True)
            progress(tracker.snapshot(PHASE_ERROR, _friendly_download_error(exc)))
            return False

        if cancel.is_set():
            progress(tracker.snapshot(PHASE_CANCELLED))
            return False
        # Confirm rather than assume: "downloaded" must mean the same thing to
        # this method and to ``is_downloaded``, or Start freezes on a fetch the
        # user was told had already happened.
        if not _has_weights(snapshot, wanted.weights_globs):
            progress(
                tracker.snapshot(
                    PHASE_ERROR, "The download finished but the model files are missing."
                )
            )
            return False
        if not _verify_blob_hashes(snapshot):
            progress(
                tracker.snapshot(
                    PHASE_ERROR,
                    "A downloaded file failed its integrity check. Remove this model and retry.",
                )
            )
            return False
        progress(tracker.snapshot(PHASE_DONE, complete=True))
        log.info("Model %s ready in %s", model_name, snapshot)
        return True


class _ProgressTracker:
    """Turns the hub's tqdm bars into one byte count, and carries the cancel.

    Two details drive the shape. The hub hands the same class to several bars on
    several worker threads, so the state lives here rather than on an instance.
    And it counts every byte twice — once received from the network, once
    written to disk, on two separate bars — so the furthest-along bar is the
    honest number and a sum would report double.

    The denominator is our own size estimate: the hub's totals start at zero and
    are inflated mid-flight by its transfer-rate heuristics, which makes for a
    bar that jumps backwards.
    """

    def __init__(
        self,
        report: Callable[[DownloadProgress], None],
        cancel: threading.Event,
        estimate: int,
    ) -> None:
        self._report = report
        self._cancel = cancel
        self._estimate = estimate
        self._lock = threading.Lock()
        self._per_bar: dict[int, int] = {}
        self._bar_seq = 0
        self._last_emit = -1
        self.downloaded = 0

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def next_bar_id(self) -> int:
        """A stable key per bar; id() would be recycled once a bar is collected."""
        with self._lock:
            self._bar_seq += 1
            return self._bar_seq

    def snapshot(self, phase: str, message: str = "", complete: bool = False) -> DownloadProgress:
        with self._lock:
            total = max(self._estimate, self.downloaded)
            done = total if complete else self.downloaded
            return DownloadProgress(done, total, phase, message)

    def note(self, bar_id: int, n: int) -> None:
        with self._lock:
            self._per_bar[bar_id] = self._per_bar.get(bar_id, 0) + n
            downloaded = max(self._per_bar.values())
            if downloaded <= self.downloaded:
                return
            self.downloaded = downloaded
            if self._last_emit >= 0 and downloaded - self._last_emit < _PROGRESS_STEP_BYTES:
                return
            self._last_emit = downloaded
            event = DownloadProgress(downloaded, max(self._estimate, downloaded), PHASE_DOWNLOADING)
        self._report(event)

    def tqdm_class(self) -> type:
        from tqdm.auto import tqdm as _tqdm

        tracker = self

        class TrackingTqdm(_tqdm):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                # A disabled tqdm skips most of __init__, self.unit included, so
                # the unit has to be read off the arguments here.
                self._counts_bytes = kwargs.get("unit") == "B"
                self._bar_id = tracker.next_bar_id()
                kwargs["disable"] = True  # a GUI app has no terminal to draw in
                super().__init__(*args, **kwargs)

            def update(self, n=1):
                if tracker.cancelled:
                    raise _Cancelled
                if self._counts_bytes:
                    tracker.note(self._bar_id, int(n or 0))
                return super().update(n)

        return TrackingTqdm


def _snapshot_candidates(root: Path, snapshots: Path) -> list[Path]:
    """Snapshot dirs, the revision refs/main points at first."""
    ordered: list[Path] = []
    ref = root / "refs" / "main"
    try:
        if ref.is_file():
            revision = ref.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", revision):
                raise OSError("Invalid snapshot reference")
            pinned = snapshots / revision
            if pinned.is_dir():
                ordered.append(pinned)
    except OSError:
        log.debug("Could not read %s", ref, exc_info=True)
    try:
        rest = [p for p in snapshots.iterdir() if p.is_dir() and p not in ordered]
    except OSError:
        return ordered
    rest.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return ordered + rest


def _has_weights(directory: Path, globs: tuple[str, ...] = ("model.bin", "*.bin")) -> bool:
    """Whether a snapshot really holds runnable weights.

    The globs come from the model's spec because not every engine uses a
    ``model.bin``: an ONNX model would otherwise look permanently absent, and
    the app would re-download it on every single launch.
    """
    return any(
        any(file.is_file() and file.stat().st_size > 0 for file in directory.glob(pattern))
        for pattern in globs
    )


def _verify_blob_hashes(snapshot: Path) -> bool:
    """Hub symlinks name LFS blobs by SHA256. Verify these without an extra network call.

    Copies on Windows do not carry that digest in their name; those retain the
    upstream hub's transport guarantees. This is integrity, not a model signature.
    """
    for path in snapshot.iterdir():
        if not path.is_file():
            continue
        expected = path.resolve().name
        if re.fullmatch(r"[0-9a-f]{64}", expected):
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                return False
    return True


def _friendly_download_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, PermissionError) or "permission" in text or "access is denied" in text:
        return "Couldn't save the download. Check that the app can write to its data folder."
    if isinstance(exc, OSError) and "space" in text:
        return "Ran out of disk space partway through the download."
    if any(
        word in text
        for word in ("connect", "network", "timeout", "timed out", "resolve", "offline", "dns")
    ):
        return "Couldn't reach the download server. Check your internet connection and try again."
    return "The download didn't finish. Try again in a moment."
