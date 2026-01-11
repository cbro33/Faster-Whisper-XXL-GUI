
APP_VERSION = "1.11.1"
SUPPORTED_EXTENSIONS = ('.mp3', '.wav', '.mp4', '.m4a', '.flac', '.aac', '.ogg', '.webm', '.mkv', '.avi', '.mov')
YTDLP_UPDATE_FAILURE_COOLDOWN_HOURS = 6
YTDLP_DEBUG_LOG_NAME = "ytdlp_update_debug.log"
EXTERNAL_MODULE_DIR_NAME = "python_modules"
PYTHON_PROBE_LOG_MAX = 25

PYTHON_INFO_SCRIPT = """import json
import sys
import importlib.util

info = {
    'version': sys.version.split()[0],
    'executable': sys.executable,
    'has_pip': importlib.util.find_spec("pip") is not None,
    'can_bootstrap_pip': False
}

try:
    import ensurepip
    info['can_bootstrap_pip'] = True
except Exception:
    pass

print(json.dumps(info))
"""
