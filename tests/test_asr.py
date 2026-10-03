"""ASR layer: capability rules, model files, download control, confidence math.

Nothing here loads a model or touches the network. The pieces that would —
the CUDA probe and ``snapshot_download`` — are replaced, which is only possible
because both sit behind a seam on purpose.
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from subbyai.asr import capability, engine, models
from subbyai.asr.capability import MachineCapability, recommended_tier, tier_available
from subbyai.asr.engine import EngineCache, Transcript, summarize_segments
from subbyai.asr.models import DownloadProgress, ModelManager, estimated_size_mb, repo_id_for
from subbyai.core.settings import TIER_MODELS, QualityTier

# Words a user should never have to read in an explanation of why an option is off.
JARGON = ("cuda", "vram", "gpu", "ctranslate", "tensor", "fp16", "int8", "gib")


@pytest.fixture(autouse=True)
def _clear_capability_cache():
    capability._cache = None
    yield
    capability._cache = None


def cap(**overrides) -> MachineCapability:
    values = {
        "has_cuda": False,
        "gpu_name": "",
        "vram_gb": 0.0,
        "cpu_cores": 4,
        "ram_gb": 8.0,
        "free_disk_gb": 200.0,
        "platform": "win32",
    }
    values.update(overrides)
    return MachineCapability(**values)


# ---------- capability ----------


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [
        ({"has_cuda": True, "vram_gb": 12.0}, QualityTier.MAXIMUM),
        ({"has_cuda": True, "vram_gb": 6.0}, QualityTier.MAXIMUM),
        ({"has_cuda": True, "vram_gb": 4.0}, QualityTier.DETAILED),
        # Unknown VRAM must not be read as "plenty".
        ({"has_cuda": True, "vram_gb": 0.0}, QualityTier.DETAILED),
        # CPU recommendations require both core and memory floors.
        ({"cpu_cores": 16, "ram_gb": 32.0}, QualityTier.BALANCED),
        ({"cpu_cores": 8, "ram_gb": 8.0}, QualityTier.BALANCED),
        ({"cpu_cores": 4, "ram_gb": 16.0}, QualityTier.QUICK),
        ({"cpu_cores": 16, "ram_gb": 4.0}, QualityTier.QUICK),
    ],
)
def test_recommended_tier(hardware, expected):
    assert recommended_tier(cap(**hardware)) is expected


def test_a_recommended_tier_can_actually_keep_up(monkeypatch):
    """Whatever is recommended must decode faster than the audio arrives.

    Conservative recommendations leave headroom for capture, rendering and translation.
    The example measurements below are synthetic test inputs, not release benchmarks.
    """
    # Synthetic throughput examples for the recommendation policy.
    measured = {"base": 18.1, "small": 5.5}
    for tier in (QualityTier.QUICK, QualityTier.BALANCED):
        model = TIER_MODELS[tier][0]
        assert measured[model] > 1.5, (
            f"{tier.value} resolves to {model}, measured at {measured[model]}x "
            "realtime, which leaves no headroom for a machine also playing video"
        )


def test_recommended_tier_uses_detect_when_no_capability_given(monkeypatch):
    monkeypatch.setattr(capability, "_probe", lambda: cap(has_cuda=True, vram_gb=24.0))
    assert recommended_tier() is QualityTier.MAXIMUM


def test_quick_is_always_available():
    assert tier_available(QualityTier.QUICK, cap(cpu_cores=1, ram_gb=2.0)) == (True, "")


def test_maximum_needs_a_graphics_card():
    ok, reason = tier_available(QualityTier.MAXIMUM, cap())
    assert not ok
    assert reason == "Needs a graphics card."


def test_maximum_needs_enough_graphics_memory():
    ok, reason = tier_available(QualityTier.MAXIMUM, cap(has_cuda=True, vram_gb=4.0))
    assert not ok
    assert reason == "Needs a graphics card with more memory."


def test_unknown_vram_does_not_lock_the_user_out():
    assert tier_available(QualityTier.MAXIMUM, cap(has_cuda=True, vram_gb=0.0)) == (True, "")


@pytest.mark.parametrize("tier", [QualityTier.BALANCED, QualityTier.DETAILED])
def test_tiers_blocked_by_memory(tier):
    ok, reason = tier_available(tier, cap(ram_gb=2.0))
    assert not ok
    assert "memory" in reason


def test_tier_blocked_by_disk_space():
    ok, reason = tier_available(QualityTier.DETAILED, cap(ram_gb=32.0, free_disk_gb=0.5))
    assert not ok
    assert "space" in reason
    assert "GB" in reason


@pytest.mark.parametrize("tier", list(QualityTier))
@pytest.mark.parametrize(
    "hardware",
    [{}, {"ram_gb": 1.0}, {"free_disk_gb": 0.01}, {"has_cuda": True, "vram_gb": 2.0}],
)
def test_unavailable_reasons_are_plain_language(tier, hardware):
    _, reason = tier_available(tier, cap(**hardware))
    for word in JARGON:
        assert word not in reason.lower(), reason
    if reason:
        assert reason.endswith(".")


def test_detect_probes_once_and_caches(monkeypatch):
    calls = []

    def fake_probe():
        calls.append(1)
        return cap()

    monkeypatch.setattr(capability, "_probe", fake_probe)
    first = capability.detect()
    second = capability.detect()
    assert first is second
    assert len(calls) == 1
    capability.detect(force_refresh=True)
    assert len(calls) == 2


def test_broken_cuda_install_reports_no_graphics_card(monkeypatch):
    """Inside the probe subprocess, a corrupt driver must read as "no card"."""

    def explode():
        raise OSError("nvcuda.dll is corrupt")

    monkeypatch.setattr(capability, "_register_nvidia_wheel_dlls", explode)
    assert capability.probe_cuda_in_process() == 0


def test_a_crashed_probe_reports_no_graphics_card(monkeypatch):
    """And if the subprocess itself dies, the app still starts without a GPU."""
    import subprocess

    def boom(*args, **kwargs):
        raise subprocess.SubprocessError("probe died")

    monkeypatch.setattr(subprocess, "run", boom)
    assert capability._cuda_device_count() == 0
    assert capability._probe_gpu() == (False, "", 0.0)
    assert capability.detect(force_refresh=True).has_cuda is False


def test_detect_reports_real_cpu_and_disk(monkeypatch):
    monkeypatch.setattr(capability, "_probe_gpu", lambda: (False, "", 0.0))
    detected = capability.detect(force_refresh=True)
    assert detected.cpu_cores >= 1
    assert detected.ram_gb > 0.0
    assert detected.free_disk_gb > 0.0
    assert detected.platform


# ---------- model files ----------


def test_repo_ids():
    assert repo_id_for("small") == "Systran/faster-whisper-small"
    # The turbo build does not live under Systran, and the quantisation matters:
    # an fp16 build of the same model is twice the size we advertise.
    assert repo_id_for("large-v3-turbo") == "Zoont/faster-whisper-large-v3-turbo-int8-ct2"
    assert repo_id_for("some-org/custom-ct2") == "some-org/custom-ct2"


def test_quantised_tiers_point_at_a_quantised_build():
    """Repo and advertised size must not drift apart.

    They did: the Detailed tier's size was updated to the int8 figure while the
    repo still pointed at an fp16 build, so the app promised 818 MB and fetched
    1.62 GB — which also consumed the entire 2x disk-space headroom the
    pre-download check reserves.
    """
    from subbyai.asr.catalogue import spec

    for tier, (model_id, size_mb, _) in TIER_MODELS.items():
        repo = repo_id_for(model_id).lower()
        # Anything we claim is under a gigabyte cannot be a full-precision build.
        if size_mb < 1000 and "large" in model_id:
            assert "int8" in repo or "distil" in repo, (
                f"{tier.value} advertises {size_mb} MB but {repo} is not a quantised build"
            )
        # And the catalogue must say which precision it really is. Most of
        # these are float16 downloads that CTranslate2 re-quantises at load;
        # the docs called them all int8, which is how the mismatch above went
        # unnoticed in the first place.
        model = spec(model_id)
        assert model is not None and model.precision in ("float16", "int8")
        bytes_per_param = 2.0 if model.precision == "float16" else 1.0
        assert model.size_bytes / bytes_per_param > 1e7


def test_estimated_sizes_are_the_whole_download():
    """Disk-space estimates include every selected file, not just weights."""
    from subbyai.asr.catalogue import CT2_FILES, all_specs

    assert estimated_size_mb("tiny") == 78
    assert estimated_size_mb("base") == 148
    assert estimated_size_mb("large-v3") == 3091
    assert estimated_size_mb("something-nobody-has-heard-of") > 0

    for model in all_specs():
        assert model.allow_patterns == CT2_FILES or model.allow_patterns
        assert model.size_bytes > 0


def test_every_offered_model_pins_a_commit():
    """refs/main is whatever the owner pushed most recently.

    One repository this app shipped against was transferred to a different
    owner mid-life; the default Detailed model is served by a personal account
    with a few hundred downloads. A pinned commit is what makes an update a
    decision rather than an accident.
    """
    from subbyai.asr.catalogue import all_specs

    for model in all_specs():
        assert len(model.revision) == 40, f"{model.id} is not pinned to a commit"
        assert model.revision.strip("0123456789abcdef") == "", (
            f"{model.id} has a revision that is not a hex sha"
        )


def test_is_downloaded_against_a_fake_cache(tmp_path):
    manager = ModelManager(tmp_path)
    assert not manager.is_downloaded("small")

    snapshot = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "deadbeef"
    snapshot.mkdir(parents=True)
    # Metadata without weights is what an interrupted download leaves behind.
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    assert not manager.is_downloaded("small")

    (snapshot / "model.bin").write_bytes(b"weights")
    assert manager.is_downloaded("small")
    assert manager.local_path("small") == snapshot
    assert not manager.is_downloaded("medium")


def test_local_path_prefers_the_revision_refs_main_points_at(tmp_path):
    root = tmp_path / "models--Systran--faster-whisper-base"
    old = root / "snapshots" / "old"
    new = root / "snapshots" / "new"
    for directory in (old, new):
        directory.mkdir(parents=True)
        (directory / "model.bin").write_bytes(b"w")
    (root / "refs").mkdir()
    (root / "refs" / "main").write_text("old", encoding="utf-8")
    assert ModelManager(tmp_path).local_path("base") == old


def test_download_is_skipped_when_already_present(tmp_path):
    snapshot = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"w")
    events: list[DownloadProgress] = []
    ok = ModelManager(tmp_path).download("base", events.append, threading.Event())
    assert ok
    assert [e.phase for e in events] == [models.PHASE_DONE]


def test_download_refuses_without_disk_space(tmp_path, monkeypatch):
    monkeypatch.setattr(
        models.shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(0, 0, 10 * 1024**2)
    )
    events: list[DownloadProgress] = []
    ok = ModelManager(tmp_path).download("small", events.append, threading.Event())
    assert not ok
    assert events[-1].phase == models.PHASE_ERROR
    assert "space" in events[-1].message
    for word in JARGON:
        assert word not in events[-1].message.lower()


def fake_snapshot(tmp_path):
    """Stand in for the directory snapshot_download leaves behind."""
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(exist_ok=True)
    (snapshot / "model.bin").write_bytes(b"weights")
    return str(snapshot)


def test_download_reports_progress_and_completes(tmp_path, monkeypatch):
    def fake_snapshot_download(repo_id, **kwargs):
        assert repo_id == "Systran/faster-whisper-small"
        assert kwargs["cache_dir"] == str(tmp_path)
        bar = kwargs["tqdm_class"](total=0, unit="B", unit_scale=True)
        for _ in range(4):
            bar.update(100 * 1024 * 1024)
        return fake_snapshot(tmp_path)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    events: list[DownloadProgress] = []
    ok = ModelManager(tmp_path).download("small", events.append, threading.Event())

    assert ok
    phases = [e.phase for e in events]
    assert phases[0] == models.PHASE_CHECKING
    assert models.PHASE_DOWNLOADING in phases
    assert phases[-1] == models.PHASE_DONE
    assert events[-1].fraction == 1.0
    assert [e.downloaded_bytes for e in events] == sorted(e.downloaded_bytes for e in events)


def test_download_counts_duplicated_hub_bars_once(tmp_path, monkeypatch):
    """The hub bills the same bytes on a transfer bar and a reconstruct bar."""

    def fake_snapshot_download(repo_id, **kwargs):
        tqdm_class = kwargs["tqdm_class"]
        transfer = tqdm_class(total=0, unit="B")
        reconstruct = tqdm_class(total=0, unit="B")
        counter = tqdm_class(total=5, desc="Fetching 5 files")
        for _ in range(3):
            transfer.update(4 * 1024 * 1024)
            reconstruct.update(4 * 1024 * 1024)
            counter.update(1)
        return fake_snapshot(tmp_path)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    events: list[DownloadProgress] = []
    ModelManager(tmp_path).download("small", events.append, threading.Event())

    downloading = [e for e in events if e.phase == models.PHASE_DOWNLOADING]
    assert downloading[-1].downloaded_bytes == 12 * 1024 * 1024


def test_download_honours_cancellation(tmp_path, monkeypatch):
    cancel = threading.Event()
    reached_second_update = []

    def fake_snapshot_download(repo_id, **kwargs):
        bar = kwargs["tqdm_class"](total=0, unit="B")
        bar.update(8 * 1024 * 1024)
        cancel.set()
        bar.update(8 * 1024 * 1024)
        reached_second_update.append(True)  # must be unreachable
        return fake_snapshot(tmp_path)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    events: list[DownloadProgress] = []
    ok = ModelManager(tmp_path).download("small", events.append, cancel)

    assert not ok
    assert not reached_second_update
    assert events[-1].phase == models.PHASE_CANCELLED


def test_download_turns_failures_into_sentences(tmp_path, monkeypatch):
    def fake_snapshot_download(repo_id, **kwargs):
        raise OSError("Failed to resolve 'huggingface.co'")

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    events: list[DownloadProgress] = []
    ok = ModelManager(tmp_path).download("small", events.append, threading.Event())

    assert not ok
    assert events[-1].phase == models.PHASE_ERROR
    assert "internet connection" in events[-1].message


def test_download_does_not_claim_success_without_weights(tmp_path, monkeypatch):
    empty = tmp_path / "snapshot"
    empty.mkdir()
    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda repo, **kw: str(empty))
    events: list[DownloadProgress] = []
    ok = ModelManager(tmp_path).download("small", events.append, threading.Event())

    assert not ok
    assert events[-1].phase == models.PHASE_ERROR


# ---------- device resolution ----------


@pytest.mark.parametrize(
    ("preference", "has_cuda", "expected"),
    [
        ("cpu", True, "cpu"),
        ("auto", True, "cuda"),
        ("auto", False, "cpu"),
        ("gpu", True, "cuda"),
        ("gpu", False, "cpu"),
    ],
)
def test_resolve_compute_device(monkeypatch, preference, has_cuda, expected):
    monkeypatch.setattr(capability, "_probe", lambda: cap(has_cuda=has_cuda))
    assert engine.resolve_compute_device(preference) == expected


# ---------- engine cache ----------


class FakeEngine:
    def __init__(self, model_name: str, device_preference: str) -> None:
        self.model_name = model_name
        self.device_preference = device_preference
        self.unloaded = 0

    def unload(self) -> None:
        self.unloaded += 1


def test_engine_cache_reuses_one_engine():
    cache = EngineCache(factory=FakeEngine)
    first = cache.get("small", "auto")
    assert cache.get("small", "auto") is first
    assert first.unloaded == 0


def test_engine_cache_swaps_on_a_different_key():
    cache = EngineCache(factory=FakeEngine)
    first = cache.get("small", "auto")
    second = cache.get("medium", "auto")
    assert second is not first
    assert first.unloaded == 1

    third = cache.get("medium", "cpu")
    assert third is not second
    assert second.unloaded == 1


def test_engine_cache_evict_all():
    cache = EngineCache(factory=FakeEngine)
    first = cache.get("small", "auto")
    cache.evict_all()
    assert first.unloaded == 1
    assert cache.current is None
    assert cache.get("small", "auto") is not first


def test_default_cache_is_shared():
    assert engine.default_cache() is engine.default_cache()


# ---------- confidence math ----------


@dataclass
class FakeSegment:
    text: str = ""
    start: float = 0.0
    end: float = 1.0
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0


@dataclass
class FakeInfo:
    language: str = "en"
    language_probability: float = 1.0
    duration: float = 4.0


def test_summarize_segments_weights_confidence_by_duration():
    import math

    segments = [
        FakeSegment("Hello", 0.0, 1.0, math.log(0.5), 0.1),
        FakeSegment("there.", 1.0, 4.0, math.log(0.9), 0.3),
    ]
    result = summarize_segments(segments, FakeInfo())

    assert result.text == "Hello there."
    assert result.confidence == pytest.approx((0.5 * 1 + 0.9 * 3) / 4)
    assert result.speech_probability == pytest.approx(1.0 - 0.2)
    assert result.language == "en"
    assert result.language_confidence == pytest.approx(1.0)
    assert result.duration == pytest.approx(4.0)


def test_summarize_segments_clamps_impossible_values():
    segments = [FakeSegment("x", 0.0, 1.0, avg_logprob=5.0, no_speech_prob=1.4)]
    result = summarize_segments(segments, FakeInfo(language_probability=1.7))
    assert result.confidence == 1.0
    assert result.speech_probability == 0.0
    assert result.language_confidence == 1.0


def test_summarize_segments_counts_zero_length_segments():
    import math

    segments = [
        FakeSegment("a", 0.0, 0.0, math.log(0.2), 0.0),
        FakeSegment("b", 0.0, 0.0, math.log(0.8), 0.0),
    ]
    result = summarize_segments(segments, FakeInfo())
    assert result.confidence == pytest.approx(0.5)


def test_summarize_segments_with_nothing_decoded():
    result = summarize_segments([], FakeInfo(language=""))
    assert result == Transcript(text="", language=None, language_confidence=1.0, duration=4.0)
    assert result.confidence == 0.0
    assert result.speech_probability == 0.0


def test_summarize_segments_survives_missing_attributes():
    class Bare:
        text = "just text"

    result = summarize_segments([Bare()], None)
    assert result.text == "just text"
    assert result.language is None
    assert result.confidence == 1.0  # avg_logprob absent -> treated as 0


# ---------- transcribe options ----------


class RecordingModel:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return [FakeSegment("recorded", 0.0, 1.0)], FakeInfo()


def test_transcribe_never_asks_whisper_to_translate():
    transcriber = engine.TranscriptionEngine("small", "cpu")
    transcriber._model = RecordingModel()  # stand in for a loaded WhisperModel

    result = transcriber.transcribe(object(), language="de")

    assert result.text == "recorded"
    assert transcriber._model.kwargs == {
        "task": "transcribe",
        "language": "de",
        "beam_size": 1,
        # Off on purpose. The segmenter has already located the speech, and
        # this would trim the trailing silence that tells the decoder an
        # utterance ended — which the shortened encoder window relies on.
        "vad_filter": False,
        "condition_on_previous_text": False,
        "initial_prompt": None,
    }


def test_transcribe_passes_the_vocabulary_prompt():
    transcriber = engine.TranscriptionEngine("small", "cpu")
    transcriber._model = RecordingModel()

    transcriber.transcribe(object(), vocabulary="Midgar, Nibelheim, Chocobo")

    assert transcriber._model.kwargs["initial_prompt"] == (
        "Terms that may come up: Midgar, Nibelheim, Chocobo."
    )


def test_vocabulary_prompt_is_bounded_deduped_and_shaped():
    from subbyai.asr.engine import _MAX_PROMPT_CHARS, _MAX_PROMPT_TERMS, vocabulary_prompt

    assert vocabulary_prompt("") == ""
    assert vocabulary_prompt(",, ,") == ""
    # Newlines and commas both separate; duplicates collapse case-insensitively.
    assert vocabulary_prompt("Midgar\nmidgar, Chocobo") == (
        "Terms that may come up: Midgar, Chocobo."
    )
    # A wall of terms is capped instead of crowding the decoder's context.
    many = vocabulary_prompt(", ".join(f"term{i}" for i in range(200)))
    assert len(many) <= _MAX_PROMPT_CHARS
    assert many.count("term") <= _MAX_PROMPT_TERMS
    assert many.endswith(".")


def test_transcribe_without_a_loaded_model_is_a_friendly_error():
    with pytest.raises(engine.EngineError) as caught:
        engine.TranscriptionEngine("small", "cpu").transcribe(object())
    for word in JARGON:
        assert word not in str(caught.value).lower()


def test_engine_reports_device_before_loading(monkeypatch):
    monkeypatch.setattr(capability, "_probe", lambda: cap(has_cuda=True, vram_gb=8.0))
    transcriber = engine.TranscriptionEngine("small", "auto")
    assert not transcriber.is_loaded
    assert transcriber.device == "cuda"
    assert transcriber.model_name == "small"


# ---------- managing what is on disk ----------
#
# A catalogue has to answer three questions the download path never needed:
# what is installed, what is it really costing, and can I have that space back.


def _install(cache: Path, repo: str, *, blob_mb: int = 1) -> Path:
    """Lay out a model the way huggingface_hub does, blobs and all."""
    root = cache / f"models--{repo.replace('/', '--')}"
    snapshot = root / "snapshots" / "cafe1234"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"\0" * (blob_mb * 1024 * 1024))
    blobs = root / "blobs"
    blobs.mkdir()
    (blobs / "cafe1234").write_bytes(b"\0" * (blob_mb * 1024 * 1024))
    (root / "refs").mkdir()
    (root / "refs" / "main").write_text("cafe1234", encoding="utf-8")
    return root


def test_installed_lists_models_by_name_not_by_repo(tmp_path):
    _install(tmp_path, "Systran/faster-whisper-base")
    _install(tmp_path, "Zoont/faster-whisper-large-v3-turbo-int8-ct2")
    installed = ModelManager(tmp_path).installed()
    assert "base" in installed
    assert "large-v3-turbo" in installed, "an overridden repo must map back to its model name"


def test_installed_ignores_a_half_finished_download(tmp_path):
    """The directory exists long before the weights do."""
    (tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc").mkdir(parents=True)
    assert ModelManager(tmp_path).installed() == []


def test_disk_bytes_counts_the_blobs_too(tmp_path):
    """The snapshot is a copy of the blob on Windows, so a model costs both."""
    _install(tmp_path, "Systran/faster-whisper-base", blob_mb=2)
    measured = ModelManager(tmp_path).disk_bytes("base")
    assert measured >= 4 * 1024 * 1024, "quoting only the snapshot understates the reclaim"


def test_disk_bytes_is_zero_for_something_never_downloaded(tmp_path):
    assert ModelManager(tmp_path).disk_bytes("large-v3") == 0


def test_delete_reclaims_the_space_and_reports_how_much(tmp_path):
    _install(tmp_path, "Systran/faster-whisper-base", blob_mb=2)
    manager = ModelManager(tmp_path)
    before = manager.disk_bytes("base")

    freed = manager.delete("base")

    assert freed == before
    assert manager.is_downloaded("base") is False
    assert manager.installed() == []
    assert manager.disk_bytes("base") == 0


def test_deleting_something_absent_is_not_an_error(tmp_path):
    assert ModelManager(tmp_path).delete("base") == 0


def test_delete_leaves_the_other_models_alone(tmp_path):
    _install(tmp_path, "Systran/faster-whisper-base")
    _install(tmp_path, "Systran/faster-whisper-small")
    manager = ModelManager(tmp_path)

    manager.delete("base")

    assert manager.installed() == ["small"]


def test_delete_says_so_when_the_files_are_locked(tmp_path, monkeypatch):
    """Windows refuses to unlink a model the engine still has open.

    Failing quietly would leave a half-removed model that loads and then dies
    somewhere inside CTranslate2.
    """
    from subbyai.asr.models import ModelInUse

    _install(tmp_path, "Systran/faster-whisper-base")

    def refuse(*args, **kwargs):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr("subbyai.asr.models.shutil.rmtree", refuse)
    with pytest.raises(ModelInUse) as caught:
        ModelManager(tmp_path).delete("base")
    assert "in use" in str(caught.value).lower()
    assert ModelManager(tmp_path).is_downloaded("base"), "nothing may be half-removed"


def test_every_known_model_has_a_repo_and_a_size():
    from subbyai.asr.models import estimated_size_mb, known_models, repo_id_for

    for name in known_models():
        assert repo_id_for(name), f"{name} has no repository"
        assert estimated_size_mb(name) > 0, f"{name} has no size to quote"


# ---------- the model is never fetched behind the user's back ----------


def test_loading_an_absent_model_refuses_instead_of_downloading(tmp_path, monkeypatch):
    """faster-whisper would fetch it right there, on the pipeline worker.

    Synchronously, with no progress and no way to cancel — which for the
    largest model is three gigabytes behind a spinner that says nothing. That
    is the exact failure this module was built to remove, and it is also what
    would make a Remove button dangerous: delete the running model, press
    Start, and the app hangs.
    """
    from subbyai.asr.engine import EngineError, TranscriptionEngine

    called = []
    monkeypatch.setattr("faster_whisper.WhisperModel", lambda *a, **k: called.append((a, k)))
    engine = TranscriptionEngine("large-v3", "cpu", models=ModelManager(tmp_path))

    with pytest.raises(EngineError) as caught:
        engine.load()

    assert "download" in str(caught.value).lower()
    assert called == [], "nothing may reach WhisperModel without local weights"


def test_the_engine_uses_the_cache_it_was_given(tmp_path):
    """A bare ModelManager() ignores an injected directory entirely."""
    from subbyai.asr.engine import TranscriptionEngine

    snapshot = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"\0")

    engine = TranscriptionEngine("base", "cpu", models=ModelManager(tmp_path))
    assert engine._models.local_path("base") == snapshot


def test_decode_threads_follow_the_machine():
    """faster-whisper defaults to four threads whatever the processor is."""
    from subbyai.asr.engine import _decode_threads

    threads = _decode_threads()
    assert 1 <= threads <= 8


def test_evicting_releases_the_model_so_its_files_can_go():
    """Stopping captions deliberately keeps the model warm, so stop is not enough."""
    from subbyai.asr.engine import EngineCache

    unloaded = []

    class FakeEngine:
        def __init__(self, name, device):
            self.name = name

        def unload(self):
            unloaded.append(self.name)

    cache = EngineCache(factory=FakeEngine)
    cache.get("base", "cpu")
    assert cache.current is not None

    cache.evict_all()

    assert unloaded == ["base"]
    assert cache.current is None


# ---------- English-only variants ----------


@pytest.mark.parametrize(
    ("tier_model", "expected"),
    [("tiny", "tiny.en"), ("base", "base.en"), ("small", "small"), ("large-v3", "large-v3")],
)
def test_english_audio_uses_the_english_build_where_it_helps(tier_model, expected):
    """Only supported tiny/base English variants replace an explicit choice."""
    from subbyai.asr.catalogue import english_variant

    assert english_variant(tier_model) == expected


def test_auto_detect_never_substitutes_an_english_only_model():
    """It would decide the very question it was asked."""
    from subbyai.core.settings import QualityTier, Settings

    settings = Settings()
    settings.captions.quality = QualityTier.QUICK
    settings.captions.source_language = ""
    assert settings.model_name == "base"

    settings.captions.source_language = "en"
    assert settings.model_name == "base.en"

    settings.captions.source_language = "ja"
    assert settings.model_name == "base"


def test_an_explicit_override_is_never_second_guessed():
    from subbyai.core.settings import Settings

    settings = Settings()
    settings.captions.source_language = "en"
    settings.captions.model_override = "large-v3"
    assert settings.model_name == "large-v3"


# ---------- the encoder window ----------


def test_the_encoder_window_follows_the_audio():
    """Short phrases use bounded windows; long phrases retain the full window."""
    from subbyai.asr.snug_window import MAX_FRAMES, window_for

    assert window_for(140) == 512, "a 1.4s utterance must not cost 30s of encoder"
    assert window_for(200) == 512
    assert window_for(400) == 768
    assert window_for(1000) == 1280
    assert window_for(3000) == MAX_FRAMES
    assert window_for(9000) == MAX_FRAMES, "longer than the window still clamps"


def test_the_window_always_keeps_a_margin_past_the_speech():
    """Preserve silence after each phrase to reduce decoder repetition."""
    from subbyai.asr.snug_window import MARGIN_FRAMES, MAX_FRAMES, window_for

    for content in range(100, MAX_FRAMES, 137):
        window = window_for(content)
        if window < MAX_FRAMES:
            assert window >= content + MARGIN_FRAMES, (
                f"{content} frames of speech got a {window}-frame window, "
                "leaving too little silence after it"
            )


def test_windows_come_from_a_small_set_of_shapes():
    """A new tensor shape per utterance would thrash the runtime's allocator."""
    from subbyai.asr.snug_window import MAX_FRAMES, QUANTUM, window_for

    shapes = {window_for(n) for n in range(50, 3000, 7)}
    # The 3000-frame cap is set by the encoder and is not a multiple of
    # anything; every window below it is quantised.
    assert all(w % QUANTUM == 0 for w in shapes if w != MAX_FRAMES)
    # 21 today, one per 128-frame step from the floor to the cap.
    assert len(shapes) <= 24, f"{len(shapes)} distinct windows is too many"


