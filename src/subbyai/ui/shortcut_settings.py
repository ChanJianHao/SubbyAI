"""One registration/persistence path for startup, live edits and capture recovery."""

from PySide6.QtCore import QObject, Signal

from ..core.settings import SettingsStore
from ..core.shortcuts import bindings_with_defaults
from ..system.hotkeys import HotkeyManager


class ShortcutSettingsController(QObject):
    changed = Signal()

    def __init__(self, store: SettingsStore, manager: HotkeyManager, parent=None):
        super().__init__(parent)
        self.store = store
        self.manager = manager
        self.failures: dict[str, str] = {}
        self.unavailable: dict[str, str] = {}
        self.bindings = bindings_with_defaults(store.settings.shortcuts.globals)
        self.suspended = False
        self._saving = False
        self._pending: dict[str, str] | None = None
        store.subscribe(self._settings_changed)

    def reload(self) -> dict[str, str]:
        """Load saved intent; unavailable bindings stay visibly unavailable."""
        self._pending = None
        self.bindings = bindings_with_defaults(self.store.settings.shortcuts.globals)
        if not self.suspended:
            self.unavailable = self.manager.register_all(self.bindings)
            self.failures = dict(self.unavailable)
        self.changed.emit()
        return dict(self.failures)

    def apply(self, bindings: dict[str, str]) -> None:
        """Commit accepted edits; retain explanations for unchanged unavailable keys."""
        bindings = bindings_with_defaults(bindings)
        previous = dict(self.bindings)
        self.suspended = False  # Capture commits on key release, before registration resumes.
        failures = self.manager.register_all(bindings)
        rejected = {
            action: reason
            for action, reason in failures.items()
            if action not in self.unavailable or bindings.get(action) != previous.get(action)
        }
        if rejected:
            self._pending = dict(bindings)
            self.unavailable = self.manager.register_all(previous)
            self.failures = {
                action: f"Change wasn't saved. {reason} Choose different keys."
                for action, reason in rejected.items()
            }
            self.failures.update(self.unavailable)
        else:
            self._pending = None
            self.bindings = dict(bindings)
            self.unavailable = dict(failures)
            self.failures = dict(failures)
            self.store.settings.shortcuts.globals = dict(bindings)
            self._saving = True
            try:
                self.store.notify("shortcuts")
            finally:
                self._saving = False
        self.changed.emit()

    def retry(self) -> None:
        if self._pending is not None:
            self.apply(self._pending)
        else:
            self.reload()

    def set_suspended(self, suspended: bool) -> None:
        if suspended == self.suspended:
            return
        self.suspended = suspended
        if suspended:
            self.manager.unregister_all()
        else:
            self.reload()

    def _settings_changed(self, section: str) -> None:
        if section == "shortcuts" and not self._saving:
            self.reload()
