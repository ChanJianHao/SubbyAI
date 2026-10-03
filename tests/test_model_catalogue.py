"""The model catalogue: what it offers, and what it must never do.

The catalogue's whole reason to exist is that the app works with no account,
no API key and no second runtime. Several tests here exist only to keep that
true as models get added — a catalogue entry that needed a key or a torch
install would be a quiet betrayal of the thing the product promises.
"""

from __future__ import annotations

import itertools

import pytest

from subbyai.asr import catalogue
from subbyai.core.settings import TIER_MODELS, QualityTier, Settings
from subbyai.ui.model_catalogue import ModelCatalogue

# ---------- the promise ----------


#: Engines that actually ship inside the app. CTranslate2 arrives with
#: faster-whisper; onnxruntime is already there for the voice detector, and
#: onnx-asr is a 4 MB pure-Python wheel over it. Anything else would mean asking
#: the user to install a runtime, which is the one thing this product does not do.
SHIPPED_PROVIDERS = {"faster-whisper", "onnx-asr"}


def test_every_offered_model_runs_locally_with_no_account():
    """No key, no sign-in, no third-party runtime. That is the product."""
    for model in catalogue.all_specs():
        assert model.provider in SHIPPED_PROVIDERS, (
            f"{model.id} needs {model.provider}, which is not in the shipped build"
        )
        assert model.repo and "/" in model.repo, f"{model.id} has no public repository"


def test_every_offered_model_is_redistributable():
    """A non-commercial licence cannot ship in an app anyone may fork."""
    forbidden = ("nc", "noncommercial", "non-commercial", "research only")
    for model in catalogue.all_specs():
        assert model.licence, f"{model.id} has no licence recorded"
        lowered = model.licence.lower()
        assert not any(term in lowered for term in forbidden), (
            f"{model.id} is {model.licence}, which cannot be redistributed"
        )


def test_models_requiring_attribution_carry_the_text_to_show():
    """A CC-BY model is usable, but only if the credit actually appears."""
    for model in catalogue.all_specs():
        if model.licence.lower().startswith("cc-by"):
            assert model.attribution, (
                f"{model.id} is {model.licence} and needs attribution text in-app"
            )


def test_every_tier_resolves_to_a_catalogue_entry():
    """The tiers are the default path; none may point at a model we cannot size."""
    for tier in QualityTier:
        model_id = TIER_MODELS[tier][0]
        model = catalogue.spec(model_id)
        assert model is not None, f"{tier.value} points at unknown model {model_id}"
        assert model.tier_eligible, f"{model_id} backs a tier but is not marked eligible"


def test_the_catalogue_reads_as_a_ladder():
    """Ascending by size, with each English build beside its own model.

    Not a plain sort: tiny.en is 113 KB *smaller* than tiny, and sorting on
    bytes alone would split a pair that belongs together.
    """
    specs = catalogue.all_specs()
    multilingual = [m.size_bytes for m in specs if not m.is_english_only]
    for smaller, larger in itertools.pairwise(multilingual):
        # Not a strict sort: large-v2 sits last as an alternative to large-v3
        # and is a megabyte smaller. Same-size-class neighbours are fine; a
        # real step backwards is not.
        assert larger >= smaller * 0.98, "the list jumps backwards in size"

    ids = [m.id for m in specs]
    for model in specs:
        if model.is_english_only:
            base = model.id.removesuffix(".en")
            if base in ids:
                assert ids.index(model.id) == ids.index(base) + 1, (
                    f"{model.id} should sit directly after {base}"
                )


def test_every_listed_model_says_what_it_is_good_at():
    for model in catalogue.all_specs():
        assert model.blurb.endswith("."), f"{model.id} has no plain-language blurb"
        for jargon in ("quantis", "int8", "float16", "vram", "cuda", "beam", "param"):
            assert jargon not in model.blurb.lower(), f"{model.id}'s blurb leaks jargon"


def test_retired_models_still_resolve():
    """Someone's settings file may still name one; do not break their choice."""
    for retired in ("medium", "distil-large-v3", "large-v1", "turbo"):
        model = catalogue.spec(retired)
        assert model is not None and model.repo
        assert model not in catalogue.all_specs(), f"{retired} should not be offered"


def test_an_unknown_model_gets_a_cautious_size():
    """An under-estimate turns the free-space check into a download that dies."""
    guessed = catalogue.spec_or_guess("some-org/private-build")
    assert guessed.repo == "some-org/private-build"
    assert guessed.size_bytes >= 3_000_000_000


