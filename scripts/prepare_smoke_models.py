"""Fetch pinned Whisper/Parakeet models and a pack for frozen release checks."""

import os
import sys
import threading
from pathlib import Path

os.environ.pop("HF_HUB_OFFLINE", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from subbyai.asr.models import ModelManager
from subbyai.translation.builtin import ArgosProvider

manager = ModelManager()
for model in ("tiny", "parakeet-tdt-0.6b-v3"):
    if not manager.download(
        model, cancel=threading.Event(), progress=lambda update: print(update.message, flush=True)
    ):
        raise SystemExit(f"Could not prepare the {model} fixture.")
provider = ArgosProvider()
provider.prepare("en", "fr")
provider.close()
print("Real recognition and translation fixtures are ready.")