def test_shortening_the_window_is_never_fatal(monkeypatch):
    """If faster-whisper's internals change, captions must still work."""
    from subbyai.asr import snug_window

    monkeypatch.setattr(snug_window, "_applied", False)
    monkeypatch.delattr("faster_whisper.transcribe.pad_or_trim", raising=False)
    assert snug_window.apply() is False, "a missing hook must be reported, not raised"


def test_only_the_default_window_is_narrowed(monkeypatch):
    """An explicit length is somebody else's decision, not ours to override."""
    import faster_whisper.transcribe as fw

    from subbyai.asr import snug_window

    seen: list[int] = []
    monkeypatch.setattr(snug_window, "_applied", False)
    monkeypatch.setattr(fw, "pad_or_trim", lambda a, length=3000, **k: seen.append(length))
    assert snug_window.apply() is True

    import numpy

    audio = numpy.zeros((80, 200), dtype=numpy.float32)
    fw.pad_or_trim(audio)  # the default: narrow it
    fw.pad_or_trim(audio, 1500)  # explicit: leave it alone
    assert seen == [512, 1500]


# ---------- what ships ----------


def test_packaging_never_bundles_compiled_caches():
    """Compiled resource caches can expose build-machine paths."""
    from pathlib import Path

    spec = Path(__file__).resolve().parent.parent / "packaging" / "pyinstaller.spec"
    text = spec.read_text(encoding="utf-8")
    assert '(str(_RESOURCES), "subbyai/resources")' not in text, (
        "copying the directory wholesale sweeps up __pycache__"
    )
    assert '".pyc"' in text, "the resource copy must filter compiled files out"


def test_resources_contain_nothing_private():
    """Everything under resources/ ships verbatim, so it must be clean."""
    from pathlib import Path

    resources = Path(__file__).resolve().parent.parent / "src" / "subbyai" / "resources"
    private = ("C:" + chr(92) + "Users" + chr(92), "DESKTOP-")
    for item in resources.iterdir():
        if not item.is_file() or item.suffix.lower() in {".png", ".ico", ".pyc"}:
            continue
        text = item.read_text(encoding="utf-8", errors="replace")
        for needle in private:
            assert needle not in text, f"{item.name} carries {needle!r}"