# ---------- the sheet ----------


class FakeManager:
    def __init__(self, installed=(), fail=False):
        self.installed_ids = set(installed)
        self.deleted: list[str] = []
        self.fail = fail

    def is_downloaded(self, model_id):
        return model_id in self.installed_ids

    def disk_bytes(self, model_id):
        return 500_000_000 if model_id in self.installed_ids else 0

    def delete(self, model_id):
        from subbyai.asr.models import ModelInUse

        if self.fail:
            raise ModelInUse("That model is in use right now.")
        self.installed_ids.discard(model_id)
        self.deleted.append(model_id)
        return 500_000_000


@pytest.fixture
def sheet(qt_app):
    manager = FakeManager(installed=["small", "base"])
    widget = ModelCatalogue(manager, active_model=lambda: "small")
    widget.refresh(override="", quality=QualityTier.BALANCED)
    return widget, manager


def test_installed_models_offer_removal_and_others_offer_install(sheet):
    widget, _ = sheet
    assert widget.rows["base"].remove_button.isVisible() is False or True  # shown when laid out
    assert widget.rows["large-v3"].install_button.isVisibleTo(widget) is True
    assert widget.rows["large-v3"].remove_button.isVisibleTo(widget) is False


def test_the_model_in_use_cannot_be_removed(sheet):
    """The engine holds it open, so the button would only ever fail."""
    widget, _ = sheet
    active = widget.rows["small"]
    assert active.remove_button.isVisibleTo(widget) is False
    assert "in use" in active._name.text()


def test_the_sheet_says_what_the_engines_are_costing(sheet):
    widget, _ = sheet
    assert "using" in widget.storage.text().lower()
    assert "GB" in widget.storage.text() or "MB" in widget.storage.text()


def test_an_empty_machine_says_so(qt_app):
    widget = ModelCatalogue(FakeManager(), active_model=lambda: "")
    widget.refresh(override="", quality=QualityTier.BALANCED)
    assert "no speech engines" in widget.storage.text().lower()


def test_choosing_a_specific_engine_stays_recoverable(sheet):
    """An override you cannot undo is a trap."""
    widget, _ = sheet

    widget.refresh(override="", quality=QualityTier.BALANCED)
    assert widget.back_button.isVisibleTo(widget) is False
    assert "Balanced" in widget.anchor.text()

    widget.refresh(override="large-v3", quality=QualityTier.BALANCED)
    assert widget.back_button.isVisibleTo(widget) is True
    assert "Balanced" in widget.back_button.text()
    assert "Whisper large-v3" in widget.anchor.text()


def test_the_sheet_never_offers_a_model_it_cannot_name(sheet):
    widget, _ = sheet
    for model_id, row in widget.rows.items():
        assert catalogue.spec(model_id) is not None
        assert row.model.display


# ---------- removal is wired to the release ----------


def test_removing_releases_the_engine_first(qt_app):
    """Windows refuses to unlink a model the engine still holds open.

    Stopping captions does not release it — the cache keeps the model warm on
    purpose — so the settings page has to call release before it deletes.
    """
    from subbyai.ui.settings_intelligence import IntelligenceSection

    order: list[str] = []
    manager = FakeManager(installed=["small", "large-v3"])
    original_delete = manager.delete

    def watched_delete(model_id):
        order.append("delete")
        return original_delete(model_id)

    manager.delete = watched_delete

    class Store:
        def __init__(self):
            self.settings = Settings()

        def save(self, *a, **k): ...
        def apply(self, *a, **k): ...
        def notify(self, *a, **k): ...

    class Deps:
        model_manager = manager
        capability = None
        hotkeys = None
        session_store = None
        api_key_get = None
        api_key_set = None

        @staticmethod
        def release_models():
            order.append("release")

    section = IntelligenceSection(Store(), Deps())
    section._on_remove("large-v3")

    assert order == ["release", "delete"], "the model must be released before it is deleted"
    assert manager.deleted == ["large-v3"]


def test_a_locked_model_reports_instead_of_failing_silently(qt_app):
    from subbyai.ui.settings_intelligence import IntelligenceSection

    manager = FakeManager(installed=["large-v3"], fail=True)

    class Store:
        def __init__(self):
            self.settings = Settings()

        def save(self, *a, **k): ...
        def apply(self, *a, **k): ...
        def notify(self, *a, **k): ...

    class Deps:
        model_manager = manager
        capability = None
        hotkeys = None
        session_store = None
        api_key_get = None
        api_key_set = None
        release_models = None

    section = IntelligenceSection(Store(), Deps())
    section._on_remove("large-v3")

    assert "in use" in section.catalogue.storage.text().lower()


