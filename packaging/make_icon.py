"""Draws the app icon (zone-coloured power blocks with a power line) into trainer/resources/icon.ico.

Run once after changing the design: python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

SIZE = 256
OUT = Path(__file__).resolve().parent.parent / "trainer" / "resources" / "icon.ico"


def draw() -> QImage:
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#14171c"))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 48, 48)

    # Workout blocks in power-zone colours, like the workout chart.
    blocks = [(40, 150, "#3d8bfd"), (82, 110, "#34c38f"), (124, 70, "#f7c948"), (166, 110, "#34c38f")]
    for x, top, color in blocks:
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(x, top, 38, 208 - top), 6, 6)

    # The rider's power line over the blocks.
    path = QPainterPath(QPointF(36, 160))
    for point in [(60, 150), (80, 118), (104, 114), (124, 78), (150, 74), (166, 108), (196, 112), (220, 104)]:
        path.lineTo(QPointF(*point))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#f4f6f8"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.end()
    return image


def main() -> None:
    QGuiApplication([])
    png = OUT.with_suffix(".png")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(str(png))
    Image.open(png).save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
