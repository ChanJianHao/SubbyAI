import os
import sys
from pathlib import Path

import pytest

# Headless Qt for CI and local runs.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never reach the network during tests.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen plugin does not enumerate installed fonts. Load
    # native fonts explicitly so layout tests exercise real glyph metrics.
    if sys.platform == "win32":
        from PySide6.QtGui import QFontDatabase

        for filename in ("segoeui.ttf", "segoeuib.ttf", "YuGothR.ttc", "msyh.ttc"):
            QFontDatabase.addApplicationFont(
                str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename)
            )
    yield app


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Keep tests off the real user config/data/log directories."""
    from subbyai import paths
    from subbyai.core import secrets

    monkeypatch.setattr(secrets, "_backend", lambda: None)

    monkeypatch.setattr(paths, "config_dir", lambda: _mk(tmp_path / "config"))
    monkeypatch.setattr(paths, "settings_file", lambda: _mk(tmp_path / "config") / "settings.json")
    monkeypatch.setattr(paths, "data_dir", lambda: _mk(tmp_path / "data"))
    monkeypatch.setattr(paths, "models_dir", lambda: _mk(tmp_path / "models"))
    monkeypatch.setattr(paths, "translation_models_dir", lambda: _mk(tmp_path / "translation"))
    monkeypatch.setattr(paths, "sessions_db", lambda: _mk(tmp_path / "data") / "sessions.db")
    monkeypatch.setattr(paths, "exports_dir", lambda: _mk(tmp_path / "exports"))
    monkeypatch.setattr(paths, "log_dir", lambda: _mk(tmp_path / "logs"))
    monkeypatch.setattr(paths, "log_file", lambda: _mk(tmp_path / "logs") / "subbyai.log")
    yield tmp_path


@pytest.fixture(autouse=True)
def dispose_test_windows():
    """Release Qt trees between tests, including hidden windows and their signals.

    Closing a desktop window can hide it in the tray. Without deferred deletion,
    later theme checks restyle every window created by earlier tests.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    previous = set(app.topLevelWidgets()) if app else set()
    yield
    app = QApplication.instance()
    if app:
        for widget in set(app.topLevelWidgets()) - previous:
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def store(tmp_path):
    from subbyai.storage import SessionStore

    store = SessionStore(tmp_path / "test.db", flush_interval=0.05)
    yield store
    store.close()


def _mk(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
