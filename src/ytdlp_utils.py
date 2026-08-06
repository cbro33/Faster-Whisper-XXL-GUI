import sys
import os
import json
import requests
import time
import logging
import re
import importlib.machinery
import importlib.util
import shutil
import subprocess

from config import APP_VERSION, YTDLP_DEBUG_LOG_NAME, EXTERNAL_MODULE_DIR_NAME, YTDLP_UPDATE_FAILURE_COOLDOWN_HOURS, HTTP_HEADERS
from utils import get_app_directory, get_settings_directory, get_portable_settings_directory, run_hidden_subprocess
from python_utils import (
    get_external_python_modules_path, detect_python_runtime, get_python_info_script_path,
    get_executable_fallback_path, refresh_python_detection_cache, get_execution_environment
)

# Global cache for yt-dlp module to avoid repeated imports
yt_dlp = None
yt_dlp_source_override = None
_YTDLP_DEBUG_ENABLED = False

def set_ytdlp_debug_logging_enabled(enabled):
    global _YTDLP_DEBUG_ENABLED
    _YTDLP_DEBUG_ENABLED = bool(enabled)

def get_ytdlp_debug_log_path():
    """Return path for yt-dlp update debug logging."""
    log_dir = os.path.join(get_app_directory(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, YTDLP_DEBUG_LOG_NAME)


def log_ytdlp_update_debug(message):
    """Append a debug entry for yt-dlp updates."""
    if not _YTDLP_DEBUG_ENABLED:
        return
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_path = get_ytdlp_debug_log_path()
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception as exc:
        logging.debug(f"Failed to write yt-dlp debug log: {exc}")


def find_external_yt_dlp_path():
    """Return the path of an updated yt-dlp package if installed externally"""
    external_modules_path = get_external_python_modules_path()
    if not external_modules_path:
        return None
    candidate = os.path.join(external_modules_path, "yt_dlp")
    if os.path.isdir(candidate):
        return candidate
    return None


def _normalize_path(path):
    try:
        return os.path.abspath(path).casefold()
    except Exception:
        return path


def is_module_in_path(module, target_path):
    if not module or not target_path:
        return False
    module_path = getattr(module, "__file__", None)
    if not module_path:
        return False
    return _normalize_path(module_path).startswith(_normalize_path(target_path))


def get_system_yt_dlp_info():
    """Return (version, package_dir) for system yt-dlp excluding app-managed modules."""
    try:
        external_modules_path = get_external_python_modules_path()
        external_path = _normalize_path(external_modules_path) if external_modules_path else None
        search_paths = []
        for path in sys.path:
            if not path:
                continue
            if external_path and _normalize_path(path) == external_path:
                continue
            search_paths.append(path)
        spec = importlib.machinery.PathFinder.find_spec("yt_dlp", search_paths)
        if not spec or not spec.origin:
            return None, None
        package_dir = os.path.dirname(spec.origin)
        return read_package_version(package_dir), package_dir
    except Exception as exc:
        logging.debug(f"Could not resolve system yt-dlp: {exc}")
        return None, None


def load_system_yt_dlp_module():
    """Import yt-dlp from system site-packages, excluding app-managed modules."""
    try:
        import importlib
        original_sys_path = list(sys.path)
        external_modules_path = get_external_python_modules_path()
        if external_modules_path:
            external_path = _normalize_path(external_modules_path)
            sys.path = [p for p in sys.path if _normalize_path(p) != external_path]
        clear_ytdlp_module_cache()
        module = importlib.import_module("yt_dlp")
        module.__fwhisper_source__ = "system"
        return module
    except Exception as exc:
        logging.warning(f"Could not load system yt-dlp: {exc}")
        return None
    finally:
        try:
            sys.path = original_sys_path
        except Exception:
            pass


def remove_external_yt_dlp(reason=None):
    """Remove a broken external yt-dlp installation to prevent repeat import errors."""
    external_path = find_external_yt_dlp_path()
    if not external_path or not os.path.isdir(external_path):
        return False
    try:
        shutil.rmtree(external_path)
        logging.warning(f"Removed external yt-dlp at {external_path}: {reason}")
        return True
    except Exception as exc:
        logging.warning(f"Failed to remove external yt-dlp at {external_path}: {exc}")
        return False



def read_package_version(package_dir):
    """Read __version__ from a package directory without importing it"""
    version_file = os.path.join(package_dir, "version.py")
    if not os.path.exists(version_file):
        return None
    try:
        with open(version_file, "r", encoding="utf-8") as handle:
            contents = handle.read()
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", contents)
        if match:
            return match.group(1).strip()
    except Exception as exc:
        logging.debug(f"Could not read package version from {version_file}: {exc}")
    return None


def get_ytdlp_version_safe(module):
    """Safely extract yt-dlp version without raising."""
    try:
        version = getattr(getattr(module, "version", None), "__version__", None)
    except Exception:
        version = None
    if not version:
        version = getattr(module, "__version__", None)
    if not version:
        module_file = getattr(module, "__file__", "")
        if module_file:
            version = read_package_version(os.path.dirname(module_file))
    return version


def parse_version_tuple(version_str):
    """Convert yt-dlp version string (YYYY.MM.DD) into a comparable tuple"""
    if not version_str:
        return ()
    try:
        parts = [int(p) for p in re.findall(r"\d+", version_str)]
        return tuple(parts)
    except Exception:
        return ()


def _load_module_from_path(module_name, package_dir):
    """Load a Python package from a specific directory"""
    init_path = os.path.join(package_dir, "__init__.py")
    if not os.path.exists(init_path):
        raise FileNotFoundError(f"{module_name} package does not contain __init__.py at {package_dir}")
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[package_dir]
    )
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception:
        if previous is not None:
            sys.modules[module_name] = previous
        else:
            sys.modules.pop(module_name, None)
        raise
    return module


