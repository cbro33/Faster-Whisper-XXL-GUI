import sys
import os
import json
import logging
import faulthandler
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from utils import get_app_directory, get_portable_settings_directory

def _debug_logs_enabled():
    try:
        settings_dir = get_portable_settings_directory()
        settings_path = os.path.join(settings_dir, "settings.json")
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8") as handle:
                settings = json.load(handle)
            return bool(settings.get("debug_model_download_logging", False))
    except Exception:
        pass
    return False

def _configure_logging():
    handlers = [logging.StreamHandler()]
    if _debug_logs_enabled():
        log_dir = os.path.join(get_app_directory(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "app.log")
        crash_path = os.path.join(log_dir, "crash.log")
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        crash_handle = open(crash_path, "a", buffering=1, encoding="utf-8")
        faulthandler.enable(crash_handle)

        def _log_uncaught_exception(exc_type, exc, tb):
            logging.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
            try:
                sys.__excepthook__(exc_type, exc, tb)
            except Exception:
                pass

        sys.excepthook = _log_uncaught_exception
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers
    )

_configure_logging()

from gui_main import WhisperGUI

def main():
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    
    window = WhisperGUI()
    
    if window.executable_path:
        window.show()
        sys.exit(app.exec())
    else:
        logging.info("Exiting application because dependencies are not met.")
        sys.exit(0)


if __name__ == '__main__':
    main()
