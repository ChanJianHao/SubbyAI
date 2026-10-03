"""Human choices mapped onto the same settings used by advanced controls."""

from .settings import QualityTier, Settings

PROFILES = {
    "fast": ("Fast", "Quicker captions, lighter on your computer.", 3.0, 0.35, 1),
    "balanced": ("Balanced", "A comfortable balance for everyday watching.", 6.0, 0.5, 1),
    "accurate": ("Accurate", "More context for tricky speech; captions take longer.", 10.0, 0.7, 3),
}


def apply_profile(settings: Settings, profile: str, capability=None) -> None:
    """Only an explicit profile choice changes engine settings, never a mode toggle."""
    from ..asr.capability import MachineCapability, recommended_tier, tier_available

    if profile not in PROFILES:
        raise ValueError("Unknown performance profile")
    caps = capability or MachineCapability()
    recommended = recommended_tier(caps)
    _, _, chunk, silence, beam = PROFILES[profile]
    tier = QualityTier.QUICK if profile == "fast" else recommended
    if tier is QualityTier.MAXIMUM:
        tier = QualityTier.DETAILED
    if profile == "accurate" and tier_available(QualityTier.DETAILED, caps)[0]:
        tier = QualityTier.DETAILED
    settings.captions.quality = tier
    settings.captions.model_override = ""
    settings.captions.compute_device = "auto"
    settings.captions.compute_type = "auto"
    settings.captions.cpu_threads = 0
    settings.captions.performance_profile = profile
    settings.captions.chunk_seconds = chunk
    settings.captions.silence_seconds = silence
    settings.captions.beam_size = beam
