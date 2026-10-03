# Notices and attribution

SubbyAI is MIT licensed. [LICENSE](LICENSE) retains required copyright notices.
The phrase list in `src/subbyai/resources/hallucinations.txt` includes material from
[System Captioner](https://github.com/evermoving/SystemCaptioner), used under MIT.

## Runtime and native dependencies

Release builds contain `LICENSE`, this notice and a `licenses` directory collected from the
actual installed runtime distributions. Its inventory records exact versions and upstream URLs;
this table is a navigation aid, not a replacement for those license texts.

| Component | Declared license / notice |
|---|---|
| PySide6 / Qt / shiboken6 | LGPL v3/GPL/commercial options; see shipped texts and used modules |
| faster-whisper, CTranslate2 | MIT |
| onnx-asr, ONNX Runtime | MIT, including native third-party notices |
| SentencePiece | Apache-2.0 |
| NumPy | BSD-3-Clause and bundled native-library notices |
| PyAudioWPatch / PortAudio (Windows), sounddevice (macOS) | MIT and native notices |
| HTTPX, httpcore, platformdirs, keyring, filelock | Upstream license texts in inventory |
| psutil | BSD-3-Clause |
| Hugging Face Hub, tokenizers | Apache-2.0 and transitive dependency notices |
| PyAV / FFmpeg | Source environment only; excluded from frozen distributions |
| Optional NVIDIA CUDA/cuDNN runtime wheels | NVIDIA redistribution/license terms, not MIT |
| Python runtime | Python and bundled component license texts |

Argos Translate's Python package, PyTorch, Stanza and spaCy are not required by the application
or bundled by the packager. The local translator reads the Argos pack format and runs CTranslate2
with SentencePiece directly. Installing unrelated tools into the build environment does not grant
permission to redistribute their content; the PyInstaller exclusions and inventory should be reviewed.

Qt is dynamically linked through unmodified, separate libraries. Users may replace compatible Qt
libraries in the portable application's `_internal` directory and rebuild from published source
using [development.md](docs/development.md). Redistribution must also provide the applicable
notices and corresponding Qt/PySide source or a compliant source offer for the exact versions.
Read [Qt's LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations); dynamic
linking alone does not establish complete compliance. Release acceptance includes this review.

## Models downloaded separately

Model weights and language packs are downloaded at the user's request and aren't included in
normal installer/portable distributions. Model revisions are pinned in the curated ASR catalog.
The development smoke fixture is also downloaded separately, never embedded in the installer.

| Model | Publisher / attribution |
|---|---|
| Whisper and SYSTRAN conversions | OpenAI / SYSTRAN, MIT |
| large-v3-turbo int8 conversion | OpenAI / Zoont, MIT; catalog links to the exact revision |
| Parakeet TDT 0.6B v3 ONNX | NVIDIA model under CC BY 4.0, converted by istupakov |
| Argos directional language packs | Argos Open Tech; license varies by pack |
| OPUS-MT inside many packs | Helsinki-NLP, commonly CC BY 4.0 |

**Parakeet attribution:** Parakeet TDT 0.6B v3 by NVIDIA, used under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), converted to ONNX by
[istupakov](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx). SubbyAI doesn't modify
these model weights. The app also shows attribution beside the model choice.

OPUS-MT models are developed by Helsinki-NLP; see the
[OPUS-MT project](https://github.com/Helsinki-NLP/Opus-MT). A pack's license is its own:
some upstream packs do not declare one. Check the precise pack before redistributing it.
No general permission is inferred for undocumented packs. Non-commercial NLLB models are not offered.

## Fonts and artwork

Mochi, the smiling speech-bubble mascot, and its generated application/tray icons are original
SubbyAI Qt artwork released with the project's MIT source. No existing anime character is used.
The release bundles no third-party typefaces. UI and overlay fonts resolve to fonts installed
on the user's computer; optional Inter, JetBrains Mono or Atkinson names in fallback lists do
not mean their font files are distributed. Documentation screenshots use fictional caption text.

## Data leaving the machine

Local speech and translation remain on the device. Model downloads contact their publishers.
Optional remote speech uploads audio only after explicit endpoint consent; optional text providers
receive caption/transcript text. There is no telemetry or automatic crash upload.
Read [privacy and security](docs/security.md) for the exact boundaries.