def maybe_use_external_yt_dlp(bundled_module):
    """Prefer externally installed yt-dlp builds when available"""
    external_path = find_external_yt_dlp_path()
    if not external_path:
        if bundled_module:
            bundled_module.__fwhisper_source__ = "bundled" if getattr(sys, 'frozen', False) else "system"
        return bundled_module

    external_version = read_package_version(external_path)
    system_version, _ = get_system_yt_dlp_info()
    bundled_version = None
    if bundled_module is not None:
        bundled_version = getattr(getattr(bundled_module, "version", None), "__version__", None)

    try:
        external_tuple = parse_version_tuple(external_version)
        system_tuple = parse_version_tuple(system_version)
        if system_tuple and (not external_tuple or system_tuple > external_tuple):
            if bundled_module and not is_module_in_path(bundled_module, external_path):
                bundled_module.__fwhisper_source__ = "system"
                return bundled_module
            system_module = load_system_yt_dlp_module()
            if system_module:
                return system_module
        if (
            not bundled_version or not external_version or
            parse_version_tuple(external_version) >= parse_version_tuple(bundled_version)
        ):
            external_module = _load_module_from_path("yt_dlp", external_path)
            external_module.__fwhisper_source__ = "external"
            external_module.__fwhisper_source_path__ = external_path
            logging.info(f"Using external yt-dlp from {external_path} (version {external_version})")
            return external_module
    except Exception as exc:
        logging.warning(f"Failed to load external yt-dlp: {exc}")
        remove_external_yt_dlp(f"external_import_failed: {exc}")

    if bundled_module:
        bundled_module.__fwhisper_source__ = "bundled" if getattr(sys, 'frozen', False) else "system"
    return bundled_module


def clear_ytdlp_module_cache():
    """Remove cached yt-dlp modules to ensure a clean reload."""
    try:
        for key in list(sys.modules.keys()):
            if key == "yt_dlp" or key.startswith("yt_dlp."):
                sys.modules.pop(key, None)
        log_ytdlp_update_debug("Cleared yt-dlp module cache.")
    except Exception as exc:
        logging.warning(f"Could not clear yt-dlp module cache: {exc}")


def reload_external_yt_dlp_if_available():
    """Reload yt-dlp from the external directory after an update"""
    global yt_dlp
    external_path = find_external_yt_dlp_path()
    if not external_path:
        return None
    try:
        clear_ytdlp_module_cache()
        module = _load_module_from_path("yt_dlp", external_path)
        module.__fwhisper_source__ = "external"
        module.__fwhisper_source_path__ = external_path
        yt_dlp = module
        return module
    except Exception as exc:
        logging.warning(f"Could not reload yt-dlp from {external_path}: {exc}")
        return None

