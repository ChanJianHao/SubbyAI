# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for SubbyAI (run from the repository root):

    pyinstaller packaging/pyinstaller.spec --noconfirm

Produces dist/SubbyAI/ on Windows and "dist/SubbyAI.app" on macOS. onedir keeps
startup fast and makes updates diffable.
"""

import sys
import tomllib
from importlib import metadata
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent

# One source of truth for the version: pyproject.toml. branding.py keeps a
# literal in sync, and the Inno script takes it as a /D define at build time,
# so a release tag can never disagree with what the app reports about itself.
with open(ROOT / "pyproject.toml", "rb") as _f:
    VERSION = tomllib.load(_f)["project"]["version"]

datas = collect_data_files("faster_whisper")  # Silero VAD assets
datas += collect_data_files("onnx_asr")  # Parakeet's NumPy filter-bank tables
# onnx-asr reads its installed version at import time; include package metadata.
# direct_url.json is dropped: when a dependency is installed from a local
# file it records an absolute file:/// path from the build machine.
for src, dest in copy_metadata("onnx-asr"):
    datas += [
        (str(file), dest)
        for file in Path(src).iterdir()
        if file.is_file() and file.name != "direct_url.json"
    ]
datas += [(str(ROOT / name), ".") for name in ("LICENSE", "NOTICE.md")]
if (ROOT / "build" / "licenses").is_dir():
    datas += [(str(ROOT / "build" / "licenses"), "licenses")]
# Include resource files recursively without bytecode or build-machine paths.
_RESOURCES = ROOT / "src" / "subbyai" / "resources"
datas += [
    (
        str(item),
        str(Path("subbyai/resources").joinpath(item.parent.relative_to(_RESOURCES))),
    )
    for item in sorted(_RESOURCES.rglob("*"))
    if item.is_file()
    and "__pycache__" not in item.parts
    and item.suffix.lower() not in {".pyc", ".pyo"}
]

# Recognition and translation use CTranslate2 without model-conversion tools.
# Exclude unrelated ML frameworks while retaining CTranslate2's guarded imports.
# The frozen self-check validates the actual decoding paths.
_UNUSED_ML_STACK = [
    "torch",
    "torchvision",
    "torchaudio",
    "stanza",
    "spacy",
    "thinc",
    "blis",
    "transformers",
    "argostranslate.translate",
    "argostranslate.sbd",
]

icon_file = (
    str(ROOT / "src" / "subbyai" / "resources" / "icon.ico") if sys.platform == "win32" else None
)

# Directory enumeration in the runtime cannot make PyInstaller discover these
# optional CUDA wheels. Keep their native libraries in the same package layout.
gpu_binaries = []
for package in ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12"):
    try:
        distribution = metadata.distribution(package)
    except metadata.PackageNotFoundError:
        continue  # A deliberately CPU-only local build.
    for file in distribution.files or []:
        if file.name.lower().endswith(".dll") or ".so" in file.name:
            gpu_binaries.append((str(distribution.locate_file(file)), str(file.parent)))

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "build" / "freeze-input"), str(ROOT / "src")],
    datas=datas,
    binaries=gpu_binaries,
    hiddenimports=[
        "sentencepiece",
        # Imported lazily inside ParakeetEngine.load().
        "onnx_asr",
    ],
    excludes=["av", "tkinter", "matplotlib", "PIL", "IPython", "pytest", *_UNUSED_ML_STACK],
    noarchive=False,
)
# QtWidgets doesn't use PDF rendering, QML or the GPL-only virtual keyboard.
# Generic PyInstaller Qt hooks collect their plugins as optional dependencies.
_UNUSED_QT = (
    "qtpdf", "qt6pdf", "qtqml", "qt6qml", "qtquick", "qt6quick",
    "qtsvg", "qt6svg", "virtualkeyboard",
)
a.binaries = [
    item for item in a.binaries
    if not any(fragment in item[0].lower() for fragment in _UNUSED_QT)
    and Path(item[0]).stem.lower() not in {"qpdf", "libqpdf"}
    and ("imageformats" not in item[0].lower() or Path(item[0]).stem.lower() in {"qico", "libqico"})
]
a.datas = [item for item in a.datas if not any(part in item[0].lower() for part in _UNUSED_QT)]
# Qt 6.11 uses Windows' ICU API. A build machine may have Poppler's incompatible
# icuuc.dll on PATH; shipping it shadows the OS DLL and prevents QtCore loading.
# These system dependencies must be resolved from Windows, not developer tools.
if sys.platform == "win32":
    a.binaries = [
        item
        for item in a.binaries
        if Path(item[0]).name.lower() not in {"icuuc.dll", "icuin.dll"}
        and not Path(item[0]).name.lower().startswith("icudt")
    ]
pyz = PYZ(a.pure)

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )
    version_tuple = (*map(int, VERSION.split(".")), 0)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "SubbyAI contributors"),
                StringStruct("FileDescription", "SubbyAI live subtitles"),
                StringStruct("FileVersion", VERSION),
                StringStruct("ProductName", "SubbyAI"),
                StringStruct("ProductVersion", VERSION),
                StringStruct("OriginalFilename", "SubbyAI.exe"),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="SubbyAI",
    console=False,
    icon=icon_file,
    version=version_info,
)

coll = COLLECT(exe, a.binaries, a.datas, name="SubbyAI")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SubbyAI.app",
        icon=str(ROOT / "src" / "subbyai" / "resources" / "icon.icns"),
        bundle_identifier="io.github.chanjianhao.subbyai",
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "15.0",
            "LSUIElement": False,
            # Only requested if the user picks a microphone as the source.
            "NSMicrophoneUsageDescription": (
                "SubbyAI can caption a microphone if you choose one as the source. "
                "Local captions keep audio here; remote speech uploads only to your chosen server."
            ),
        },
    )
