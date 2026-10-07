"""Settings: domain-split models behind one store.

Design notes:
- Each area owns its own dataclass, so adding providers or shortcuts never
  grows a god-object.
- Loading coerces and clamps every field. A hand-edited or downgraded file can
  produce odd values but never a crash at paint time, and a corrupt file is
  backed up rather than silently discarded.
- The store emits change notifications per section; nothing reaches into
  another object's ``__dict__``.
- The pipeline never reads a live settings object — it takes an immutable
  snapshot at start (see ``SessionConfig``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

from .. import paths

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4


class QualityTier(StrEnum):
    """User-facing quality levels. Model ids stay out of the UI."""

    QUICK = "quick"
    BALANCED = "balanced"
    DETAILED = "detailed"
    MAXIMUM = "maximum"


# Quality tier -> (model ID, approximate weight download in MB, description).
TIER_MODELS: dict[QualityTier, tuple[str, int, str]] = {
    QualityTier.QUICK: ("base", 148, "Fastest. Fine for clear speech."),
    QualityTier.BALANCED: ("small", 487, "Great accuracy for most things."),
    QualityTier.DETAILED: ("large-v3-turbo", 818, "Better with accents and noise."),
    QualityTier.MAXIMUM: ("large-v3", 3090, "Best accuracy. Needs a graphics card."),
}


class OverlayPreset(StrEnum):
    """Caption looks. Values are persisted, so they never change.

    Display names live in ``ui.widgets.PRESET_LABELS`` precisely so the wording
    can be reconsidered without migrating anyone's settings file: ``glass`` is
    shown as "Clean" and ``solid`` as "Gaming".
    """

    MINIMAL = "minimal"
    GLASS = "glass"
    SOLID = "solid"
    HIGH_CONTRAST = "high_contrast"
    CINEMA = "cinema"
    LARGE_TEXT = "large_text"
    ANIME = "anime"
    CUTE = "cute"
    CUSTOM = "custom"


class OriginalPosition(StrEnum):
    ABOVE = "above"
    BELOW = "below"
    HIDDEN = "hidden"


@dataclass
class CaptionSettings:
    source_language: str = ""  # "" = detect automatically
    target_language: str = ""  # "" = no translation (transcribe only)
    show_original: bool = True
    quality: QualityTier = QualityTier.BALANCED
    compute_device: str = "auto"  # auto | gpu | cpu
    model_override: str = ""  # advanced escape hatch; empty = use tier
    filter_hallucinations: bool = True
    speech_sensitivity: str = "standard"  # low | standard | high
    # Names/jargon the recognizer should lean toward, comma or newline
    # separated. Biases the Whisper decoder prompt; Parakeet ignores it.
    vocabulary: str = ""
    performance_profile: str = "balanced"  # fast | balanced | accurate | custom
    beam_size: int = 1
    chunk_seconds: float = 6.0
    silence_seconds: float = 0.5
    compute_type: str = "auto"
    cpu_threads: int = 0  # 0 = choose safely
    keep_warm_minutes: int = 2  # 0 = release after stop; bounded idle RAM/VRAM


@dataclass
class ProcessingSettings:
    """Remote audio processing is opt-in and consent is bound to the endpoint."""

    asr_backend: str = "local"  # local | remote
    base_url: str = ""
    model: str = "whisper-1"
    consent_url: str = ""
    timeout_seconds: float = 15.0
    retries: int = 1


@dataclass
class OverlaySettings:
    preset: OverlayPreset = OverlayPreset.GLASS
    font_size: int = 26
    font_weight: int = 600
    max_pairs: int = 2
    original_position: OriginalPosition = OriginalPosition.ABOVE
    dim_original: bool = True
    opacity: float = 1.0
    click_through: bool = False
    auto_hide: bool = True
    auto_hide_seconds: float = 4.0
    visible: bool = True
    # Custom preset overrides
    custom_bg_color: str = "#12141880"
    custom_text_color: str = "#FFFFFF"
    custom_translation_color: str = "#FFF4DD"
    custom_border_color: str = "#FFFFFF"
    custom_border_width: float = 0.0
    custom_radius: int = 14
    custom_outline: bool = False
    custom_shadow: bool = True
    # Typography overrides. Empty/zero means "whatever the preset says", so a
    # user who never opens these keeps getting the preset's own judgement.
    font_family: str = ""
    line_spacing: float = 0.0
    align: str = ""  # "" | left | center | right
    padding: str = ""  # "" | tight | normal | roomy
    animate: bool = True
    # Geometry, keyed by a screen signature so multi-monitor setups behave.
    geometry: dict[str, list[int]] = field(default_factory=dict)
    screen_name: str = ""
    outline_width: float = 1.5
    max_lines: int = 4  # per language; long phrases end with an ellipsis
    always_on_top: bool = True
    saved_themes: dict[str, dict] = field(default_factory=dict)


@dataclass
class AudioSettings:
    device_id: str | None = None  # None = system default output
    device_name: str = ""  # remembered for reconnect + display
    source: str = "system"  # system | microphone; never fall back between kinds


@dataclass
class ProviderSettings:
    """One translation/AI endpoint. The API key lives in the OS keychain."""

    id: str = ""
    kind: str = "openai"  # builtin | ollama | lmstudio | openai
    label: str = ""
    base_url: str = ""
    model: str = ""
    enabled: bool = False
    consent_url: str = ""  # Cloud translation consent applies to this exact endpoint.


@dataclass
class IntelligenceSettings:
    translation_order: list[str] = field(default_factory=lambda: ["builtin"])
    providers: list[ProviderSettings] = field(default_factory=list)
    on_translator_unavailable: str = "show_original"  # show_original | pause
    assistant_enabled: bool = False
    assistant_provider: str = ""


@dataclass
class HistorySettings:
    enabled: bool = False
    retention_days: int = 90  # 0 = forever, -1 = session only
    save_original: bool = True
    save_translation: bool = True
    clear_live_on_stop: bool = False


@dataclass
class ShortcutSettings:
    """Action id -> key sequence. Empty string means unbound."""

    globals: dict[str, str] = field(
        default_factory=lambda: {
            "toggle_captions": "Ctrl+Alt+C",
            "toggle_overlay": "Ctrl+Alt+O",
            "toggle_click_through": "Ctrl+Alt+T",
            "toggle_translation": "Ctrl+Alt+L",
            "caption_bigger": "Ctrl+Alt+Up",
            "caption_smaller": "Ctrl+Alt+Down",
            "scrub_back": "Ctrl+Alt+Left",
            "scrub_forward": "Ctrl+Alt+Right",
        }
    )


@dataclass
class GeneralSettings:
    theme: str = "system"  # system | light | dark
    reduce_motion: bool = False
    close_to_tray: bool = True
    start_with_os: bool = False
    autostart_captions: bool = False
    onboarding_complete: bool = False
    window_geometry: list[int] = field(default_factory=list)
    usage_intents: list[str] = field(default_factory=list)  # video | games | calls | everything
    accessibility_mode: bool = False
    advanced_mode: bool = False
    accent: str = "sakura"  # sakura | lavender | ocean
    text_scale: float = 1.0


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    captions: CaptionSettings = field(default_factory=CaptionSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    intelligence: IntelligenceSettings = field(default_factory=IntelligenceSettings)
    history: HistorySettings = field(default_factory=HistorySettings)
    shortcuts: ShortcutSettings = field(default_factory=ShortcutSettings)
    general: GeneralSettings = field(default_factory=GeneralSettings)
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)

    @property
    def model_name(self) -> str:
        """The engine model actually selected."""
        if self.captions.model_override:
            return self.captions.model_override
        chosen = TIER_MODELS[self.captions.quality][0]
        if self.captions.source_language == "en":
            # English-only builds of the two smallest models are measurably
            # better at English for the same download, so someone who has said
            # the audio is English should simply get them. Only applied when
            # they said so: on auto-detect this would answer the question it
            # was asked. See asr.catalogue.english_variant.
            from ..asr.catalogue import english_variant

            return english_variant(chosen)
        return chosen

    @property
    def translation_enabled(self) -> bool:
        target = self.captions.target_language
        return bool(target) and target != self.captions.source_language


# Clamps applied on load, so a bad file degrades instead of breaking.
_CLAMPS: dict[str, tuple[float, float]] = {
    "overlay.font_size": (10, 72),
    "overlay.font_weight": (300, 900),
    "overlay.max_pairs": (1, 4),
    "overlay.opacity": (0.2, 1.0),
    "overlay.auto_hide_seconds": (1.0, 30.0),
    "overlay.custom_radius": (0, 24),
    "overlay.custom_border_width": (0.0, 5.0),
    "history.retention_days": (-1, 3650),
    "captions.beam_size": (1, 10),
    "captions.chunk_seconds": (2.0, 15.0),
    "captions.silence_seconds": (0.2, 2.0),
    "captions.cpu_threads": (0, 16),
    "captions.keep_warm_minutes": (0, 30),
    "general.text_scale": (1.0, 1.5),
    "overlay.line_spacing": (0.0, 2.5),
    "overlay.outline_width": (0.5, 5.0),
    "overlay.max_lines": (1, 8),
    "processing.timeout_seconds": (2.0, 60.0),
    "processing.retries": (0, 2),
}


class SettingsStore:
    """Owns load/validate/save and notifies listeners per section."""

    def __init__(self, path: Path | None = None):
        self._path = path or paths.settings_file()
        self._listeners: list[Callable[[str], None]] = []
        self._error_listeners: list[Callable[[str], None]] = []
        self.last_save_error = ""
        self.settings = self._load()

    # ---------- access ----------

    @property
    def path(self) -> Path:
        return self._path

    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def notify(self, section: str) -> None:
        """Announce that a section changed, then persist."""
        self.try_save()
        for listener in list(self._listeners):
            try:
                listener(section)
            except Exception:  # a bad listener must not break saving
                log.exception("Settings listener failed for section %s", section)

    def subscribe_errors(self, listener: Callable[[str], None]) -> None:
        self._error_listeners.append(listener)

    def try_save(self) -> bool:
        """Keep the running configuration usable when its disk is unavailable."""
        try:
            self.save()
        except OSError:
            self.last_save_error = (
                "Your changes work for this session, but couldn't be saved. "
                "Check free disk space and access to your settings folder."
            )
            for listener in list(self._error_listeners):
                try:
                    listener(self.last_save_error)
                except Exception:
                    log.exception("Settings error listener failed")
            return False
        self.last_save_error = ""
        return True

    # ---------- persistence ----------

    def _load(self) -> Settings:
        if not self._path.exists():
            settings = Settings()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write(settings)
            return settings
        try:
            if self._path.stat().st_size > 1024 * 1024:
                raise ValueError("settings file exceeds 1 MiB")
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("settings root is not an object")
        except (OSError, ValueError) as exc:
            backup = self._path.with_suffix(".corrupt.json")
            log.warning("Settings unreadable (%s); keeping a copy at %s", exc, backup)
            with contextlib.suppress(OSError):
                shutil.copy2(self._path, backup)
            return Settings()
        version = raw.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool) or version > SCHEMA_VERSION:
            raise ValueError("These settings were saved by a newer SubbyAI. Upgrade to open them.")
        if version < SCHEMA_VERSION:
            backup = self._path.with_suffix(f".v{version}.bak.json")
            if not backup.exists():
                shutil.copy2(self._path, backup)
            # Privacy choices are explicit. Missing history fields stay opt-in.
        return from_dict(Settings, raw)

    def save(self) -> None:
        self._write(self.settings)

    def _write(self, settings: Settings) -> None:
        settings.schema_version = SCHEMA_VERSION
        data = to_dict(settings)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            log.exception("Could not save settings")
            raise


# ---------- dataclass <-> dict with coercion ----------


def to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    return obj


def from_dict[T](cls: type[T], raw: dict[str, Any], _prefix: str = "") -> T:
    """Build a dataclass from untrusted data, field by field.

    Unknown keys are ignored, wrong types are coerced when sensible and
    otherwise replaced by the default, and numeric ranges are clamped.
    """
    instance = cls()
    if not isinstance(raw, dict):
        return instance
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in raw:
            continue
        value = raw[f.name]
        current = getattr(instance, f.name)
        path = f"{_prefix}{f.name}"
        try:
            if is_dataclass(current) and not isinstance(current, type):
                setattr(instance, f.name, from_dict(type(current), value, f"{path}."))
            elif isinstance(current, Enum):
                setattr(instance, f.name, type(current)(value))
            elif isinstance(current, bool):
                if isinstance(value, (bool, int, str)):
                    setattr(instance, f.name, _to_bool(value))
            elif isinstance(current, int) and not isinstance(current, bool):
                setattr(instance, f.name, _clamp_value(path, int(value)))
            elif isinstance(current, float):
                number = float(value)
                if math.isfinite(number):
                    setattr(instance, f.name, _clamp_value(path, number))
            elif isinstance(current, str):
                if isinstance(value, str):
                    setattr(instance, f.name, value[:8192])
            elif isinstance(current, list):
                setattr(instance, f.name, _list_from(f.name, current, value))
            elif isinstance(current, dict):
                setattr(instance, f.name, dict(value) if isinstance(value, dict) else current)
            elif current is None and (value is None or isinstance(value, (str, int))):
                setattr(instance, f.name, str(value) if value is not None else None)
        except (TypeError, ValueError, OverflowError):
            log.debug("Ignoring invalid value for %s", path)
    if isinstance(instance, Settings):
        _validate(instance)
    return instance


def _validate(settings: Settings) -> None:
    """Validate choices and geometry before any UI or worker consumes them."""
    choices = {
        "general.theme": {"system", "light", "dark"},
        "general.accent": {"sakura", "lavender", "ocean"},
        "captions.compute_device": {"auto", "gpu", "cpu"},
        "captions.compute_type": {"auto", "int8", "float16", "float32", "int8_float16"},
        "captions.speech_sensitivity": {"low", "standard", "high"},
        "captions.performance_profile": {"fast", "balanced", "accurate", "maximum", "custom"},
        "overlay.align": {"", "left", "center", "right"},
        "overlay.padding": {"", "tight", "normal", "roomy"},
        "processing.asr_backend": {"local", "remote"},
        "audio.source": {"system", "microphone"},
        "intelligence.on_translator_unavailable": {"show_original", "pause"},
    }
    defaults = Settings()
    for path, allowed in choices.items():
        section, name = path.split(".")
        obj = getattr(settings, section)
        if getattr(obj, name) not in allowed:
            setattr(obj, name, getattr(getattr(defaults, section), name))
    import re

    for name in ("source_language", "target_language"):
        value = getattr(settings.captions, name)
        if value and not re.fullmatch(r"[a-z]{2,3}", value):
            setattr(settings.captions, name, "")
    for name, sizes in (
        ("custom_text_color", (3, 6)),
        ("custom_translation_color", (3, 6)),
        ("custom_border_color", (3, 6, 8)),
        ("custom_bg_color", (6, 8)),
    ):
        value = getattr(settings.overlay, name)
        if not re.fullmatch(r"#[0-9a-fA-F]+", value) or len(value) - 1 not in sizes:
            setattr(settings.overlay, name, getattr(defaults.overlay, name))
    settings.general.window_geometry = _geometry(settings.general.window_geometry)
    settings.overlay.geometry = {
        str(key)[:200]: valid
        for key, value in settings.overlay.geometry.items()
        if (valid := _geometry(value))
    }
    settings.shortcuts.globals = {
        key: value[:80]
        for key, value in settings.shortcuts.globals.items()
        if key in defaults.shortcuts.globals and isinstance(value, str)
    }
    settings.intelligence.translation_order = [
        value for value in settings.intelligence.translation_order[:32] if isinstance(value, str)
    ]
    settings.general.usage_intents = [
        value
        for value in settings.general.usage_intents
        if value in ("video", "games", "calls", "everything")
    ]
    from .overlay_themes import MAX_THEMES, sanitized, valid_name

    settings.overlay.saved_themes = {
        name.strip(): sanitized(value)
        for name, value in list(settings.overlay.saved_themes.items())[:MAX_THEMES]
        if valid_name(name) and isinstance(value, dict)
    }


def _geometry(value: Any) -> list[int]:
    if not isinstance(value, list) or len(value) != 4:
        return []
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return []
    x, y, width, height = value
    return (
        value
        if abs(x) <= 100000 and abs(y) <= 100000 and 1 <= width <= 20000 and 1 <= height <= 20000
        else []
    )


def _list_from(name: str, current: list, value: Any) -> list:
    if not isinstance(value, list):
        return current
    if name == "providers":
        return [from_dict(ProviderSettings, v) for v in value[:32] if isinstance(v, dict)]
    return value


def _clamp_value(path: str, value: float) -> Any:
    bounds = _CLAMPS.get(path)
    if bounds is None:
        return value
    low, high = bounds
    clamped = max(low, min(high, value))
    return type(value)(clamped)


def _clamp(settings: Settings) -> Settings:
    """Apply clamps to a settings object built outside ``from_dict``."""
    return from_dict(Settings, to_dict(settings))


def _coerce[T](kind: Callable[[Any], T], value: Any, default: T) -> T:
    try:
        return kind(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Immutable snapshot handed to the pipeline when captioning starts.

    Live-tunable knobs (translation on/off, overlay style) are applied through
    explicit methods instead, so no worker thread ever reads mutating settings.
    """

    model_name: str
    compute_device: str
    source_language: str
    target_language: str
    filter_hallucinations: bool
    speech_sensitivity: str
    vocabulary: str
    store_history: bool
    translation_order: tuple[str, ...]
    on_translator_unavailable: str
    beam_size: int = 1
    chunk_seconds: float = 8.0
    silence_seconds: float = 0.6
    compute_type: str = "auto"
    cpu_threads: int = 0
    processing_json: str = "{}"
    intelligence_json: str = "{}"

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionConfig:
        return cls(
            model_name=settings.model_name,
            compute_device=settings.captions.compute_device,
            source_language=settings.captions.source_language,
            target_language=(
                settings.captions.target_language if settings.translation_enabled else ""
            ),
            filter_hallucinations=settings.captions.filter_hallucinations,
            speech_sensitivity=settings.captions.speech_sensitivity,
            vocabulary=settings.captions.vocabulary,
            store_history=settings.history.enabled,
            translation_order=tuple(settings.intelligence.translation_order),
            on_translator_unavailable=settings.intelligence.on_translator_unavailable,
            beam_size=settings.captions.beam_size,
            chunk_seconds=settings.captions.chunk_seconds,
            silence_seconds=settings.captions.silence_seconds,
            compute_type=settings.captions.compute_type,
            cpu_threads=settings.captions.cpu_threads,
            processing_json=json.dumps(to_dict(settings.processing)),
            intelligence_json=json.dumps(to_dict(settings.intelligence)),
        )
