"""Allowlisted diagnostics: unknown fields and free-form strings never leave."""

import math

METRICS = (
    "asr_seconds",
    "translation_seconds",
    "queue_wait_seconds",
    "subtitle_latency_seconds",
    "dropped_segments",
    "dropped_translations",
    "dropped_audio_samples",
    "recognition_queue",
    "translation_queue",
)


def safe_snapshot(raw: dict) -> dict:
    result = {}
    for key, allowed in (
        ("phase", {"idle", "preparing", "running", "stopping", "failed"}),
        ("processing", {"local", "remote"}),
        ("audio_source", {"system", "microphone"}),
    ):
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    for key in ("history_pending", "history_dropped"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    metrics = raw.get("pipeline")
    if isinstance(metrics, dict):
        result["pipeline"] = {
            key: round(value, 4)
            for key in METRICS
            if isinstance((value := metrics.get(key)), (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        }
    return result
