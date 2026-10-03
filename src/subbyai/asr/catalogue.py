"""Every speech model the app can run, as data.

One table, consulted by the downloader, the engine, the quality picker and the
catalogue sheet, so those four can never disagree about a model's size, its
repository or its licence. Adding a model means adding a row.

Revisions pin the exact model files. Sizes include every file selected by
``allow_patterns`` and feed the disk-space check. Precision describes the
downloaded weights; CTranslate2 may re-quantize them at load, so download size
and inference memory requirements differ.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The five files CTranslate2 reads. Anything else in a repo (PyTorch weights,
#: ONNX exports, READMEs) is not fetched.
CT2_FILES: tuple[str, ...] = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
)

MULTILINGUAL = "multilingual"
ENGLISH = "en"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One runnable speech model."""

    id: str
    display: str
    repo: str
    revision: str
    """Pinned commit. Never resolve refs/main for something a user downloads."""

    size_bytes: int
    blurb: str
    """One plain sentence: what this model is good at. No jargon, no numbers."""

    languages: str = MULTILINGUAL
    publisher: str = "OpenAI"
    licence: str = "MIT"
    attribution: str = ""
    """Text that must be shown in-app. Non-empty only where a licence demands it."""

    precision: str = "float16"
    provider: str = "faster-whisper"
    allow_patterns: tuple[str, ...] = CT2_FILES
    weights_globs: tuple[str, ...] = ("model.bin",)
    tier_eligible: bool = False
    """Reachable through the four quality tiers, rather than only the catalogue."""

    reports_confidence: bool = True
    """False when the engine returns text and nothing else.

    Captions from such a model are never marked uncertain — an absent signal is
    not a negative one. The catalogue says so rather than letting the missing
    italics look like a bug.
    """

    detects_language: bool = True
    """False when the engine cannot say what was spoken, or be told.

    Auto-detect cannot use such a model, so the app has to ask the user which
    language they are listening to before it will run.
    """

    listed: bool = True
    """Offered in the catalogue. False means resolvable but no longer promoted."""

    @property
    def size_mb(self) -> int:
        return round(self.size_bytes / 1_000_000)

    @property
    def is_english_only(self) -> bool:
        return self.languages == ENGLISH


def _whisper(
    model_id: str,
    display: str,
    revision: str,
    size_bytes: int,
    blurb: str,
    *,
    repo: str = "",
    languages: str = MULTILINGUAL,
    precision: str = "float16",
    tier_eligible: bool = False,
    listed: bool = True,
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        display=display,
        repo=repo or f"Systran/faster-whisper-{model_id}",
        revision=revision,
        size_bytes=size_bytes,
        blurb=blurb,
        languages=languages,
        precision=precision,
        tier_eligible=tier_eligible,
        listed=listed,
    )


#: Parakeet accepts variable-length audio and has a separate language/capability contract.
_PARAKEET_ATTRIBUTION = (
    "Parakeet TDT 0.6B v3 by NVIDIA, used under CC BY 4.0. Converted to ONNX by istupakov."
)

PARAKEET = ModelSpec(
    id="parakeet-tdt-0.6b-v3",
    display="Parakeet",
    repo="istupakov/parakeet-tdt-0.6b-v3-onnx",
    revision="8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce",
    size_bytes=670_480_039,
    blurb=(
        "CPU recognition for 25 European languages. Select the input language "
        "when translating; automatic language detection is unavailable."
    ),
    languages="european",
    publisher="NVIDIA",
    licence="CC-BY-4.0",
    attribution=_PARAKEET_ATTRIBUTION,
    precision="int8",
    provider="onnx-asr",
    # Named exactly, not "*.onnx": the repository also carries a 2.4 GB
    # full-precision encoder that a wildcard would happily fetch alongside the
    # 670 MB we actually run.
    allow_patterns=(
        "encoder-model.int8.onnx",
        "decoder_joint-model.int8.onnx",
        "nemo128.onnx",
        "vocab.txt",
        "config.json",
    ),
    weights_globs=("*.int8.onnx",),
    reports_confidence=False,
    detects_language=False,
)


