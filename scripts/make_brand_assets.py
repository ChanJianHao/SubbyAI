"""Render repository and installer artwork from SubbyAI's original vector mark.

Uses the app's palette and installed UI font; no remote art or image metadata.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QPainter,
)

from subbyai.ui.mascot import draw_mochi  # noqa: E402
from subbyai.ui.tokens import DARK, LIGHT, UI_FONT_STACK  # noqa: E402


def text(painter, rect, value, size, color, *, bold=False, center=False):
    font = QFont()
    font.setFamilies(UI_FONT_STACK)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    painter.setFont(font)
    painter.setPen(QColor(color))
    align = Qt.AlignmentFlag.AlignCenter if center else Qt.AlignmentFlag.AlignLeft
    painter.drawText(QRectF(*rect), align | Qt.TextFlag.TextWordWrap, value)


def panel(painter, rect, color, radius):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(*rect), radius, radius)


def mark(painter, x, y, size, palette=DARK):
    painter.save()
    painter.translate(x, y)
    painter.scale(size / 100, size / 100)
    draw_mochi(painter, palette, "listening")
    painter.restore()


def hero():
    canvas = QImage(1600, 720, QImage.Format.Format_RGB32)
    canvas.fill(QColor(DARK.canvas))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    panel(painter, (1100, -110, 700, 950), DARK.surface, 350)
    panel(painter, (1240, 90, 300, 300), DARK.accent_subtle, 150)
    mark(painter, 1245, 105, 285)
    mark(painter, 65, 57, 76)
    text(painter, (155, 64, 560, 65), "SubbyAI", 47, DARK.text, bold=True)
    text(painter, (80, 193, 1040, 96), "Your world, subtitled.", 76, DARK.text, bold=True)
    text(painter, (85, 318, 890, 110),
         "Live subtitles & translation for anime, dramas, games and everything you play.",
         34, DARK.text_secondary)
    labels = (("Windows", 185), ("Local by default", 285), ("No account needed", 305))
    x = 85
    for label, width in labels:
        panel(painter, (x, 464, width, 58), DARK.raised, 29)
        text(painter, (x, 464, width, 58), label, 26, DARK.text_secondary, center=True)
        x += width + 16
    panel(painter, (1090, 433, 450, 167), DARK.raised, 24)
    text(painter, (1120, 463, 390, 40), "今日は、どんな物語を見よう？", 23,  # noqa: RUF001
         DARK.text_secondary, center=True)
    text(painter, (1120, 515, 390, 65), "What story shall we watch today?", 29,
         DARK.accent, bold=True, center=True)
    text(painter, (85, 619, 920, 42), "A little companion. A whole world of stories.", 27,
         DARK.text_tertiary)
    painter.end()
    return canvas


def installer():
    canvas = QImage(328, 628, QImage.Format.Format_RGB32)
    canvas.fill(QColor(LIGHT.canvas))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    mark(painter, 40, 65, 248, LIGHT)
    text(painter, (20, 355, 288, 65), "SubbyAI", 43, LIGHT.text, bold=True, center=True)
    text(painter, (32, 433, 264, 106), "Your world,\nsubtitled.", 30,
         LIGHT.text_secondary, center=True)
    painter.end()
    return canvas


def main():
    app = QGuiApplication([])
    if sys.platform == "win32":
        for name in ("segoeui.ttf", "segoeuib.ttf", "YuGothR.ttc"):
            QFontDatabase.addApplicationFont(
                str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name)
            )
    images = ROOT / "docs/images"
    images.mkdir(parents=True, exist_ok=True)
    artwork = hero()
    if not artwork.save(str(images / "hero.png")):
        raise RuntimeError("Could not save repository artwork")
    # A social preview has a different shape, so compose it deliberately.
    social = QImage(1280, 640, QImage.Format.Format_RGB32)
    social.fill(QColor(DARK.canvas))
    painter = QPainter(social)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    mark(painter, 965, 40, 240)
    text(painter, (65, 65, 860, 90), "SubbyAI", 65, DARK.text, bold=True)
    text(painter, (65, 233, 1110, 100), "Your world, subtitled.", 75, DARK.text, bold=True)
    text(painter, (70, 367, 1100, 100), "Live subtitles for anime, dramas, videos & games.",
         37, DARK.text_secondary)
    text(painter, (70, 537, 1100, 54), "Windows  ·  Local by default  ·  No account needed", 29,
         DARK.accent)
    painter.end()
    if not social.save(str(images / "social-preview.png")):
        raise RuntimeError("Could not save social artwork")
    wizard = installer()
    resources = ROOT / "packaging/windows"
    if not wizard.save(str(resources / "wizard.bmp"), "BMP"):
        raise RuntimeError("Could not save installer artwork")
    app.quit()
    print("Rendered Mochi repository, social preview and installer artwork.")


if __name__ == "__main__":
    main()
