"""Einstiegspunkt: ``python -m pager`` bzw. ``pager``."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from pager import APP_NAME, __version__
from pager.config import Settings
from pager.ui import theme
from pager.ui.main_window import MainWindow


def main() -> int:
    if sys.platform == "win32":
        # Eigenes Taskleisten-Icon statt python.exe
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"pager.app.{__version__}")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(theme.app_icon())
    theme.apply(app)

    window = MainWindow(Settings.load())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
