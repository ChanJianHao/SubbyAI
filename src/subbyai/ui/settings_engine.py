"""Advanced engine and remote processing controls, kept in separate categories."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..core.network import validate_endpoint
from ..core.secrets import endpoint_key_id
from .settings_widgets import Group, SettingsSection, hint_label
from .widgets import compact_combo


class EngineSection(SettingsSection):
    restart_capture_requested = Signal()

    def __init__(self, store, parent=None):
        super().__init__(store, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.controls = {}
        inference = Group(
            "Recognition",
            "Model selection and downloads are in Models & providers. "
            "Device, precision and beam size apply to Whisper. Parakeet uses CPU/int8.",
        )
        for name, label, options in (
            ("compute_device", "Device", ("auto", "cpu", "gpu")),
            ("compute_type", "Precision", ("auto", "int8", "float16", "float32", "int8_float16")),
        ):
            control = compact_combo(QComboBox())
            control.addItems(options)
            control.setCurrentText(getattr(self.settings.captions, name))
            control.currentTextChanged.connect(lambda value, name=name: self._changed(name, value))
            self.controls[name] = control
            inference.add_row(label, control)
        for name, label, lo, hi, step, decimals in (
            ("beam_size", "Beam size", 1, 10, 1, 0),
            ("cpu_threads", "CPU threads (0 = automatic)", 0, 16, 1, 0),
            ("chunk_seconds", "Maximum phrase length (seconds)", 2, 15, 0.5, 1),
            ("silence_seconds", "Pause before a phrase ends (seconds)", 0.2, 2, 0.05, 2),
        ):
            control = QDoubleSpinBox() if decimals else QSpinBox()
            control.setRange(lo, hi)
            control.setSingleStep(step)
            if decimals:
                control.setDecimals(decimals)
            control.setValue(getattr(self.settings.captions, name))
            control.valueChanged.connect(lambda value, name=name: self._changed(name, value))
            self.controls[name] = control
            inference.add_row(label, control)
        root.addWidget(inference)
        root.addWidget(
            hint_label(
                "Changes reconnect a running session. A slower engine drops old phrases "
                "to keep captions live. Hiding Advanced Mode preserves every value."
            )
        )

    def _changed(self, name, value):
        setattr(self.settings.captions, name, value)
        self.settings.captions.performance_profile = "custom"
        self.apply("captions")
        self.restart_capture_requested.emit()

    def refresh(self):
        for name, control in self.controls.items():
            control.blockSignals(True)
            value = getattr(self.settings.captions, name)
            if isinstance(control, QComboBox):
                control.setCurrentText(value)
            else:
                control.setValue(value)
            control.blockSignals(False)


class RemoteSection(SettingsSection):
    restart_capture_requested = Signal()

    def __init__(self, store, deps=None, parent=None):
        super().__init__(store, parent)
        self._deps = deps
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        group = Group(
            "Where speech becomes subtitles",
            "Use a personal server or a compatible transcription API. "
            "Local processing is the default.",
        )
        self.backend = compact_combo(QComboBox())
        self.backend.addItem("On this computer", "local")
        self.backend.addItem("Remote speech server", "remote")
        group.add_row("Processing", self.backend)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://speech.example.com/v1")
        group.add_row("Server address", self.url)
        self.model = QLineEdit()
        group.add_row("Server model", self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Leave blank to keep this server's stored key")
        group.add_row("API key", self.key, "Stored in the operating system's credential store.")
        self.timeout = QSpinBox()
        self.timeout.setRange(2, 60)
        group.add_row("Timeout (seconds per network operation)", self.timeout)
        self.retries = QSpinBox()
        self.retries.setRange(0, 2)
        group.add_row("Retries for busy servers", self.retries)
        self.consent = QCheckBox("Allow audio to this server")
        self.consent.setToolTip("Captured audio is sent only to the address shown above.")
        group.add(self.consent)
        self.notice = hint_label(
            "Internet endpoints require HTTPS. LAN HTTP sends audio without encryption; "
            "prefer HTTPS on shared networks. The server controls its own retention."
        )
        group.add(self.notice)
        save = QPushButton("Save processing choice")
        save.setObjectName("primary")
        save.clicked.connect(self.save_choice)
        group.add(save)
        self.status = hint_label("")
        group.add(self.status)
        root.addWidget(group)
        self.url.textEdited.connect(lambda _text: self.consent.setChecked(False))
        self.refresh()

    def refresh(self):
        config = self.settings.processing
        self.backend.setCurrentIndex(max(0, self.backend.findData(config.asr_backend)))
        self.url.setText(config.base_url)
        self.model.setText(config.model)
        self.timeout.setValue(int(config.timeout_seconds))
        self.retries.setValue(config.retries)
        self.consent.setChecked(bool(config.base_url) and config.consent_url == config.base_url)

    def save_choice(self):
        remote = self.backend.currentData() == "remote"
        try:
            url = validate_endpoint(self.url.text()) if remote else self.url.text().strip()
            if remote and not self.consent.isChecked():
                raise ValueError("Allow audio to this address before choosing remote processing.")
            if remote and not self.model.text().strip():
                raise ValueError("Choose the model your speech server offers.")
            if self.key.text():
                setter = getattr(self._deps, "api_key_set", None)
                if setter is None:
                    raise ValueError("The credential store is unavailable; the key was not saved.")
                setter(endpoint_key_id("remote-asr", url), self.key.text())
                self.key.clear()
        except Exception as exc:
            self.status.setText(str(exc))
            return
        config = self.settings.processing
        config.asr_backend = "remote" if remote else "local"
        config.base_url = url
        config.model = self.model.text().strip()
        config.consent_url = url if remote else ""
        config.timeout_seconds = float(self.timeout.value())
        config.retries = self.retries.value()
        self.apply("processing")
        self.status.setText(
            "Saved. Audio goes to your selected server."
            if remote
            else "Saved. Speech recognition runs on this computer."
        )
        self.restart_capture_requested.emit()
