import sys
import os
import logging
import re
import shutil
import subprocess
import shutil
from PyQt6.QtCore import Qt
from config import HTTP_HEADERS

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

def executable_word(capitalized=True):
    """The right word for a program file on this platform.

    Windows calls these EXEs; Linux and macOS do not, so the paths screen used
    to show "FFMPEG EXE:" to users who have no .exe files at all.
    """
    if sys.platform == "win32":
        return "EXE"
    return "Binary" if capitalized else "binary"


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

def resolve_ffmpeg_location():
    """Return ffmpeg executable path from bundled bin or PATH."""
    try:
        env_override = os.environ.get("FWHISPER_FFMPEG_PATH")
        if env_override and os.path.exists(env_override):
            return env_override
        app_dir = get_app_directory()
        bin_dir = os.path.join(app_dir, "bin")
        exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        bundled = os.path.join(bin_dir, exe_name)
        if os.path.exists(bundled):
            return bundled
        return shutil.which(exe_name)
    except Exception as exc:
        logging.debug(f"Could not resolve ffmpeg path: {exc}")
        return None


# ---------------------------------------------------------------------------
# Pure text / path utilities (extracted from gui_main.WhisperGUI)
# ---------------------------------------------------------------------------

def redact_path_text(text):
    """Replace file-system paths in *text* with ``<path>`` for safe display."""
    if not text:
        return text
    redacted = text
    redacted = re.sub(r'(["\'])([A-Za-z]:[\\/].*?)(\\1)', r'"\<path\>"', redacted)
    redacted = re.sub(r'(["\'])(\\\\.*?)(\\1)', r'"\<path\>"', redacted)
    redacted = re.sub(r'(["\'])(//.*?)(\\1)', r'"\<path\>"', redacted)
    redacted = re.sub(r'(["\'])(/.*?)(\\1)', r'"\<path\>"', redacted)
    redacted = re.sub(r"[A-Za-z]:[\\/][^\\s]+", "<path>", redacted)
    redacted = re.sub(r"\\\\[^\\s]+", "<path>", redacted)
    redacted = re.sub(r"//[^\\s]+", "<path>", redacted)
    redacted = re.sub(r"(?<![A-Za-z0-9])/(?:[^\\s]+)", "<path>", redacted)
    redacted = re.sub(r"<path>[^\\s]*", "<path>", redacted)
    return redacted


def looks_like_path_token(token):
    """Return True if *token* looks like a filesystem path."""
    if not token:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    if token.startswith("\\\\") or token.startswith("//"):
        return True
    if token.startswith("/"):
        return True
    return False


def sanitize_command_display(command_tokens, display_command=None):
    """Build a sanitized command string with paths redacted.

    *command_tokens* is a list of CLI tokens (may be ``None``).
    *display_command* is a fallback string representation.
    """
    if not command_tokens:
        return redact_path_text(display_command or "")
    safe_tokens = []
    for token in command_tokens:
        if token.startswith("-"):
            safe_tokens.append(token)
            continue
        if looks_like_path_token(token):
            safe_tokens.append("<path>")
        else:
            safe_tokens.append(redact_path_text(token))
    quoted_parts = []
    for token in safe_tokens:
        if " " in token or '"' in token or "'" in token:
            processed = token.replace('"', '\\"')
            quoted_parts.append(f'"{processed}"')
        else:
            quoted_parts.append(token)
    return " ".join(quoted_parts)


def is_windows_path(path):
    """Return True if *path* uses Windows drive-letter syntax."""
    return bool(re.match(r"^[A-Za-z]:\\\\", path))


def windows_to_posix_path(path):
    """Convert a Windows path like ``C:\\foo`` to ``/mnt/c/foo`` (WSL style)."""
    drive = path[0].lower()
    rest = path[2:].replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{rest}"


def sanitize_model_name(name):
    """Sanitize a model name to contain only safe characters."""
    if not name:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    cleaned = cleaned.strip("-._")
    return cleaned.lower()


