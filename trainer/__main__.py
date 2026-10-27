"""Entry point: python -m trainer"""
import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .config import Settings, config_dir
from .devices.manager import DeviceManager
from .ui.main_window import MainWindow


def setup_logging() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(config_dir() / "trainer.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,  # a library adds a root handler on import, which would make basicConfig a no-op
    )


def main() -> int:
    setup_logging()
    logging.getLogger(__name__).info("Cycling Trainer %s starting", __version__)
    if sys.platform == "win32":
        # Own taskbar entry and icon, instead of being grouped under Python.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CyclingTrainer.App")
    app = QApplication(sys.argv)
    app.setApplicationName("Cycling Trainer")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources" / "icon.ico")))
    window = MainWindow(Settings.load(), DeviceManager())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
