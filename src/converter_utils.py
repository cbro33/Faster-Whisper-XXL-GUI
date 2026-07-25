import os
import sys
import json
import shutil
import zipfile
import tempfile
import hashlib
import logging
import requests

from config import (
    APP_VERSION,
    CONVERTER_BUNDLE_REPO,
    CONVERTER_BUNDLE_TAG,
    CONVERTER_BUNDLE_ASSET,
    CONVERTER_BUNDLE_SHA256_ASSET,
    CONVERTER_BUNDLE_DIR_NAME,
    HTTP_HEADERS,
)
from utils import get_settings_directory

GITHUB_HEADERS = dict(HTTP_HEADERS)

_WEIGHT_FILENAMES = {"model.safetensors", "pytorch_model.bin"}

def find_transformers_weight_files(filenames):
    if not filenames:
        return []
    matches = []
    for name in filenames:
        if not name:
            continue
        lowered = name.lower()
        if lowered in _WEIGHT_FILENAMES:
            matches.append(name)
        elif lowered.startswith("model-") and lowered.endswith(".safetensors"):
            matches.append(name)
        elif lowered.startswith("pytorch_model-") and lowered.endswith(".bin"):
            matches.append(name)
    return matches

def scan_transformers_weights(model_dir):
    if not model_dir or not os.path.isdir(model_dir):
        return []
    try:
        filenames = os.listdir(model_dir)
    except Exception:
        return []
    return find_transformers_weight_files(filenames)

def get_converter_bundle_cache_dir():
    settings_dir = get_settings_directory()
    return os.path.join(settings_dir, CONVERTER_BUNDLE_DIR_NAME)

def get_converter_bundle_dir():
    base_dir = get_converter_bundle_cache_dir()
    return os.path.join(base_dir, CONVERTER_BUNDLE_TAG)

def get_converter_python_path(bundle_dir):
    if not bundle_dir:
        return None
    candidates = [
        os.path.join(bundle_dir, "python", "Scripts", "python.exe"),
        os.path.join(bundle_dir, "Scripts", "python.exe"),
        os.path.join(bundle_dir, "python", "python.exe"),
        os.path.join(bundle_dir, "python.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None

def _fetch_release_info():
    api_url = f"https://api.github.com/repos/{CONVERTER_BUNDLE_REPO}/releases/tags/{CONVERTER_BUNDLE_TAG}"
    response = requests.get(api_url, timeout=12, headers=GITHUB_HEADERS)
    if response.status_code != 200:
        raise RuntimeError(f"Release lookup failed (status {response.status_code}).")
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Release lookup failed: {exc}") from exc

def _find_release_asset(release, asset_name):
    assets = release.get("assets") or []
    for asset in assets:
        if asset.get("name") == asset_name:
            return asset
    return None

def _download_file(url, target_path, progress_cb=None, cancel_cb=None):
    response = requests.get(url, stream=True, timeout=30, headers=GITHUB_HEADERS)
    response.raise_for_status()
    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    with open(target_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if cancel_cb and cancel_cb():
                return False, total, downloaded
            if not chunk:
                continue
            handle.write(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(downloaded, total)
    return True, total, downloaded

def _read_sha256_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            contents = handle.read().strip()
        if not contents:
            return None
        token = contents.split()[0].strip()
        if len(token) != 64:
            return None
        return token.lower()
    except Exception:
        return None

def _compute_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()

def ensure_converter_bundle(progress_cb=None, cancel_cb=None):
    bundle_dir = get_converter_bundle_dir()
    python_path = get_converter_python_path(bundle_dir)
    if python_path and os.path.isfile(python_path):
        return python_path

    os.makedirs(bundle_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="fw-converter-")
    try:
        release = _fetch_release_info()
        asset = _find_release_asset(release, CONVERTER_BUNDLE_ASSET)
        if not asset or not asset.get("browser_download_url"):
            raise RuntimeError(f"Converter bundle asset not found: {CONVERTER_BUNDLE_ASSET}")
        zip_url = asset.get("browser_download_url")

        sha_asset = _find_release_asset(release, CONVERTER_BUNDLE_SHA256_ASSET)
        sha_url = sha_asset.get("browser_download_url") if sha_asset else None

        zip_path = os.path.join(temp_dir, CONVERTER_BUNDLE_ASSET)
        if progress_cb:
            progress_cb("Downloading converter bundle...", 0, 0)

        def download_progress(downloaded, total):
            if progress_cb:
                progress_cb("Downloading converter bundle...", downloaded, total)

        ok, _total, _downloaded = _download_file(zip_url, zip_path, progress_cb=download_progress, cancel_cb=cancel_cb)
        if not ok:
            raise RuntimeError("Converter download cancelled.")

        sha_expected = None
        if sha_url:
            sha_path = os.path.join(temp_dir, CONVERTER_BUNDLE_SHA256_ASSET)
            _download_file(sha_url, sha_path, cancel_cb=cancel_cb)
            sha_expected = _read_sha256_file(sha_path)

        if sha_expected:
            sha_actual = _compute_sha256(zip_path)
            if sha_actual != sha_expected:
                raise RuntimeError("Converter bundle checksum mismatch.")

        if progress_cb:
            progress_cb("Extracting converter bundle...", 0, 0)
        extract_dir = os.path.join(temp_dir, "extract")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            total_members = len(members)
            for index, member in enumerate(members, start=1):
                if cancel_cb and cancel_cb():
                    raise RuntimeError("Converter extraction cancelled.")
                archive.extract(member, extract_dir)
                if progress_cb and total_members:
                    progress_cb("Extracting converter bundle...", index, total_members)

        extracted_root = extract_dir
        entries = os.listdir(extract_dir)
        if len(entries) == 1:
            candidate = os.path.join(extract_dir, entries[0])
            if os.path.isdir(candidate):
                extracted_root = candidate

        if os.path.exists(bundle_dir):
            shutil.rmtree(bundle_dir, ignore_errors=True)
        shutil.move(extracted_root, bundle_dir)

        python_path = get_converter_python_path(bundle_dir)
        if not python_path or not os.path.isfile(python_path):
            raise RuntimeError("Converter bundle missing python.exe.")
        return python_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def get_fallback_python():
    override = (os.environ.get("FWHISPER_CONVERTER_PYTHON") or "").strip()
    if override:
        if os.path.isdir(override):
            candidate = os.path.join(override, "python.exe")
            if os.path.isfile(candidate):
                return candidate
        if os.path.isfile(override):
            return override
    return sys.executable
