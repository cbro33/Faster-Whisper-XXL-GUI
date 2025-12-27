import sys
import os
import logging
import subprocess
from PyQt6.QtCore import Qt

def get_app_directory():
    """ Get the application's base directory consistently, whether running from source or as executable """
    try:
        # Check if running as PyInstaller executable
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller executable - use directory where .exe is located
            return os.path.dirname(sys.executable)
        else:
            # Running from source - use parent directory of src/
            script_dir = os.path.dirname(os.path.abspath(__file__))
            return os.path.dirname(script_dir)  # Go up one level from src/
    except Exception as e:
        # Fallback to current working directory if all else fails
        print(f"Warning: Could not determine app directory: {e}, using current working directory")
        return os.getcwd()


def get_settings_directory():
    """ Get a writable, persistent directory for settings across all execution modes """
    try:
        if sys.platform == "win32":
            # Windows: Use %APPDATA%\FasterWhisperXXL\
            appdata = os.environ.get('APPDATA')
            if appdata:
                settings_dir = os.path.join(appdata, "FasterWhisperXXL")
            else:
                # Fallback to user profile
                settings_dir = os.path.join(os.path.expanduser("~"), ".faster-whisper-xxl")
        else:
            # Linux/Mac: Use ~/.faster-whisper-xxl/
            settings_dir = os.path.join(os.path.expanduser("~"), ".faster-whisper-xxl")
        
        # Create directory if it doesn't exist
        os.makedirs(settings_dir, exist_ok=True)
        return settings_dir
    except Exception as e:
        logging.warning(f"Could not create settings directory: {e}, using app directory")
        # Final fallback to app directory (if writable)
        return get_app_directory()


def get_portable_settings_directory():
    """ Get settings directory that stays with the application (portable) """
    try:
        if getattr(sys, 'frozen', False):
            # Running as exe - use same directory as .exe
            return os.path.dirname(sys.executable)
        else:
            # Running from source - use src/ directory
            return os.path.dirname(os.path.abspath(__file__))
    except Exception as e:
        logging.warning(f"Could not determine portable settings directory: {e}")
        return os.getcwd()


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
        # In PyInstaller, resources are in a 'resources' subdirectory
        return os.path.join(base_path, "resources", relative_path)
    except AttributeError:
        # Fallback for normal execution
        # When running from src/, resources are in the parent directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base_path = os.path.dirname(script_dir)  # Go up one level from src/
        return os.path.join(base_path, "resources", relative_path)

def _apply_windows_hidden_process_kwargs(kwargs):
    if os.name != "nt":
        return kwargs
    kw = dict(kwargs)
    startupinfo = kw.get("startupinfo")
    startinfo_class = getattr(subprocess, "STARTUPINFO", None)
    if startinfo_class and startupinfo is None:
        startupinfo = startinfo_class()
    if startupinfo is not None:
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kw["startupinfo"] = startupinfo
    kw["creationflags"] = kw.get("creationflags", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw

def run_hidden_subprocess(command, **kwargs):
    merged_kwargs = _apply_windows_hidden_process_kwargs(kwargs)
    return subprocess.run(command, **merged_kwargs)

def popen_hidden_subprocess(command, **kwargs):
    merged_kwargs = _apply_windows_hidden_process_kwargs(kwargs)
    return subprocess.Popen(command, **merged_kwargs)

def format_path_for_display(path):
    if not path:
        return ""
    try:
        normalized = os.path.abspath(path)
    except Exception:
        normalized = str(path)
    return normalized.replace("\\", "/")

def normalize_path_signature(path):
    if not path:
        return None
    try:
        normalized = os.path.realpath(os.path.abspath(path))
    except Exception:
        normalized = str(path)
    normalized = normalized.replace("\\", "/").lower()
    return normalized

def get_window_stays_on_top_flag():
    """ Get WindowStaysOnTopHint flag with PyQt6 version compatibility """
    try:
        # Try newer PyQt6 style
        return Qt.WindowType.WindowStaysOnTopHint
    except AttributeError:
        try:
            # Try original style  
            return Qt.WindowFlag.WindowStaysOnTopHint
        except AttributeError:
            try:
                # Try direct access
                return Qt.WindowStaysOnTopHint
            except AttributeError:
                try:
                    # Try explicit import
                    from PyQt6.QtCore import Qt as QtCore
                    return QtCore.WindowStaysOnTopHint
                except (AttributeError, ImportError):
                    # Fallback - return None to skip the flag
                    logging.warning("Could not find WindowStaysOnTopHint flag, dialogs won't stay on top")
                    return None

def detect_faster_whisper_binary_version(executable_path):
    """Best-effort detection of the bundled faster-whisper executable version"""
    if not executable_path or not os.path.exists(executable_path):
        return None
    candidate_commands = [[executable_path, "--version"], [executable_path, "-V"]]
    for command in candidate_commands:
        try:
            result = run_hidden_subprocess(command, capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            output = (result.stdout or result.stderr or "").strip()
            if output:
                return output.splitlines()[0]
    return None