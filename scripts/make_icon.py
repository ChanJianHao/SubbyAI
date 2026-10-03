"""Generate original Mochi icons directly from the application's Qt vector art."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from subbyai.ui.mascot import draw_mochi  # noqa: E402
from subbyai.ui.tokens import DARK  # noqa: E402

OUT_DIR = ROOT / "src" / "subbyai" / "resources"
SIZES = [16, 20, 24, 32, 48, 64, 128, 256]


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 100, size / 100)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(DARK.canvas))
    painter.drawRoundedRect(QRectF(0, 0, 100, 100), 24, 24)
    draw_mochi(painter, DARK)
    painter.end()
    return image


def main() -> int:
    app = QGuiApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render(1024).save(str(OUT_DIR / "icon.png"), "PNG")
    payloads = []
    for size in SIZES:
        image = render(size)
        image.save(str(OUT_DIR / f"icon-{size}.png"), "PNG")
        raw = QByteArray()
        buffer = QBuffer(raw)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        payloads.append(bytes(raw))
    # Windows consumes actual small-size variants, not a resized 256px tile.
    offset = 6 + 16 * len(SIZES)
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    for size, data in zip(SIZES, payloads, strict=True):
        header += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (OUT_DIR / "icon.ico").write_bytes(header + b"".join(payloads))
    chunks = []
    for tag, size in ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128),
                      (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
        raw = QByteArray()
        buffer = QBuffer(raw)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        render(size).save(buffer, "PNG")
        data = bytes(raw)
        chunks.append(tag + struct.pack(">I", 8 + len(data)) + data)
    body = b"".join(chunks)
    (OUT_DIR / "icon.icns").write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
    app.quit()
    print(f"Wrote original Mochi icons in {len(SIZES)} Windows/tray sizes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