def refresh_yt_dlp_module_after_update():
    """Reload yt-dlp so the running session can use the updated build"""
    global yt_dlp
    if getattr(sys, 'frozen', False):
        return reload_external_yt_dlp_if_available()
    try:
        import importlib
        clear_ytdlp_module_cache()
        module = importlib.import_module("yt_dlp")
        module = maybe_use_external_yt_dlp(module)
        yt_dlp = module
        log_ytdlp_update_debug("Reloaded yt-dlp module after update.")
        return module
    except Exception as exc:
        logging.warning(f"Could not reload yt-dlp module after update: {exc}")
        log_ytdlp_update_debug(f"Reload failed: {exc}")
        return None

def select_yt_dlp_source(source):
    """Select which yt-dlp module to use: bundled or system."""
    global yt_dlp, yt_dlp_source_override
    yt_dlp_source_override = source
    if source == "system":
        module = load_system_yt_dlp_module()
        if module:
            yt_dlp = module
            return yt_dlp
    try:
        import importlib
        clear_ytdlp_module_cache()
        module = importlib.import_module("yt_dlp")
        module = maybe_use_external_yt_dlp(module)
        yt_dlp = module
        return yt_dlp
    except Exception as exc:
        logging.warning(f"Could not load bundled yt-dlp: {exc}")
        return None

_YT_DLP_RELEASE_CACHE = {
    "timestamp": 0,
    "releases": None
}
GITHUB_HEADERS = dict(HTTP_HEADERS)


def fetch_yt_dlp_releases(max_releases=30, cache_seconds=600):
    """Fetch recent yt-dlp releases (cached to avoid spamming the API)"""
    now = time.time()
    if (_YT_DLP_RELEASE_CACHE["releases"] and
            now - _YT_DLP_RELEASE_CACHE["timestamp"] < cache_seconds):
        return _YT_DLP_RELEASE_CACHE["releases"]
    try:
        response = requests.get(
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases",
            params={"per_page": max_releases},
            timeout=5,
            headers=GITHUB_HEADERS
        )
        if response.status_code == 200:
            releases = response.json()
            _YT_DLP_RELEASE_CACHE["timestamp"] = now
            _YT_DLP_RELEASE_CACHE["releases"] = releases
            return releases
    except requests.RequestException as exc:
        logging.debug(f"Could not fetch yt-dlp releases: {exc}")
    return _YT_DLP_RELEASE_CACHE["releases"] or []


def evaluate_yt_dlp_version_status(current_version):
    """Compare current yt-dlp version with upstream releases"""
    releases = fetch_yt_dlp_releases()
    release_tags = [rel.get("tag_name") for rel in releases if rel.get("tag_name")]
    latest_version = release_tags[0] if release_tags else None

    if not current_version:
        return {
            "status": "unknown",
            "latest_version": latest_version,
            "releases_behind": None
        }

    if not latest_version:
        return {
            "status": "unknown",
            "latest_version": None,
            "releases_behind": None
        }

    if parse_version_tuple(current_version) == parse_version_tuple(latest_version):
        return {
            "status": "up_to_date",
            "latest_version": latest_version,
            "releases_behind": 0
        }

    releases_behind = None
    for idx, version in enumerate(release_tags):
        if parse_version_tuple(version) == parse_version_tuple(current_version):
            releases_behind = idx
            break

    status = "behind" if releases_behind is not None else "unknown"
    return {
        "status": status,
        "latest_version": latest_version,
        "releases_behind": releases_behind
    }



def can_update_yt_dlp():
    """ Check if yt-dlp can be updated in the current environment """
    plan = get_python_update_plan()
    return plan.get("status") == "ready"

