"""Erzeugt build/pager.ico aus resources/pager.svg (für die Windows-EXE)."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "pager" / "resources" / "pager.svg"
OUT = ROOT / "build" / "pager.ico"


def main() -> int:
    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SRC))
    image = QImage(256, 256, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    OUT.parent.mkdir(exist_ok=True)
    if not image.save(str(OUT), "ICO"):
        print("ICO konnte nicht geschrieben werden", file=sys.stderr)
        return 1
    print(f"Icon geschrieben: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
