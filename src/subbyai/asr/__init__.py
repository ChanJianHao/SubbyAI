"""Speech recognition: what the machine can do, the model files, the decoder.

Importing this package is cheap. Every expensive dependency — CTranslate2,
faster-whisper, huggingface_hub, numpy — is imported inside the function that
needs it, keeping startup responsive.
"""

from .capability import (
    MachineCapability,
    detect,
    recommended_tier,
    tier_available,
    tier_download_mb,
)
from .engine import (
    EngineCache,
    EngineError,
    Transcript,
    TranscriptionEngine,
    default_cache,
    resolve_compute_device,
    summarize_segments,
)
from .models import (
    DownloadProgress,
    ModelManager,
    estimated_size_mb,
    repo_id_for,
    required_free_mb,
)

__all__ = [
    "DownloadProgress",
    "EngineCache",
    "EngineError",
    "MachineCapability",
    "ModelManager",
    "Transcript",
    "TranscriptionEngine",
    "default_cache",
    "detect",
    "estimated_size_mb",
    "recommended_tier",
    "repo_id_for",
    "required_free_mb",
    "resolve_compute_device",
    "summarize_segments",
    "tier_available",
    "tier_download_mb",
]