def get_python_update_plan():
    """Build a structured plan (and diagnostics) for updating yt-dlp"""
    plan = {
        "environment": get_execution_environment(),
        "status": "ready"
    }
    try:
        if plan["environment"] == "source":
            python_info = {
                "command": (sys.executable,),
                "display_name": sys.executable,
                "version": sys.version.split()[0],
                "has_pip": True,
                "can_bootstrap_pip": True
            }
            plan.update({
                "python_info": python_info,
                "pre_commands": [],
                "update_command": [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
            })
            return plan

        if plan["environment"] == "exe_no_python":
            plan["status"] = "missing_python"
            plan["status_detail"] = "Python was not found on PATH."
            return plan

        python_info = detect_python_runtime()
        if not python_info:
            plan["status"] = "missing_python"
            plan["status_detail"] = "Python was not found on PATH."
            return plan
        plan["python_info"] = python_info
        plan["pre_commands"] = []

        if not python_info.get("has_pip"):
            if python_info.get("can_bootstrap_pip"):
                plan["pre_commands"].append({
                    "command": list(python_info["command"]) + ["-m", "ensurepip", "--upgrade"],
                    "description": "Installing pip in the detected Python installation...",
                    "refresh_python_cache": True
                })
            else:
                plan["status"] = "pip_missing"
                plan["status_detail"] = "Pip is missing in the detected Python installation."
                return plan

        target_dir = None
        if getattr(sys, 'frozen', False):
            target_dir = get_external_python_modules_path()
            if not target_dir:
                plan["status"] = "target_unwritable"
                plan["status_detail"] = "Could not create a writable folder for updated packages."
                return plan
            plan["target_directory"] = target_dir

        update_command = list(python_info["command"]) + ["-m", "pip", "install", "--upgrade"]
        if target_dir:
            update_command.extend(["--target", target_dir, "--no-warn-script-location"])
        update_command.append("yt-dlp")

        plan["update_command"] = update_command
        return plan
    except Exception as exc:
        logging.error(f"Could not build yt-dlp update plan: {exc}")
        plan["status"] = "error"
        plan["status_detail"] = str(exc)
        return plan

def get_ytdlp_installation_info():
    """ Detect which yt-dlp installation is actually being used """
    global yt_dlp
    try:
        current_version = get_ytdlp_version_safe(yt_dlp)
        ytdlp_path = os.path.abspath(getattr(yt_dlp, "__file__", ""))
        env = get_execution_environment()
        is_frozen = getattr(sys, 'frozen', False)

        installation_type = "development"
        source_tag = getattr(yt_dlp, "__fwhisper_source__", None)
        bundle_base = getattr(sys, "_MEIPASS", None)
        external_path = find_external_yt_dlp_path()

        if is_frozen:
            if source_tag == "external" or (external_path and ytdlp_path.startswith(external_path)):
                installation_type = "external_override"
            elif bundle_base and ytdlp_path.startswith(os.path.abspath(bundle_base)):
                installation_type = "bundled"
            elif os.path.dirname(sys.executable) and ytdlp_path.startswith(os.path.dirname(sys.executable)):
                installation_type = "bundled"
            else:
                installation_type = "system"
        else:
            installation_type = "development"

        return {
            "version": current_version,
            "path": ytdlp_path,
            "installation_type": installation_type,
            "environment": env,
            "source": source_tag or installation_type
        }
    except Exception:
        return {
            "version": None,
            "path": None,
            "installation_type": "missing",
            "environment": get_execution_environment()
        }


def _default_ytdlp_update_status():
    return {
        "ytdlp_updates": {
            "source": {},
            "exe_with_python": {},
            "exe_no_python": {}
        }
    }


def load_ytdlp_update_status():
    """ Load persistent yt-dlp update tracking data """
    try:
        portable_dir = get_portable_settings_directory()
        update_file = os.path.join(portable_dir, "ytdlp_updates.json")

        if os.path.exists(update_file):
            try:
                with open(update_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as exc:
                logging.warning(f"Could not load yt-dlp update status: {exc}")
                data = _default_ytdlp_update_status()
                save_ytdlp_update_status(data)
                return data
        return _default_ytdlp_update_status()
    except Exception as e:
        logging.warning(f"Could not load yt-dlp update status: {e}")
        return _default_ytdlp_update_status()


def save_ytdlp_update_status(data):
    """ Save persistent yt-dlp update tracking data """
    try:
        portable_dir = get_portable_settings_directory()
        update_file = os.path.join(portable_dir, "ytdlp_updates.json")
        
        with open(update_file, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Could not save yt-dlp update status: {e}")


def is_within_update_cooldown(last_update_timestamp, cooldown_hours=24):
    """ Check if we're within the cooldown period since last successful update """
    if not last_update_timestamp:
        return False
    
    import time
    current_time = time.time()
    cooldown_seconds = cooldown_hours * 3600
    
    return (current_time - last_update_timestamp) < cooldown_seconds


def should_check_ytdlp_update(current_version=None, env=None):
    """Determine if we should check for yt-dlp updates based on persistent tracking."""
    try:
        if current_version is None or env is None:
            install_info = get_ytdlp_installation_info()
            if env is None:
                env = install_info["environment"]
            if current_version is None:
                current_version = install_info["version"]
        
        if not current_version:
            return True  # yt-dlp not found, should check
        
        update_data = load_ytdlp_update_status()
        env_data = update_data["ytdlp_updates"].get(env, {})
        
        last_updated_version = env_data.get("last_updated_version")
        last_update_timestamp = env_data.get("last_update_timestamp")
        last_failure_version = env_data.get("last_failure_version")
        last_failure_timestamp = env_data.get("last_failure_timestamp")

        # If the version changed since the last failure, clear the failure cooldown.
        if last_failure_timestamp and current_version and last_failure_version and current_version != last_failure_version:
            env_data.pop("last_failure_timestamp", None)
            env_data.pop("last_failure_reason", None)
            env_data.pop("last_failure_version", None)
            update_data["ytdlp_updates"][env] = env_data
            save_ytdlp_update_status(update_data)
            last_failure_timestamp = None

        if (
            last_updated_version == current_version and 
            is_within_update_cooldown(last_update_timestamp)
        ):
            logging.info(f"yt-dlp update skipped: recently updated to {current_version} in {env} mode")
            return False
        
        if is_within_update_cooldown(last_failure_timestamp, YTDLP_UPDATE_FAILURE_COOLDOWN_HOURS):
            logging.info(f"yt-dlp update prompt skipped (recent failure cooldown) in {env} mode")
            return False
        
        return True
        
    except Exception as e:
        logging.error(f"Error checking if should update yt-dlp: {e}")
        return True  # Default to checking if unsure


def record_ytdlp_update_success(updated_version):
    """ Record a successful yt-dlp update """
    try:
        import time
        
        env = get_execution_environment()
        
        update_data = load_ytdlp_update_status()
        
        if env not in update_data["ytdlp_updates"]:
            update_data["ytdlp_updates"][env] = {}
        
        update_data["ytdlp_updates"][env].update({
            "last_updated_version": updated_version,
            "last_update_timestamp": time.time()
        })
        update_data["ytdlp_updates"][env].pop("last_failure_timestamp", None)
        update_data["ytdlp_updates"][env].pop("last_failure_reason", None)
        update_data["ytdlp_updates"][env].pop("last_failure_version", None)
        
        save_ytdlp_update_status(update_data)
        
        logging.info(f"Recorded successful yt-dlp update to {updated_version} in {env} mode")
        
    except Exception as e:
        logging.error(f"Could not record yt-dlp update success: {e}")


def record_ytdlp_update_failure(reason=None, current_version=None):
    """Record a failed yt-dlp update attempt to avoid repeated prompts."""
    try:
        import time
        env = get_execution_environment()
        update_data = load_ytdlp_update_status()
        if env not in update_data["ytdlp_updates"]:
            update_data["ytdlp_updates"][env] = {}
        update_data["ytdlp_updates"][env].update({
            "last_failure_timestamp": time.time(),
            "last_failure_reason": reason,
            "last_failure_version": current_version
        })
        save_ytdlp_update_status(update_data)
        logging.info(f"Recorded yt-dlp update failure in {env} mode")
    except Exception as e:
        logging.error(f"Could not record yt-dlp update failure: {e}")

def clear_ytdlp_update_failure(env=None):
    """Clear any stored yt-dlp update failure markers for the given environment."""
    try:
        env = env or get_execution_environment()
        update_data = load_ytdlp_update_status()
        if env not in update_data["ytdlp_updates"]:
            return
        env_data = update_data["ytdlp_updates"][env]
        env_data.pop("last_failure_timestamp", None)
        env_data.pop("last_failure_reason", None)
        env_data.pop("last_failure_version", None)
        update_data["ytdlp_updates"][env] = env_data
        save_ytdlp_update_status(update_data)
        logging.info(f"Cleared yt-dlp update failure markers for {env} mode")
    except Exception as e:
        logging.error(f"Could not clear yt-dlp update failure markers: {e}")

import yt_dlp as bundled_yt_dlp
yt_dlp = maybe_use_external_yt_dlp(bundled_yt_dlp)
