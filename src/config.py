
APP_VERSION = "1.17.2"

# Sent on every outbound request. A bare "python-requests/x.y" User-Agent from an
# unsigned executable is a mild antivirus heuristic trigger, and both GitHub and
# Hugging Face ask clients to identify themselves.
APP_USER_AGENT = (
    f"Faster-Whisper-XXL-GUI/{APP_VERSION} "
    "(+https://github.com/cbro33/Faster-Whisper-XXL-GUI)"
)
HTTP_HEADERS = {"User-Agent": APP_USER_AGENT}
SUPPORTED_EXTENSIONS = ('.mp3', '.wav', '.mp4', '.m4a', '.flac', '.aac', '.ogg', '.webm', '.mkv', '.avi', '.mov')
YTDLP_UPDATE_FAILURE_COOLDOWN_HOURS = 6
YTDLP_DEBUG_LOG_NAME = "ytdlp_update_debug.log"
EXTERNAL_MODULE_DIR_NAME = "python_modules"
PYTHON_PROBE_LOG_MAX = 25
CONVERTER_BUNDLE_REPO = "cbro33/Faster-Whisper-XXL-GUI"
CONVERTER_BUNDLE_TAG = "converter-bundle"
CONVERTER_BUNDLE_ASSET = "fwxxl-converter-win-x64.zip"
CONVERTER_BUNDLE_SHA256_ASSET = "fwxxl-converter-win-x64.sha256"
CONVERTER_BUNDLE_DIR_NAME = "converter_bundle"

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