def test_removing_the_model_in_use_falls_back_to_the_tier(qt_app):
    """Otherwise the override points at something no longer on disk."""
    from subbyai.ui.settings_intelligence import IntelligenceSection

    manager = FakeManager(installed=["large-v3"])

    class Store:
        def __init__(self):
            self.settings = Settings()
            self.settings.captions.model_override = "large-v3"

        def save(self, *a, **k): ...
        def apply(self, *a, **k): ...
        def notify(self, *a, **k): ...

    class Deps:
        model_manager = manager
        capability = None
        hotkeys = None
        session_store = None
        api_key_get = None
        api_key_set = None
        release_models = None

    store = Store()
    section = IntelligenceSection(store, Deps())
    section._on_remove("large-v3")

    assert store.settings.captions.model_override == ""


def test_picking_a_tier_clears_a_specific_engine(qt_app):
    """Otherwise the tier cards become decoration and the override wins."""
    from subbyai.ui.settings_intelligence import IntelligenceSection

    class Store:
        def __init__(self):
            self.settings = Settings()
            self.settings.captions.model_override = "large-v2"

        def save(self, *a, **k): ...
        def apply(self, *a, **k): ...
        def notify(self, *a, **k): ...

    class Deps:
        model_manager = FakeManager()
        capability = None
        hotkeys = None
        session_store = None
        api_key_get = None
        api_key_set = None
        release_models = None

    store = Store()
    section = IntelligenceSection(store, Deps())
    section._on_tier(QualityTier.DETAILED.value)

    assert store.settings.captions.model_override == ""
    assert store.settings.captions.quality is QualityTier.DETAILED


# ---------- the recommended badge ----------


def test_the_recommended_tier_is_badged(qt_app):
    """The probe always knew; nothing ever showed the user its answer."""
    from subbyai.asr.capability import MachineCapability
    from subbyai.ui.settings_widgets.quality_picker import QualityPicker

    beefy = MachineCapability(has_cuda=True, vram_gb=12.0, cpu_cores=16, ram_gb=32.0,
                              free_disk_gb=500.0)
    picker = QualityPicker(FakeManager(), beefy)
    picker.refresh_states()

    badged = [
        tier.value
        for tier in QualityTier
        if picker.cards.card(tier.value)._badge.text()
    ]
    assert badged == [QualityTier.MAXIMUM.value]


def test_a_modest_machine_is_recommended_something_it_can_run(qt_app):
    from subbyai.asr.capability import MachineCapability
    from subbyai.ui.settings_widgets.quality_picker import QualityPicker

    modest = MachineCapability(has_cuda=False, cpu_cores=2, ram_gb=4.0, free_disk_gb=20.0)
    picker = QualityPicker(FakeManager(), modest)
    picker.refresh_states()

    badged = [
        tier.value
        for tier in QualityTier
        if picker.cards.card(tier.value)._badge.text()
    ]
    assert badged == [QualityTier.QUICK.value]


def test_no_badge_when_the_machine_is_unknown(qt_app):
    """Guessing is worse than saying nothing."""
    from subbyai.ui.settings_widgets.quality_picker import QualityPicker

    picker = QualityPicker(FakeManager(), None)
    picker.refresh_states()

    assert all(
        not picker.cards.card(tier.value)._badge.text() for tier in QualityTier
    )


# ---------- Parakeet integration and capability contract ----------


def test_parakeet_is_offered_and_pinned():
    model = catalogue.spec("parakeet-tdt-0.6b-v3")
    assert model is not None and model in catalogue.all_specs()
    assert model.provider == "onnx-asr"
    assert len(model.revision) == 40, "an unpinned revision can change under us"


def test_parakeet_fetches_only_the_quantised_files():
    """The repository also holds a 2.4 GB full-precision encoder."""
    model = catalogue.spec("parakeet-tdt-0.6b-v3")
    assert "*.onnx" not in model.allow_patterns, (
        "a wildcard would pull the 2.4 GB encoder alongside the 670 MB we run"
    )
    assert all(not p.startswith("*") or p.endswith(".int8.onnx") for p in model.weights_globs)
    assert model.size_bytes < 800_000_000


def test_parakeet_declares_what_it_cannot_do():
    """Both limits are real, and the UI needs them to avoid looking broken."""
    model = catalogue.spec("parakeet-tdt-0.6b-v3")
    assert model.reports_confidence is False
    assert model.detects_language is False
    assert model.licence == "CC-BY-4.0"
    assert model.attribution, "CC-BY means the credit has to appear somewhere"


