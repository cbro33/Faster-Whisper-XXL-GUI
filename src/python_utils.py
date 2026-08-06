import sys
import os
import importlib.util
import json
import logging
import platform
import subprocess
import tempfile
from functools import lru_cache

from config import EXTERNAL_MODULE_DIR_NAME, PYTHON_INFO_SCRIPT, PYTHON_PROBE_LOG_MAX
from utils import get_settings_directory, get_app_directory, run_hidden_subprocess, normalize_path_signature

PYTHON_INFO_SCRIPT_PATH = None
PYTHON_PROBE_LOG = {}

def get_external_python_modules_path():
    """Return (and create) the directory where we can drop updated python packages"""
    try:
        settings_dir = get_settings_directory()
        external_dir = os.path.join(settings_dir, EXTERNAL_MODULE_DIR_NAME)
        os.makedirs(external_dir, exist_ok=True)
        return external_dir
    except Exception as e:
        logging.warning(f"Could not prepare external python module directory: {e}")
        return None

def get_executable_fallback_path():
    if getattr(sys, 'frozen', False):
        return sys.executable or os.path.abspath(sys.argv[0])
    return sys.executable

def get_python_info_script_path():
    global PYTHON_INFO_SCRIPT_PATH
    if PYTHON_INFO_SCRIPT_PATH and os.path.exists(PYTHON_INFO_SCRIPT_PATH):
        return PYTHON_INFO_SCRIPT_PATH
    try:
        fd, path = tempfile.mkstemp(prefix="fw_python_probe_", suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(PYTHON_INFO_SCRIPT)
        PYTHON_INFO_SCRIPT_PATH = path
    except Exception as exc:
        logging.error(f"Could not create python probe script: {exc}")
        raise
    return PYTHON_INFO_SCRIPT_PATH

def _log_python_probe(entry):
    try:
        command_key = tuple(entry.get("command", []))
        if command_key in PYTHON_PROBE_LOG:
            PYTHON_PROBE_LOG[command_key].update(entry)
        else:
            if len(PYTHON_PROBE_LOG) >= PYTHON_PROBE_LOG_MAX:
                oldest_key = next(iter(PYTHON_PROBE_LOG.keys()))
                PYTHON_PROBE_LOG.pop(oldest_key, None)
            PYTHON_PROBE_LOG[command_key] = entry
    except Exception:
        pass

def get_python_probe_log():
    return list(PYTHON_PROBE_LOG.values())

def _extract_json_line(raw_output):
    if not raw_output:
        return None
    lines = raw_output.strip().splitlines()
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None

def _probe_python_command(command_tokens):
    script_path = get_python_info_script_path()
    try:
        result = run_hidden_subprocess(
            command_tokens + [script_path],
            capture_output=True,
            text=True,
            timeout=10
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        _log_python_probe({
            "command": command_tokens,
            "status": "error",
            "detail": str(exc)
        })
        return None

    if result.returncode != 0:
        _log_python_probe({
            "command": command_tokens,
            "status": "error",
            "detail": result.stderr or result.stdout or f"Exit code {result.returncode}"
        })
        return None

    info = _extract_json_line(result.stdout or result.stderr)
    if not info:
        _log_python_probe({
            "command": command_tokens,
            "status": "error",
            "detail": "Could not parse probe output"
        })
        return None

    _log_python_probe({
        "command": command_tokens,
        "status": "success",
        "detail": info.get("version")
    })

    return {
        "command": tuple(command_tokens),
        "display_name": " ".join(command_tokens),
        "version": info.get("version"),
        "executable": info.get("executable"),
        "has_pip": bool(info.get("has_pip")),
        "can_bootstrap_pip": bool(info.get("can_bootstrap_pip"))
    }

def _build_python_command_candidates():
    candidates = []
    env_override = os.environ.get("FWXXL_PYTHON")
    if env_override:
        candidates.append([env_override])

    # Allow users to drop a portable python next to the application
    possible_local = [
        os.path.join(get_app_directory(), "python.exe"),
        os.path.join(get_app_directory(), "python", "python.exe"),
        os.path.join(get_app_directory(), "python3"),
        os.path.join(get_app_directory(), "python", "python3"),
    ]
    for path in possible_local:
        if os.path.exists(path):
            candidates.append([path])

    if sys.platform.startswith("win"):
        candidates.extend(
            _build_windows_python_candidates()
        )
    else:
        candidates.extend([
            ["python3"],
            ["python"],
            ["python3.12"],
            ["python3.11"],
            ["python3.10"]
        ])

    unique = []
    seen = set()
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cmd)
    return unique

def _build_windows_python_candidates():
    base_candidates = [
        ["py", "-3"],
        ["py", "-3.12"],
        ["py", "-3.11"],
        ["py", "-3.10"],
        ["py"],
        ["python"],
        ["python3"]
    ]

    discovered_paths = set()

    for discovered in discover_python_via_py_launcher():
        discovered_paths.add(discovered)

    for discovered in discover_python_standard_locations():
        discovered_paths.add(discovered)

    for path in sorted(discovered_paths):
        base_candidates.append([path])

    return base_candidates

def discover_python_via_py_launcher():
    if os.name != "nt":
        return []
    paths = []
    try:
        result = run_hidden_subprocess(["py", "-0p"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            _log_python_probe({
                "command": ["py", "-0p"],
                "status": "error",
                "detail": result.stderr or result.stdout
            })
            return []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Installed Pythons"):
                continue
            if line.startswith("-"):
                parts = line.split()
                potential_path = parts[-1]
                if potential_path.lower().endswith("python.exe") and os.path.exists(potential_path):
                    paths.append(potential_path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log_python_probe({
            "command": ["py", "-0p"],
            "status": "exception",
            "detail": str(exc)
        })
    return paths

def discover_python_standard_locations():
    if os.name != "nt":
        return []
    potential_paths = []
    env_vars = {
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
        "PROGRAMFILES": os.environ.get("PROGRAMFILES"),
        "PROGRAMFILES(X86)": os.environ.get("PROGRAMFILES(X86)"),
    }

    search_roots = []
    for key, base in env_vars.items():
        if not base:
            continue
        search_roots.append(os.path.join(base, "Programs", "Python"))
        search_roots.append(os.path.join(base, "Python"))

    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                full_path = os.path.join(root, entry, "python.exe")
                if os.path.exists(full_path):
                    potential_paths.append(full_path)
        except PermissionError:
            continue

    system_root = os.environ.get("SystemRoot")
    if system_root:
        for sub in ("py.exe", os.path.join("System32", "py.exe")):
            candidate = os.path.join(system_root, sub)
            if os.path.exists(candidate):
                potential_paths.append(candidate)

    return potential_paths

@lru_cache(maxsize=1)
def detect_python_runtime():
    """Detect a usable Python interpreter (for frozen builds)"""
    candidates = _build_python_command_candidates()
    detected = []
    for candidate in candidates:
        info = _probe_python_command(candidate)
        if not info:
            continue
        detected.append(info)
        if info.get("has_pip"):
            return info
    return detected[0] if detected else None

def refresh_python_detection_cache():
    try:
        detect_python_runtime.cache_clear()
    except AttributeError:
        pass

def get_current_python_runtime_info():
    try:
        has_pip = importlib.util.find_spec("pip") is not None
    except Exception:
        has_pip = False
    if getattr(sys, 'frozen', False):
        display = "Bundled Interpreter"
    else:
        display = sys.executable or "current_python"
    executable_path = get_executable_fallback_path()
    return {
        "command": (display or "" ,),
        "display_name": display,
        "version": platform.python_version(),
        "executable": executable_path,
        "has_pip": has_pip,
        "can_bootstrap_pip": True
    }

def enumerate_python_runtimes():
    """Return every Python interpreter we can successfully probe"""
    runtimes = [get_current_python_runtime_info()]
    for candidate in _build_python_command_candidates():
        info = _probe_python_command(candidate)
        if info:
            runtimes.append(info)
    aggregated = {}
    order = []
    for runtime in runtimes:
        executable = runtime.get("executable") or runtime.get("display_name") or "unknown"
        normalized = normalize_path_signature(executable)
        if not normalized:
            normalized = executable.lower()
        entry = aggregated.get(normalized)
        if not entry:
            entry = runtime.copy()
            entry["commands"] = set()
            aggregated[normalized] = entry
            order.append(normalized)
        command_label = build_command_label(runtime.get("command"))
        if command_label:
            entry["commands"].add(command_label)
        elif runtime.get("display_name"):
            entry["commands"].add(runtime.get("display_name"))
        if not entry.get("executable") and runtime.get("executable"):
            entry["executable"] = runtime["executable"]
        if not entry.get("version") and runtime.get("version"):
            entry["version"] = runtime["version"]
        if runtime.get("has_pip"):
            entry["has_pip"] = True
        if runtime.get("can_bootstrap_pip"):
            entry["can_bootstrap_pip"] = True
    deduped = []
    for key in order:
        entry = aggregated[key]
        entry["commands"] = sorted(cmd for cmd in entry["commands"] if cmd)
        deduped.append(entry)
    return deduped

def build_command_label(command_tokens):
    if not command_tokens:
        return None
    first = command_tokens[0]
    if os.path.isabs(first):
        first = os.path.basename(first)
    label = first
    if len(command_tokens) > 1:
        label += f" {command_tokens[1]}"
    return label

def get_execution_environment():
    """ Detect how the application is running and what update capabilities are available """
    try:
        is_frozen = getattr(sys, 'frozen', False)
        
        if not is_frozen:
            # Running from source - can always update
            return "source"
        
        python_info = detect_python_runtime()
        if python_info:
            return "exe_with_python"
        return "exe_no_python"
        
    except Exception as e:
        logging.warning(f"Could not determine execution environment: {e}")
        return "unknown"