def parse_hf_repo_id(value):
    """Parse a Hugging Face repo ID from a URL or ``owner/repo`` string.

    Returns the repo ID (``owner/repo``) or ``None`` if parsing fails.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        marker = "huggingface.co/"
        if marker not in text:
            return None
        text = text.split(marker, 1)[1]
    elif text.startswith("huggingface.co/"):
        text = text.split("huggingface.co/", 1)[1]
    text = text.split("?", 1)[0].split("#", 1)[0].strip("/")
    for marker in ("/blob/", "/tree/"):
        if marker in text:
            text = text.split(marker, 1)[0]
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return None
    repo_id = "/".join(parts[:2])
    if re.match(r"^[^/]+/[^/]+$", repo_id):
        return repo_id
    return None


def detect_model_arch_from_dir(model_path):
    """Detect whether a model directory contains CT2 or Transformers weights.

    Returns a dict with ``arch``, ``path``, ``model_dir`` keys, or ``None``.
    """
    if not model_path or not os.path.isdir(model_path):
        return None
    model_bin = os.path.join(model_path, "model.bin")
    if os.path.isfile(model_bin):
        return {
            "arch": "CT2",
            "path": model_bin,
            "model_dir": model_path,
        }
    config_path = os.path.join(model_path, "config.json")
    weight_files = []
    try:
        for filename in os.listdir(model_path):
            lowered = filename.lower()
            if lowered in ("model.safetensors", "pytorch_model.bin"):
                weight_files.append(os.path.join(model_path, filename))
            elif lowered.startswith("model-") and lowered.endswith(".safetensors"):
                weight_files.append(os.path.join(model_path, filename))
            elif lowered.startswith("pytorch_model-") and lowered.endswith(".bin"):
                weight_files.append(os.path.join(model_path, filename))
    except Exception:
        weight_files = []
    if os.path.isfile(config_path) or weight_files:
        return {
            "arch": "Transformers",
            "path": config_path if os.path.isfile(config_path) else (weight_files[0] if weight_files else model_path),
            "model_dir": model_path,
        }
    return None


def strip_terminal_escapes(data):
    """Strip ANSI escape sequences and OSC terminal title sequences from text."""
    data = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', data)
    data = re.sub(r'\]0;[^\[]*', '', data)
    data = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', data)
    data = re.sub(r'\[\d+(?:;\d+)*m', '', data)
    return data


def filter_verbose_output(data):
    """Filter transcription output to show only timestamp lines and completion messages."""
    data = strip_terminal_escapes(data)
    lines = data.split('\n')
    filtered_lines = []
    for line in lines:
        if re.match(
            r'^\[(?:\d+:\d+\.\d+|\d+:\d+:\d+\.\d+)\s*-->\s*(?:\d+:\d+\.\d+|\d+:\d+:\d+\.\d+)\]',
            line,
        ):
            filtered_lines.append(line)
        elif 'Subtitles are written to' in line:
            if not filtered_lines or filtered_lines[-1] != "":
                filtered_lines.append("")
            filtered_lines.append(line)
    return '\n'.join(filtered_lines) if filtered_lines else ''


def extract_links_from_text(text):
    """Extract whitespace-separated tokens from *text*, one per non-empty line token."""
    if not text:
        return []
    links = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        links.extend(token for token in re.split(r"\s+", line) if token)
    return links


def normalize_version(version_str):
    """Strip leading 'v'/'V' and whitespace from a version string."""
    if not version_str:
        return ""
    return version_str.strip().lstrip("vV")


def version_tuple(version_str):
    """Parse a version string into a tuple of ints for comparison."""
    try:
        return tuple(int(p) for p in re.findall(r"\d+", version_str))
    except Exception:
        return ()


def text_indicates_transcription_success(text):
    """Return True if *text* contains markers indicating transcription finished."""
    if not text:
        return False
    success_indicators = [
        "Operation finished in:",
        "Subtitles are written to",
        "Transcription speed:",
        "audio seconds/s",
    ]
    return any(indicator in text for indicator in success_indicators)


def find_7zip():
    """Find 7-Zip executable on the system. Returns path or None."""
    exe = shutil.which('7z') or shutil.which('7zr')
    if exe:
        return exe
    app_dir = get_app_directory()
    for name in ['7zr.exe', '7z.exe', '7zr', '7z']:
        local_path = os.path.join(app_dir, 'bin', name)
        if os.path.exists(local_path):
            return local_path
    if sys.platform == "win32":
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        for base in [prog_files, prog_files_x86]:
            path = os.path.join(base, "7-Zip", "7z.exe")
            if os.path.exists(path):
                return path
    return None


_7ZR_DOWNLOAD_URL = "https://github.com/ip7z/7zip/releases/download/26.01/7zr.exe"


def download_7zr_portable(dest_dir=None):
    """Download portable 7zr.exe to dest_dir (defaults to app bin/). Returns path or None."""
    import requests
    if dest_dir is None:
        dest_dir = os.path.join(get_app_directory(), 'bin')
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, '7zr.exe')
    try:
        logging.info(f"Downloading 7zr.exe to {dest_path}")
        resp = requests.get(_7ZR_DOWNLOAD_URL, timeout=30, headers=HTTP_HEADERS)
        resp.raise_for_status()
        with open(dest_path, 'wb') as f:
            f.write(resp.content)
        logging.info(f"Downloaded 7zr.exe ({len(resp.content)} bytes)")
        return dest_path
    except Exception as e:
        logging.error(f"Failed to download 7zr.exe: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None


def get_7zip_install_command():
    """Return a platform-appropriate command to install 7-Zip, or None."""
    if sys.platform == "win32":
        return None
    if shutil.which("pacman"):
        return "sudo pacman -S p7zip"
    if shutil.which("apt"):
        return "sudo apt install p7zip-full"
    if shutil.which("dnf"):
        return "sudo dnf install p7zip p7zip-plugins"
    if shutil.which("zypper"):
        return "sudo zypper install p7zip-full"
    if shutil.which("brew"):
        return "brew install p7zip"
    return None
