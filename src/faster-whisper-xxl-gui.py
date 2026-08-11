import sys
import os
import json
import logging
import traceback
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


def _selftest_themes(app, window):
    """Apply every theme and check the stylesheet actually landed.

    apply_theme falls back to an empty stylesheet and only logs when a .qss is
    missing, so calling it proves nothing on its own. A packaged build that
    failed to bundle `resources` would pass otherwise.
    """
    original = window.settings.get("theme", "light")
    for theme in ("Light", "Dark", "AMOLED"):
        window.apply_theme(theme)
        if not window.styleSheet():
            raise RuntimeError(f"{theme} theme produced an empty stylesheet")
        app.processEvents()
    window.apply_theme(original.capitalize())


def _selftest_dialogs(app, window):
    """Build every dialog a user can open.

    These are constructed on demand, so nothing above covers them, and a Qt
    plugin missing from the bundle would surface here first. They are only
    constructed, never exec'd, so none of them block.
    """
    import tempfile

    from model_manager import ModelManagerDialog
    from gui_components import (
        AudioPreprocessDialog,
        ConverterProgressDialog,
        HardwareOptimizationDialog,
        LoudnessProgressDialog,
        ModelDownloadDialog,
        UpdateProgressDialog,
    )

    # HardwareDetectionDialog is left out on purpose: it starts real hardware
    # detection on a worker thread from its constructor, which would make this
    # slow and flaky for no extra coverage over the dialog below.
    with tempfile.TemporaryDirectory() as tmp:
        dialogs = [
            ModelManagerDialog(window),
            UpdateProgressDialog(window),
            ConverterProgressDialog(window),
            ModelDownloadDialog("tiny", tmp, window),
            LoudnessProgressDialog(1, window),
            AudioPreprocessDialog(window),
        ]
        # Both branches, since a CI runner has no GPU and would only ever build
        # the second one.
        for has_cuda in (True, False):
            dialogs.append(HardwareOptimizationDialog(window, {
                "has_cuda": has_cuda,
                "gpu_name": "Selftest GPU",
                "gpu_memory_gb": 8.0,
                "ram_gb": 16.0,
                "cpu_cores": 8,
                "detection_method": "selftest",
            }))
        app.processEvents()

    return len(dialogs)


def _run_selftest():
    """Headless startup check used by CI. Exits 0 if the build is sound.

    A packaged build can be broken in ways source runs never show: a PyInstaller
    `excludes` entry that removed something still needed, or a missing Qt plugin.
    Both surface as a crash on the user's machine. This builds the real main
    window offscreen so the packaged app is exercised end to end, without
    needing a display or the Faster Whisper XXL binary.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # Startup work that needs the network or shows a dialog would block a
    # non-interactive run, and none of it tells us whether the bundle is intact.
    for name in ("check_hardware_optimization", "check_yt_dlp_version", "check_app_update"):
        setattr(WhisperGUI, name, lambda self: None)
    WhisperGUI.check_and_setup_dependencies = lambda self: True

    app = QApplication(sys.argv)
    window = WhisperGUI()
    window.resize(1250, 820)
    window.show()
    app.processEvents()

    if not window.tabs.count():
        raise RuntimeError("no tabs were created")
    if not window._required_tab_bar_width():
        raise RuntimeError("tab bar could not be measured")

    # Only reached in frozen builds, so nothing else covers these.
    sys.frozen = True
    window._apply_tab_minimums()
    window._enforce_splitter_sizes_for_frozen()
    window.resize(1300, 840)
    app.processEvents()

    _selftest_themes(app, window)
    dialogs = _selftest_dialogs(app, window)

    # A windowed build has no stdout, so this line is the only thing CI sees.
    # Counting the dialogs here is what shows that coverage actually ran.
    message = (f"selftest OK: {window.tabs.count()} tabs, {dialogs} dialogs, "
               f"{window.width()}x{window.height()}")
    logging.info(message)
    print(message)
    # console=False builds have no stdout, so leave a file CI can read too.
    try:
        with open(os.path.join(get_app_directory(), "selftest.log"), "w", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass
    return 0


def main():
    if "--selftest" in sys.argv:
        try:
            sys.exit(_run_selftest())
        except Exception:
            logging.exception("selftest failed")
            traceback.print_exc()
            try:
                with open(os.path.join(get_app_directory(), "selftest.log"), "w", encoding="utf-8") as handle:
                    handle.write("selftest FAILED\n" + traceback.format_exc())
            except Exception:
                pass
            sys.exit(1)

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
