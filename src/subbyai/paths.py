"""Per-user directories. All state lives in OS-standard locations."""

from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

from .branding import APP_ID

# Store configuration and user data in the standard per-user application directories.
_dirs = PlatformDirs(appname=APP_ID, appauthor=False, roaming=True)


def config_dir() -> Path:
    return _ensure(Path(_dirs.user_config_dir))


def settings_file() -> Path:
    return config_dir() / "settings.json"


def data_dir() -> Path:
    return _ensure(Path(_dirs.user_data_dir))


def models_dir() -> Path:
    return _ensure(data_dir() / "models")


def translation_models_dir() -> Path:
    return _ensure(data_dir() / "translation")


def sessions_db() -> Path:
    return data_dir() / "sessions.db"


def exports_dir() -> Path:
    return _ensure(Path(_dirs.user_documents_dir) / "SubbyAI")


def log_dir() -> Path:
    return _ensure(Path(_dirs.user_log_dir))


def log_file() -> Path:
    return log_dir() / "subbyai.log"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
