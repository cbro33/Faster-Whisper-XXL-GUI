
APP_VERSION = "1.17.2"

# Sent on every outbound request. A bare "python-requests/x.y" User-Agent from an
# unsigned executable is a mild antivirus heuristic trigger, and both GitHub and
# Hugging Face ask clients to identify themselves.
APP_USER_AGENT = (
    f"Faster-Whisper-XXL-GUI/{APP_VERSION} "
    "(+https://github.com/cbro33/Faster-Whisper-XXL-GUI)"
)
HTTP_HEADERS = {"User-Agent": APP_USER_AGENT}
# The engine archive is downloaded and its contents are then executed, and
# upstream publishes no checksum of its own. GitHub release assets can be
# replaced in place without the URL changing, so the hash is pinned here where
# it is covered by this repository rather than by the download server. Update
# both values together when moving to a newer upstream release.
ENGINE_ARCHIVE_BASE = (
    "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/"
)
ENGINE_ARCHIVES = {
    "windows": {
        "url": ENGINE_ARCHIVE_BASE + "Faster-Whisper-XXL_r245.4_windows.7z",
        "sha256": "237dee23939cdabfc96ef859fc5e584b842c3a5557e0d2ca744e1f87c14c5844",
        "size": 1424256246,
    },
    "linux": {
        "url": ENGINE_ARCHIVE_BASE + "Faster-Whisper-XXL_r245.4_linux.7z",
        "sha256": "510ee48ed73a7d4779fa8a7531437513ae109a76d934e983cbdaea3fc248c4f4",
        "size": 1657690937,
    },
}

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