#: Ordered fastest to most accurate, which is the order the catalogue shows.
_SPECS: tuple[ModelSpec, ...] = (
    _whisper(
        "tiny",
        "Whisper tiny",
        "d90ca5fe260221311c53c58e660288d3deb8d356",
        78_203_619,
        "The floor: runs where anything larger falls behind.",
    ),
    _whisper(
        "tiny.en",
        "Whisper tiny (English)",
        "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
        78_090_594,
        "A quarter fewer mistakes than tiny on English, for the same size.",
        languages=ENGLISH,
    ),
    _whisper(
        "base",
        "Whisper base",
        "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        147_882_941,
        "Keeps up with live speech on any computer, including old laptops.",
        tier_eligible=True,
    ),
    _whisper(
        "base.en",
        "Whisper base (English)",
        "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
        147_769_510,
        "A sixth fewer mistakes than base on English, at exactly the same size.",
        languages=ENGLISH,
    ),
    _whisper(
        "small",
        "Whisper small",
        "536b0662742c02347bc0e980a01041f333bce120",
        486_212_372,
        "The best accuracy that still keeps up without a graphics card.",
        tier_eligible=True,
    ),
    _whisper(
        "small.en",
        "Whisper small (English)",
        "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
        486_098_798,
        "Slightly better English than small, at the cost of every other language.",
        languages=ENGLISH,
    ),
    PARAKEET,
    _whisper(
        "large-v3-turbo",
        "Whisper large-v3-turbo",
        "846f74797293e5f605d509a6ab5395a1f2f2874e",
        817_887_676,
        "Large-model accuracy on accents and noise, at a third of the download.",
        repo="Zoont/faster-whisper-large-v3-turbo-int8-ct2",
        precision="int8",
        tier_eligible=True,
    ),
    _whisper(
        "large-v3",
        "Whisper large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        3_090_835_702,
        "Best accuracy overall, and the strongest on Chinese, Japanese and Korean.",
        tier_eligible=True,
    ),
    _whisper(
        "large-v2",
        "Whisper large-v2",
        "f0fe81560cb8b68660e564f55dd99207059c092e",
        3_089_578_858,
        "An alternative to large-v3 for audio where that model repeats itself.",
    ),
)

#: Models an existing settings file may still name. Resolvable so nobody's
#: explicit choice breaks under them, but no longer offered: every one is
#: beaten by something in the table above on size, speed and accuracy at once.
_RETIRED: tuple[ModelSpec, ...] = (
    _whisper("medium", "Whisper medium", "main", 1_530_000_000, "", listed=False),
    _whisper(
        "medium.en",
        "Whisper medium (English)",
        "main",
        1_530_000_000,
        "",
        languages=ENGLISH,
        listed=False,
    ),
    _whisper("large-v1", "Whisper large-v1", "main", 3_090_000_000, "", listed=False),
    _whisper(
        "distil-small.en",
        "Distil-Whisper small",
        "main",
        332_000_000,
        "",
        repo="Systran/faster-distil-whisper-small.en",
        languages=ENGLISH,
        listed=False,
    ),
    _whisper(
        "distil-medium.en",
        "Distil-Whisper medium",
        "main",
        789_000_000,
        "",
        repo="Systran/faster-distil-whisper-medium.en",
        languages=ENGLISH,
        listed=False,
    ),
    _whisper(
        "distil-large-v2",
        "Distil-Whisper large-v2",
        "main",
        1_510_000_000,
        "",
        repo="Systran/faster-distil-whisper-large-v2",
        listed=False,
    ),
    _whisper(
        "distil-large-v3",
        "Distil-Whisper large-v3",
        "main",
        1_510_000_000,
        "",
        repo="Systran/faster-distil-whisper-large-v3",
        listed=False,
    ),
    # Aliases people type into the override field.
    _whisper(
        "turbo",
        "Whisper large-v3-turbo",
        "846f74797293e5f605d509a6ab5395a1f2f2874e",
        817_887_676,
        "",
        repo="Zoont/faster-whisper-large-v3-turbo-int8-ct2",
        precision="int8",
        listed=False,
    ),
    _whisper(
        "large",
        "Whisper large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        3_090_835_702,
        "",
        repo="Systran/faster-whisper-large-v3",
        listed=False,
    ),
)

_BY_ID: dict[str, ModelSpec] = {spec.id: spec for spec in (*_SPECS, *_RETIRED)}

#: The size quoted for a model nobody has a row for. Deliberately large: an
#: under-estimate turns the disk-space check into a download that runs out
#: halfway.
UNKNOWN_SIZE_BYTES = 3_200_000_000


def all_specs() -> list[ModelSpec]:
    """Everything the catalogue offers, fastest first."""
    return [spec for spec in _SPECS if spec.listed]


def spec(model_id: str) -> ModelSpec | None:
    """The row for a model id, or None for a repo id or directory path."""
    return _BY_ID.get(model_id.strip())


def spec_or_guess(model_id: str) -> ModelSpec:
    """A usable spec for anything, including a repo the user typed themselves."""
    name = model_id.strip()
    known = _BY_ID.get(name)
    if known is not None:
        return known
    return ModelSpec(
        id=name,
        display=name,
        repo=name if "/" in name else f"Systran/faster-whisper-{name}",
        revision="main",
        size_bytes=UNKNOWN_SIZE_BYTES,
        blurb="",
        publisher="",
        licence="",
    )


def english_variant(model_id: str) -> str:
    """Use the English-specialized tiny/base models for explicit English input.

    Automatic language detection always keeps a multilingual model.
    """
    if model_id in ("tiny", "base"):
        candidate = f"{model_id}.en"
        if candidate in _BY_ID:
            return candidate
    return model_id


def attributions() -> list[tuple[str, str]]:
    """(display, attribution) for every model whose licence demands credit."""
    return [(s.display, s.attribution) for s in _SPECS if s.attribution]
