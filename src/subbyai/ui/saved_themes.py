"""Save and reuse subtitle looks in the normal appearance editor."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QMessageBox, QPushButton

from ..core.overlay_themes import apply_theme, save_theme
from .settings_widgets import Group, hint_label
from .widgets import compact_combo


class SavedThemes(Group):
    style_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__("My subtitle looks", parent=parent)
        self._store = store
        self.choice = compact_combo(QComboBox())
        self.choice.activated.connect(self._apply)
        self.add_row("Saved looks", self.choice)
        row = QHBoxLayout()
        self.save_button = QPushButton("Save this look…")
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        self.delete_button = QPushButton("Remove saved look")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        self._body.addLayout(row)
        self.note = hint_label(
            "Choose a saved look to apply it immediately. Display positions stay separate."
        )
        self.add(self.note)
        self.refresh()

    def refresh(self, selected=None):
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItem("Choose a saved look…", None)
        for name in self._store.settings.overlay.saved_themes:
            self.choice.addItem(name, name)
        self.choice.setCurrentIndex(max(0, self.choice.findData(selected)))
        self.choice.blockSignals(False)
        self.delete_button.setEnabled(selected is not None)

    def _apply(self, index):
        name = self.choice.itemData(index)
        if name is None:
            self.delete_button.setEnabled(False)
            return
        apply_theme(self._store.settings.overlay, name)
        self._store.notify("overlay")
        self.style_changed.emit()
        self.refresh(name)
        self.note.setText("Applied " + name + ". Your changes appear in the preview straight away.")

    def _save(self):
        name, accepted = QInputDialog.getText(self, "Save subtitle look", "Give your look a name:")
        if not accepted:
            return
        overlay = self._store.settings.overlay
        if (
            name.strip() in overlay.saved_themes
            and QMessageBox.question(
                self, "Replace saved look?", "Replace the saved appearance for this name?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            save_theme(overlay, name)
        except ValueError as exc:
            self.note.setText(str(exc))
            return
        self._store.notify("overlay")
        self.refresh(name.strip())
        self.note.setText("Saved " + name.strip() + ".")

    def _delete(self):
        name = self.choice.currentData()
        if name is None:
            return
        del self._store.settings.overlay.saved_themes[name]
        self._store.notify("overlay")
        self.refresh()
        self.note.setText("Removed the saved look. The current subtitle appearance is kept.")
