"""Native/offscreen onboarding render probe with isolated settings."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from subbyai.asr.capability import MachineCapability
from subbyai.core.settings import Settings
from subbyai.ui import theme
from subbyai.ui.onboarding import OnboardingWizard

app = QApplication([])
theme.apply(app, "dark", "sakura", reduce_motion=True)
wizard = OnboardingWizard(Settings(), capability=MachineCapability(
    has_cuda=False, cpu_cores=8, ram_gb=16, free_disk_gb=100, platform="win32"))
wizard._show_step(len(wizard._steps) - 1)
wizard.show()

def capture():
    folder = Path(__file__).resolve().parents[1] / "build" / "validation"
    folder.mkdir(parents=True, exist_ok=True)
    wizard.grab().save(str(folder / "onboarding.png"))
    print("wizard", wizard.size(), "primary", wizard._primary.geometry(),
          "hint", wizard._primary.sizeHint(), flush=True)
    if "--capture" in sys.argv:
        app.quit()

QTimer.singleShot(1000, capture)
app.exec()