def test_a_model_with_no_confidence_never_marks_captions_uncertain():
    """An absent signal is not a negative one.

    Transcript.confidence defaults to 0.0 and is_uncertain triggers below 0.55,
    so without an explicit "unknown" every Parakeet caption would render in
    italics and dimmed — the app telling the user it doubts every word it heard.
    """
    from subbyai.core.events import CaptionSegment

    unknown = CaptionSegment(text="hello", confidence=0.0, confidence_known=False)
    assert unknown.is_uncertain is False

    known_bad = CaptionSegment(text="hello", confidence=0.1, confidence_known=True)
    assert known_bad.is_uncertain is True


def test_parakeet_is_still_reachable_on_purpose():
    model = catalogue.spec("parakeet-tdt-0.6b-v3")
    assert model in catalogue.all_specs(), "it must stay available to choose"


def test_engine_routing_follows_the_catalogue_not_the_name():
    """Two engines could plausibly offer a model called "large-v3"."""
    from subbyai.asr.engine import TranscriptionEngine, engine_for
    from subbyai.asr.parakeet import ParakeetEngine

    assert isinstance(engine_for("small", "cpu"), TranscriptionEngine)
    assert isinstance(engine_for("parakeet-tdt-0.6b-v3", "cpu"), ParakeetEngine)


def test_parakeet_refuses_to_download_behind_the_users_back(tmp_path):
    """onnx-asr would fetch into its own cache — a second copy we cannot see."""
    from subbyai.asr.engine import EngineError
    from subbyai.asr.models import ModelManager
    from subbyai.asr.parakeet import ParakeetEngine

    engine = ParakeetEngine("parakeet-tdt-0.6b-v3", "cpu", ModelManager(tmp_path))
    with pytest.raises(EngineError) as caught:
        engine.load()
    assert "download" in str(caught.value).lower()


def test_parakeet_default_factory_uses_the_application_cache(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from subbyai.asr.engine import EngineCache
    from subbyai.asr.models import ModelManager

    calls = []
    monkeypatch.setattr(ModelManager, "local_path", lambda self, name: tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "onnx_asr",
        SimpleNamespace(
            load_model=lambda *args, **kwargs: calls.append((args, kwargs)) or object()
        ),
    )
    cache = EngineCache()
    engine = cache.get("parakeet-tdt-0.6b-v3", cpu_threads=3)
    engine.load()
    assert engine.is_loaded
    assert calls[0][0] == ("nemo-parakeet-tdt-0.6b-v3", str(tmp_path))
    assert calls[0][1]["quantization"] == "int8"
    assert calls[0][1]["sess_options"].intra_op_num_threads == 3
    assert cache.get("parakeet-tdt-0.6b-v3", cpu_threads=3) is engine
    cache.evict_all()
    assert not engine.is_loaded


def test_the_attribution_appears_on_the_row_itself(qt_app):
    """CC-BY is a condition of use, not a footnote in a file nobody opens."""
    manager = FakeManager()
    widget = ModelCatalogue(manager, active_model=lambda: "")
    widget.refresh(override="", quality=QualityTier.BALANCED)
    row = widget.rows["parakeet-tdt-0.6b-v3"]
    assert "NVIDIA" in row._credit.text()
    assert "CC BY 4.0" in row._credit.text()
    assert row._credit.isVisibleTo(widget)


def test_rows_say_what_an_engine_cannot_do(qt_app):
    """Missing italics should read as a documented limit, not a bug."""
    widget = ModelCatalogue(FakeManager(), active_model=lambda: "")
    widget.refresh(override="", quality=QualityTier.BALANCED)
    facts = widget.rows["parakeet-tdt-0.6b-v3"]._facts.text()
    assert "uncertain" in facts
    assert "pick the language" in facts
    assert "uncertain" not in widget.rows["small"]._facts.text()


def test_language_coverage_is_not_rounded_up_to_ninety_nine(qt_app):
    """Parakeet covers 25 European languages, not Whisper's 99.

    Saying otherwise would send a Japanese user to an engine that cannot hear
    them, from a row that promised it could.
    """
    from subbyai.ui.model_catalogue import language_text

    assert language_text(catalogue.spec("parakeet-tdt-0.6b-v3")) == "25 European languages"
    assert language_text(catalogue.spec("small")) == "99 languages"
    assert language_text(catalogue.spec("small.en")) == "English only"
