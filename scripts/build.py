"""Baut PAGER als eigenständige Anwendung mit PyInstaller.

* **Windows:** ``dist/PAGER/PAGER.exe`` (Ordner) und mit ``--onefile`` zusätzlich ``dist/onefile/PAGER.exe``
* **macOS:**   ``dist/PAGER.app``

Mit ``--package`` werden die Ergebnisse für ein GitHub-Release nach ``release/`` gepackt
(``PAGER-<version>-windows.zip``, ``PAGER-<version>-windows.exe``, ``PAGER-<version>-macos.zip``).

    uv run python scripts/build.py [--onefile] [--package]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
RESOURCES = SRC / "pager" / "resources"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
ONEFILE = DIST / "onefile"

sys.path.insert(0, str(SRC))
from pager import APP_NAME, __version__  # noqa: E402

BUNDLE_ID = "io.github.ynkblw.pager"


def render_png(size: int, out: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    renderer = QSvgRenderer(str(RESOURCES / "pager.svg"))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(out)):
        raise SystemExit(f"Icon konnte nicht geschrieben werden: {out}")


def make_icon() -> Path:
    if sys.platform == "win32":
        ico = BUILD / "pager.ico"
        render_png(256, ico)  # Qt schreibt .ico anhand der Endung
        return ico
    if sys.platform == "darwin":
        iconset = BUILD / "pager.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        for size in (16, 32, 128, 256, 512):
            render_png(size, iconset / f"icon_{size}x{size}.png")
            render_png(size * 2, iconset / f"icon_{size}x{size}@2x.png")
        icns = BUILD / "pager.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
        return icns
    png = BUILD / "pager.png"
    render_png(256, png)
    return png


def pyinstaller(icon: Path, onefile: bool, dist: Path = DIST) -> None:
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME, "--icon", str(icon), "--paths", str(SRC),
        "--add-data", f"{RESOURCES}{sep}pager/resources",
        "--distpath", str(dist), "--workpath", str(BUILD / ("onefile" if onefile else "pyinstaller")), "--specpath", str(BUILD),
    ]
    if sys.platform == "darwin":
        cmd += ["--osx-bundle-identifier", BUNDLE_ID]
    if onefile:
        cmd += ["--onefile"]
    cmd.append(str(SRC / "pager" / "__main__.py"))
    subprocess.run(cmd, check=True)


def patch_plist() -> None:
    import plistlib

    path = DIST / f"{APP_NAME}.app" / "Contents" / "Info.plist"
    with path.open("rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHumanReadableCopyright": "PAGER",
    })
    with path.open("wb") as f:
        plistlib.dump(info, f)
    # Nach dem Ändern der Info.plist neu (ad hoc) signieren, sonst gilt das Bundle als beschädigt.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(path.parent.parent)], check=True)


def package() -> list[Path]:
    RELEASE.mkdir(exist_ok=True)
    out = []
    if sys.platform == "win32":
        z = RELEASE / f"{APP_NAME}-{__version__}-windows"
        shutil.make_archive(str(z), "zip", DIST, APP_NAME)  # Ordner PAGER/ mit PAGER.exe
        out.append(z.with_suffix(".zip"))
        single = ONEFILE / f"{APP_NAME}.exe"
        if single.is_file():
            target = RELEASE / f"{APP_NAME}-{__version__}-windows.exe"
            shutil.copy2(single, target)
            out.append(target)
    elif sys.platform == "darwin":
        z = RELEASE / f"{APP_NAME}-{__version__}-macos.zip"
        z.unlink(missing_ok=True)
        # ditto erhält Symlinks und Signatur des Bundles (anders als zip/shutil)
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(DIST / f"{APP_NAME}.app"), str(z)], check=True)
        out.append(z)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--onefile", action="store_true",
                        help="Windows: zusätzlich eine einzelne PAGER.exe bauen")
    parser.add_argument("--package", action="store_true", help="Ergebnisse nach release/ packen")
    args = parser.parse_args()

    shutil.rmtree(DIST, ignore_errors=True)
    icon = make_icon()
    pyinstaller(icon, onefile=False)
    if sys.platform == "darwin":
        patch_plist()
        # Den Hilfsordner dist/PAGER braucht nur Windows; auf macOS zählt das .app-Bundle.
        shutil.rmtree(DIST / APP_NAME, ignore_errors=True)
    if args.onefile and sys.platform == "win32":
        pyinstaller(icon, onefile=True, dist=ONEFILE)
    for path in package() if args.package else []:
        print(f"Paket: {path}")
    print(f"Fertig: {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
