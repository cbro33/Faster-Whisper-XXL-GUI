import os
import logging
import subprocess
import tempfile
import threading
import json
from collections import deque
import re
import time
import queue
import sys
import shutil
import requests
from PyQt6.QtCore import pyqtSignal, QThread
from utils import popen_hidden_subprocess, resolve_ffmpeg_location, run_hidden_subprocess
from converter_utils import ensure_converter_bundle, get_converter_bundle_dir, get_converter_python_path, get_fallback_python, scan_transformers_weights
import ytdlp_utils
from ytdlp_utils import log_ytdlp_update_debug
from python_utils import refresh_python_detection_cache

class YouTubeDownloader(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    file_ready = pyqtSignal(str)
    total_found = pyqtSignal(int)

    def __init__(self, urls, output_path, audio_only=True, stream_mode=True, serial_mode=False, ytdlp_exe=None):
        super().__init__()
        if isinstance(urls, str):
            self.urls = [urls]
        else:
            self.urls = list(urls or [])
        self.output_path = output_path
        self.audio_only = audio_only
        self.stream_mode = stream_mode
        self.serial_mode = serial_mode
        self.ytdlp_exe = ytdlp_exe
        self.stop_requested = False
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._debug_logger = None
        self._active_ytdlp_process = None
        self._reported_paths = set()
        self._watch_thread = None
        self._watch_stop_event = None
        self._watched_files = set()
        self._watch_start_time = None
        self._fs_watch_new_paths = set()
        self._fs_watch_lock = threading.Lock()
        self._file_ready_paths = set()
        self._started_titles = set()
        self._download_index = 0
        self._download_total = None
        self._download_started_count = 0
        self._download_completed_count = 0

    def _log_debug(self, message, *args):
        if self._debug_logger is None:
            try:
                from gui_components import get_model_download_logger
            except Exception:
                logger = logging.getLogger("model_download")
            else:
                logger = get_model_download_logger()
            self._debug_logger = logger
        logger = self._debug_logger
        if getattr(logger, "disabled", False):
            return
        logger.info(message, *args)

    def progress_hook(self, d):
        if self.stop_requested:
            raise ytdlp_utils.yt_dlp.utils.DownloadError("Download cancelled by user.")
        if d['status'] == 'downloading':
            # Sanitize the output to prevent weird formatting issues
            percent_str = d.get('_percent_str', 'N/A').strip()
            total_bytes_str = d.get('_total_bytes_str', 'N/A').strip()
            speed_str = d.get('_speed_str', 'N/A').strip()
            self.progress.emit(f"Downloading: {percent_str} of {total_bytes_str} at {speed_str}")
        elif d['status'] == 'finished':
            pass

    def _final_filename(self, ydl, info_dict):
        final_filename = ydl.prepare_filename(info_dict)
        if self.audio_only:
            base, _ = os.path.splitext(final_filename)
            final_filename = base + '.mp3'
        return final_filename

    def _collect_filenames(self, ydl, info_dict):
        if not info_dict:
            return []
        if info_dict.get('_type') == 'playlist' or 'entries' in info_dict:
            entries = list(info_dict.get('entries') or [])
            return [self._final_filename(ydl, entry) for entry in entries if entry]
        return [self._final_filename(ydl, info_dict)]

    def _entry_url(self, entry):
        if isinstance(entry, dict):
            return entry.get("webpage_url") or entry.get("url")
        if isinstance(entry, str):
            return entry
        return None

    def _is_playlist_url(self, url):
        if not url:
            return False
        lowered = url.lower()
        return "list=" in lowered or "playlist" in lowered

    def run(self):
        try:
            if self.ytdlp_exe:
                self._start_output_watcher()
            output_template = os.path.join(self.output_path, '%(title)s.%(ext)s')
            ffmpeg_location = resolve_ffmpeg_location()
            if self.audio_only:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'outtmpl': output_template,
                    'progress_hooks': [self.progress_hook],
                    'logger': logging.getLogger('yt_dlp'),
                }
            else:
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': output_template,
                    'progress_hooks': [self.progress_hook],
                    'logger': logging.getLogger('yt_dlp'),
                }
            if ffmpeg_location:
                ydl_opts['ffmpeg_location'] = ffmpeg_location

            downloaded_files = []
            total_expected = 0
            if self.ytdlp_exe:
                self._download_index = 0
                self._download_total = None
                self._started_titles = set()
                self._download_started_count = 0
                self._download_completed_count = 0
                if self.stream_mode:
                    total_expected = 0
                    total_known = True
                    for url in self.urls:
                        if self.stop_requested:
                            raise RuntimeError("Download cancelled by user.")
                        if self._is_playlist_url(url):
                            count = self._probe_ytdlp_exe_playlist_count(url)
                            if not count:
                                total_known = False
                                break
                            total_expected += count
                        else:
                            total_expected += 1
                    if total_known and total_expected > 0:
                        self._download_total = total_expected
                        self.total_found.emit(total_expected)
                        self._log_debug("yt-dlp.exe preflight count: %s", total_expected)
                for url in self.urls:
                    if self.stop_requested:
                        raise RuntimeError("Download cancelled by user.")
                    self.progress.emit("Downloading with yt-dlp.exe...")
                    self._log_debug("yt-dlp.exe download start: %s", url)
                    for path in self._run_ytdlp_exe(url, output_template, ffmpeg_location):
                        if self._record_file_ready(path):
                            downloaded_files.append(path)
                            self.file_ready.emit(path)
                    self._emit_fallback_outputs(downloaded_files)
                self._emit_fallback_outputs(downloaded_files)
            else:
                if ytdlp_utils.yt_dlp is None:
                    raise RuntimeError("yt-dlp module is not available.")
                with ytdlp_utils.yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    for url in self.urls:
                        if self.stop_requested:
                            raise ytdlp_utils.yt_dlp.utils.DownloadError("Download cancelled by user.")
                        if self.stream_mode:
                            info_dict = ydl.extract_info(url, download=False)
                            if info_dict and (info_dict.get('_type') == 'playlist' or 'entries' in info_dict):
                                entries = list(info_dict.get('entries') or [])
                                if entries:
                                    total_expected += len(entries)
                                    self.total_found.emit(total_expected)
                                for entry in entries:
                                    if self.stop_requested:
                                        raise ytdlp_utils.yt_dlp.utils.DownloadError("Download cancelled by user.")
                                    entry_url = self._entry_url(entry)
                                    if not entry_url:
                                        continue
                                    entry_info = ydl.extract_info(entry_url, download=True)
                                    for path in self._collect_filenames(ydl, entry_info):
                                        downloaded_files.append(path)
                                        self.file_ready.emit(path)
                                        if self.serial_mode:
                                            self._resume_event.clear()
                                            while not self._resume_event.is_set():
                                                if self.stop_requested:
                                                    raise ytdlp_utils.yt_dlp.utils.DownloadError("Download cancelled by user.")
                                                self._resume_event.wait(0.1)
                            else:
                                total_expected += 1
                                self.total_found.emit(total_expected)
                                info_dict = ydl.extract_info(url, download=True)
                                for path in self._collect_filenames(ydl, info_dict):
                                    downloaded_files.append(path)
                                    self.file_ready.emit(path)
                                    if self.serial_mode:
                                        self._resume_event.clear()
                                        while not self._resume_event.is_set():
                                            if self.stop_requested:
                                                raise ytdlp_utils.yt_dlp.utils.DownloadError("Download cancelled by user.")
                                            self._resume_event.wait(0.1)
                        else:
                            info_dict = ydl.extract_info(url, download=True)
                            downloaded_files.extend(self._collect_filenames(ydl, info_dict))

            missing = [path for path in downloaded_files if not os.path.exists(path)]
            if missing:
                raise FileNotFoundError(
                    "Post-processing failed. Expected files not found:\n" + "\n".join(missing)
                )

            self.finished.emit(downloaded_files)
        except Exception as e:
            logging.error(f"yt-dlp thread error: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._stop_output_watcher()

    def stop(self):
        self.stop_requested = True
        self._resume_event.set()
        self._terminate_active_process()
        self._stop_output_watcher()

    def allow_next_download(self):
        self._resume_event.set()

    def _run_ytdlp_exe(self, url, output_template, ffmpeg_location):
        return self._run_ytdlp_exe_stream(url, output_template, ffmpeg_location)

    def _run_ytdlp_exe_stream(self, url, output_template, ffmpeg_location):
        cmd = [
            self.ytdlp_exe,
            url,
            "-o",
            output_template,
            "--print",
            "after_move:filepath",
            "--newline",
            "--progress",
            "--no-warnings",
            "--progress-template",
            "PROGRESS:%(info.title)s | %(progress._percent_str)s of %(progress._total_bytes_str)s at %(progress._speed_str)s ETA %(progress._eta_str)s",
        ]
        if self.audio_only:
            cmd.extend(["-f", "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", "192"])
        else:
            cmd.extend(["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"])
        if ffmpeg_location:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = popen_hidden_subprocess(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self._active_ytdlp_process = process
        stdout_queue = queue.Queue()
        stderr_queue = queue.Queue()
        stderr_lines = []
        stdout_lines = []
        last_progress_line = None
        last_progress_time = 0.0
        last_progress_title = None
        last_activity_time = time.monotonic()
        stderr_buffer = ""

        def _drain_stream(stream, collector, out_queue=None, raw=False):
            if raw:
                while True:
                    chunk = stream.read(1)
                    if chunk == "":
                        break
                    if out_queue is not None:
                        out_queue.put(chunk)
                if out_queue is not None:
                    out_queue.put(None)
                return
            for line in stream:
                if out_queue is not None:
                    out_queue.put(line)
                else:
                    collector.append(line.rstrip())
            if out_queue is not None:
                out_queue.put(None)

        threading.Thread(
            target=_drain_stream,
            args=(process.stdout, stdout_lines, stdout_queue),
            daemon=True,
        ).start()
        threading.Thread(
            target=_drain_stream,
            args=(process.stderr, stderr_lines, stderr_queue, True),
            daemon=True,
        ).start()

        found_paths = []
        stdout_done = False
        stderr_done = False
        try:
            while True:
                if self.stop_requested:
                    self._terminate_active_process()
                    raise RuntimeError("Download cancelled by user.")
                try:
                    item = stdout_queue.get(timeout=0.05)
                except queue.Empty:
                    item = False
                if item is None:
                    stdout_done = True
                elif item is not False:
                    line = item.strip().strip('"')
                    if line:
                        self._log_debug("yt-dlp.exe stdout: %s", line)
                        if line.startswith("PROGRESS:"):
                            progress_line = line[len("PROGRESS:") :].strip()
                            title_part, details = (
                                progress_line.split(" | ", 1) if " | " in progress_line else (progress_line, "")
                            )
                            title_part = title_part.strip()
                            if title_part:
                                key = self._normalize_title_key(title_part)
                                display_title = self._format_display_name(title_part)
                                if display_title != last_progress_title:
                                    last_progress_title = display_title
                                    last_progress_line = None
                                    last_progress_time = 0.0
                                if key and key not in self._started_titles:
                                    self._started_titles.add(key)
                                    self._download_started_count += 1
                                index = self._download_started_count
                                if self._download_total:
                                    prefix = f"{index}/{self._download_total}"
                                else:
                                    prefix = f"{index}/?"
                                display_line = f"Downloading ({prefix}): {display_title}"
                                if details:
                                    display_line += f" | {details}"
                                if display_line != last_progress_line:
                                    last_progress_line = display_line
                                    self.progress.emit(display_line)
                        else:
                            stdout_lines.append(line)
                            if line not in self._reported_paths:
                                self._reported_paths.add(line)
                                display_file = self._format_display_name(os.path.splitext(os.path.basename(line))[0])
                                self._download_completed_count += 1
                                index = self._download_completed_count
                                if self._download_total:
                                    prefix = f"{index}/{self._download_total}"
                                else:
                                    prefix = f"{index}/?"
                                self.progress.emit(f"Downloaded ({prefix}): {display_file}")
                            path = self._normalize_existing_path(line)
                            if not path:
                                path = self._wait_for_existing_path(line, timeout=5)
                            if path:
                                found_paths.append(path)
                                try:
                                    mtime = os.path.getmtime(path)
                                    size = os.path.getsize(path)
                                    self._log_debug(
                                        "yt-dlp.exe file_ready: %s mtime=%s size=%s",
                                        path,
                                        mtime,
                                        size,
                                    )
                                except Exception:
                                    self._log_debug("yt-dlp.exe file_ready: %s", path)
                                yield path
                try:
                    err_item = stderr_queue.get_nowait()
                except queue.Empty:
                    err_item = False
                if err_item is None:
                    stderr_done = True
                elif err_item is not False:
                    stderr_buffer += err_item
                    last_activity_time = time.monotonic()
                    while True:
                        split_index = None
                        for sep in ("\r", "\n"):
                            idx = stderr_buffer.find(sep)
                            if idx != -1:
                                split_index = idx
                                break
                        if split_index is None:
                            break
                        err_line = stderr_buffer[:split_index].strip()
                        stderr_buffer = stderr_buffer[split_index + 1:]
                        if err_line:
                            stderr_lines.append(err_line)
                            self._log_debug("yt-dlp.exe stderr: %s", err_line)
                            now = time.monotonic()
                            if err_line != last_progress_line and (now - last_progress_time) >= 0.2:
                                last_progress_line = err_line
                                last_progress_time = now
                                self.progress.emit(f"Downloading: {err_line}")
                returncode = process.poll()
                if returncode is not None and stdout_done and stderr_done:
                    break
            returncode = process.wait()
            if returncode != 0:
                msg = ("\n".join(stderr_lines) or "\n".join(stdout_lines)).strip()
                raise RuntimeError(msg or "yt-dlp.exe failed")
            if not found_paths and stdout_lines:
                logging.warning("yt-dlp.exe returned paths but none exist: %s", stdout_lines)
                self._log_debug("yt-dlp.exe returned paths but none exist: %s", stdout_lines)
        finally:
            self._active_ytdlp_process = None

    def _normalize_existing_path(self, line):
        if os.path.exists(line):
            return line
        normalized = os.path.normpath(line)
        if normalized != line and os.path.exists(normalized):
            return normalized
        matched = self._find_matching_output_path(line)
        if matched:
            return matched
        return None

    def _wait_for_existing_path(self, line, timeout=5):
        if not line:
            return None
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < timeout:
            path = self._normalize_existing_path(line)
            if path:
                return path
            time.sleep(0.1)
        return None

    def _wait_for_nonzero_size(self, path, timeout=5):
        if not path:
            return False
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < timeout:
            if not os.path.exists(path):
                return False
            try:
                if os.path.getsize(path) > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.1)
        return False

    def _terminate_active_process(self):
        process = self._active_ytdlp_process
        if process and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=2)
                    return
                except subprocess.TimeoutExpired:
                    process.kill()
                    return
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                    )
                except Exception:
                    pass
        if sys.platform == "win32":
            exe_name = None
            if self.ytdlp_exe:
                exe_name = os.path.basename(self.ytdlp_exe)
            if not exe_name:
                exe_name = "yt-dlp.exe"
            try:
                subprocess.run(
                    ["taskkill", "/IM", exe_name, "/T", "/F"],
                    capture_output=True,
                    text=True,
                )
            except Exception:
                pass

    def _should_watch_file(self, filename):
        if not filename:
            return False
        lowered = filename.lower()
        if self.audio_only:
            return lowered.endswith(".mp3")
        return lowered.endswith((".mp4", ".mkv", ".webm", ".m4a"))

    def _format_display_name(self, name):
        if not name:
            return name
        cleaned = name.replace("｜", "|")
        cleaned = re.sub(r"\s*\|\s*", " | ", cleaned)
        cleaned = re.sub(r"(?<=\S)\s{2,}(?=\S)", " | ", cleaned)
        return cleaned.strip()

    def _normalize_title_key(self, name):
        cleaned = self._format_display_name(name)
        return cleaned.lower() if cleaned else ""

    def _record_file_ready(self, path):
        if not path:
            return False
        normalized = os.path.normpath(path)
        if normalized in self._file_ready_paths:
            return False
        self._file_ready_paths.add(normalized)
        return True

    def _is_candidate_output_file(self, filename):
        if not self._should_watch_file(filename):
            return False
        lowered = filename.lower()
        if ".temp." in lowered:
            return False
        if re.search(r"\.f\d+\.", lowered):
            return False
        return True

    def _normalize_match_key(self, name):
        cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", name or "").strip()
        return re.sub(r"\s+", " ", cleaned).lower()

    def _find_matching_output_path(self, path_text):
        if not self.output_path or not path_text:
            return None
        base_name = os.path.basename(path_text)
        stem, ext = os.path.splitext(base_name)
        if not stem or not ext:
            return None
        target_key = self._normalize_match_key(stem)
        if not target_key:
            return None
        matches = []
        try:
            for name in os.listdir(self.output_path):
                if not self._is_candidate_output_file(name):
                    continue
                candidate_stem, candidate_ext = os.path.splitext(name)
                if candidate_ext.lower() != ext.lower():
                    continue
                if self._normalize_match_key(candidate_stem) == target_key:
                    matches.append(os.path.join(self.output_path, name))
        except Exception:
            return None
        if len(matches) == 1 and os.path.exists(matches[0]):
            return matches[0]
        return None

    def _gather_output_files(self, min_mtime=None):
        if not self.output_path:
            return []
        results = []
        try:
            for name in os.listdir(self.output_path):
                if not self._is_candidate_output_file(name):
                    continue
                path = os.path.join(self.output_path, name)
                try:
                    mtime = os.path.getmtime(path)
                    if min_mtime is not None and mtime < min_mtime:
                        continue
                    if os.path.getsize(path) == 0:
                        continue
                except OSError:
                    continue
                results.append(path)
        except Exception:
            return []
        return results

    def _emit_fallback_outputs(self, downloaded_files):
        min_mtime = self._watch_start_time
        with self._fs_watch_lock:
            fallback_paths = list(self._fs_watch_new_paths)
        fallback_paths.extend(self._gather_output_files(min_mtime))
        for path in fallback_paths:
            if not path or not os.path.exists(path):
                continue
            try:
                if os.path.getsize(path) == 0:
                    if not self._wait_for_nonzero_size(path, timeout=5):
                        continue
            except OSError:
                continue
            if self._record_file_ready(path):
                downloaded_files.append(path)
                self.file_ready.emit(path)

    def _start_output_watcher(self):
        if not self.output_path:
            return
        self._watch_stop_event = threading.Event()
        self._watched_files = set()
        self._fs_watch_new_paths = set()
        self._watch_start_time = time.time()
        try:
            for name in os.listdir(self.output_path):
                if self._is_candidate_output_file(name):
                    self._watched_files.add(name)
        except Exception:
            pass
        self._watch_thread = threading.Thread(target=self._watch_output_folder, daemon=True)
        self._watch_thread.start()

    def _stop_output_watcher(self):
        if self._watch_stop_event:
            self._watch_stop_event.set()
        if self._watch_thread and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=1)
        self._watch_thread = None
        self._watch_stop_event = None

    def _watch_output_folder(self):
        while self._watch_stop_event and not self._watch_stop_event.is_set():
            try:
                for name in os.listdir(self.output_path):
                    if not self._is_candidate_output_file(name):
                        continue
                    if name in self._watched_files:
                        continue
                    self._watched_files.add(name)
                    path = os.path.join(self.output_path, name)
                    with self._fs_watch_lock:
                        self._fs_watch_new_paths.add(path)
                    try:
                        size = os.path.getsize(path)
                        mtime = os.path.getmtime(path)
                        self._log_debug("fs_watch new file: %s mtime=%s size=%s", path, mtime, size)
                    except Exception:
                        self._log_debug("fs_watch new file: %s", path)
                    self._log_debug("fs_watch console suppressed for: %s", name)
                time.sleep(0.5)
            except Exception as exc:
                self._log_debug("fs_watch error: %s", exc)
                time.sleep(1.0)

    def _probe_ytdlp_exe_total(self, url):
        cmd = [self.ytdlp_exe, url, "--dump-single-json", "--flat-playlist", "--no-warnings"]
        logging.info("yt-dlp.exe preflight start: %s", url)
        self._log_debug("yt-dlp.exe preflight start: %s", url)
        stdout, stderr, returncode = self._run_ytdlp_exe_command(cmd, timeout=60, label="preflight")
        if returncode != 0:
            snippet = (stderr or stdout or "").strip()
            logging.warning("yt-dlp.exe preflight failed: %s", snippet or "no output")
            self._log_debug("yt-dlp.exe preflight failed: %s", snippet or "no output")
            return None
        payload = (stdout or "").strip()
        if not payload:
            logging.warning("yt-dlp.exe preflight returned empty output")
            self._log_debug("yt-dlp.exe preflight returned empty output")
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logging.warning("yt-dlp.exe preflight JSON parse failed")
            self._log_debug("yt-dlp.exe preflight JSON parse failed")
            return None
        entries = data.get("entries") if isinstance(data, dict) else None
        if isinstance(entries, list):
            logging.info("yt-dlp.exe preflight count: %s", len(entries))
            self._log_debug("yt-dlp.exe preflight count: %s", len(entries))
            return len(entries)
        logging.info("yt-dlp.exe preflight count: 1")
        self._log_debug("yt-dlp.exe preflight count: 1")
        return 1

    def _run_ytdlp_exe_command(self, cmd, timeout=None, label=None):
        process = popen_hidden_subprocess(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        start_time = time.monotonic()
        while True:
            if self.stop_requested:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RuntimeError("Download cancelled by user.")
            if timeout is not None and (time.monotonic() - start_time) >= timeout:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                logging.warning("yt-dlp.exe %s timed out after %ss", label or "command", timeout)
                self._log_debug("yt-dlp.exe %s timed out after %ss", label or "command", timeout)
                return "", f"yt-dlp.exe {label or 'command'} timed out", 124
            returncode = process.poll()
            if returncode is not None:
                break
            time.sleep(0.1)
        stdout, stderr = process.communicate()
        if label:
            if returncode == 0:
                logging.info("yt-dlp.exe %s finished", label)
                self._log_debug("yt-dlp.exe %s finished", label)
            else:
                logging.warning("yt-dlp.exe %s failed: %s", label, (stderr or stdout or "").strip())
                self._log_debug("yt-dlp.exe %s failed: %s", label, (stderr or stdout or "").strip())
        return stdout, stderr, returncode

    def _probe_ytdlp_exe_playlist_count(self, url):
        cmd = [
            self.ytdlp_exe,
            url,
            "--flat-playlist",
            "--print",
            "id",
            "--no-warnings",
            "--yes-playlist",
        ]
        stdout, stderr, returncode = self._run_ytdlp_exe_command(cmd, timeout=20, label="playlist_count")
        if returncode != 0:
            snippet = (stderr or stdout or "").strip()
            self._log_debug("yt-dlp.exe playlist count failed: %s", snippet or "no output")
            return None
        lines = [line for line in (stdout or "").splitlines() if line.strip()]
        return len(lines) if lines else None


class YtDlpUpdateWorker(QThread):
    """Thread worker for updating yt-dlp without blocking the main UI"""
    finished = pyqtSignal(bool, str)  # success, message
    progress = pyqtSignal(str)  # progress message

    def __init__(self, update_plan):
        super().__init__()
        self.update_plan = update_plan
        self.stop_requested = False
        self.current_process = None

    def run(self):
        try:
            steps = self._build_steps()
            total_steps = len(steps)
            for index, step in enumerate(steps, 1):
                if self.stop_requested:
                    self.finished.emit(False, "Update cancelled by user")
                    return
                description = step.get("description", "Running command...")
                self.progress.emit(f"{description} ({index}/{total_steps})")
                success, stdout, stderr, message = self._run_command(
                    step.get("command"),
                    step.get("timeout", 300)
                )
                if not success:
                    if not message:
                        snippet = (stderr or stdout or "").strip()
                        message = snippet or "An unknown error occurred while updating yt-dlp."
                    self.finished.emit(False, message)
                    return
                if step.get("refresh_python_cache"):
                    refresh_python_detection_cache()

            python_info = self.update_plan.get("python_info")
            target_dir = self.update_plan.get("target_directory")
            success_msg = "yt-dlp has been updated successfully!\nThe new version will be used for future downloads."
            if python_info:
                success_msg += (
                    f"\n\nPython: {python_info.get('display_name')} "
                    f"(version {python_info.get('version')})"
                )
            if target_dir:
                success_msg += f"\nUpdated files were placed in:\n{target_dir}"

            logging.info("yt-dlp updated successfully via thread")
            self.finished.emit(True, success_msg)
        except Exception as exc:
            logging.error(f"yt-dlp update error via thread: {exc}")
            self.finished.emit(False, f"An error occurred while updating yt-dlp:\n{exc}")

    def _build_steps(self):
        steps = []
        for pre in self.update_plan.get("pre_commands", []):
            steps.append({
                "command": pre.get("command"),
                "description": pre.get("description", "Preparing environment..."),
                "timeout": pre.get("timeout", 180),
                "refresh_python_cache": pre.get("refresh_python_cache", False)
            })
        steps.append({
            "command": self.update_plan.get("update_command"),
            "description": "Downloading and installing the latest yt-dlp...",
            "timeout": self.update_plan.get("timeout", 300),
            "refresh_python_cache": True
        })
        return steps

    def _run_command(self, command, timeout):
        if not command:
            return False, "", "", "Internal error: missing update command."
        try:
            log_ytdlp_update_debug(f"Running command: {' '.join(command)}")
            self.current_process = popen_hidden_subprocess(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = self.current_process.communicate(timeout=timeout)
            return_code = self.current_process.returncode
        except subprocess.TimeoutExpired:
            if self.current_process:
                self.current_process.kill()
                stdout, stderr = self.current_process.communicate()
            else:
                stdout = stderr = ""
            log_ytdlp_update_debug("Command timed out.")
            return False, stdout, stderr, "Command timed out. Please check your internet connection and try again."
        except FileNotFoundError as exc:
            log_ytdlp_update_debug(f"Command not found: {exc}")
            return False, "", "", f"Command not found: {command[0]} ({exc})."
        except Exception as exc:
            log_ytdlp_update_debug(f"Command error: {exc}")
            return False, "", "", str(exc)
        finally:
            self.current_process = None

        if self.stop_requested:
            log_ytdlp_update_debug("Update cancelled by user.")
            return False, stdout, stderr, "Update cancelled by user"

        if return_code != 0:
            snippet_source = (stderr or stdout or "").strip().splitlines()
            snippet = "\n".join(snippet_source[-10:]) if snippet_source else "Command returned a non-zero exit code."
            log_ytdlp_update_debug(f"Command failed (exit {return_code}).")
            if stderr:
                log_ytdlp_update_debug(f"STDERR:\n{stderr.strip()}")
            if stdout:
                log_ytdlp_update_debug(f"STDOUT:\n{stdout.strip()}")
            return False, stdout, stderr, snippet

        log_ytdlp_update_debug("Command finished successfully.")
        if stdout:
            log_ytdlp_update_debug(f"STDOUT:\n{stdout.strip()}")
        if stderr:
            log_ytdlp_update_debug(f"STDERR:\n{stderr.strip()}")
        return True, stdout, stderr, ""

    def stop(self):
        """Request the update to stop"""
        self.stop_requested = True
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
            except Exception:
                try:
                    self.current_process.kill()
                except Exception:
                    pass


class ModelConversionWorker(QThread):
    progress = pyqtSignal(str)
    progress_stats = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, model_dir, use_bundle=False, parent=None):
        super().__init__(parent)
        self.model_dir = model_dir
        self.use_bundle = use_bundle
        self.stop_requested = False
        self.current_process = None
        self._last_percent = None
        self._stdout_tail = deque(maxlen=12)
        self._stderr_tail = deque(maxlen=12)
        self._last_metric_time = None
        self._last_output_size = 0
        self._total_input_bytes = 0

    def stop(self):
        self.stop_requested = True
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
            except Exception:
                try:
                    self.current_process.kill()
                except Exception:
                    pass

    def _is_cancelled(self):
        return self.stop_requested

    def _bundle_progress(self, message, downloaded, total):
        if self.stop_requested:
            return
        if total and downloaded:
            percent = int((downloaded / total) * 100)
            if percent != self._last_percent:
                self._last_percent = percent
                self.progress.emit(f"{message} ({percent}%)")
        else:
            self.progress.emit(message)

    def _run_command(self, command):
        if not command:
            return False, "Missing conversion command."
        try:
            self.current_process = popen_hidden_subprocess(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._build_subprocess_env()
            )
            def _stream(pipe, target, label):
                if pipe is None:
                    return
                try:
                    for line in iter(pipe.readline, ""):
                        if not line:
                            break
                        cleaned = line.strip()
                        if cleaned:
                            target.append(cleaned)
                            self.progress.emit(f"{label}: {cleaned}")
                except Exception:
                    pass
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            stdout_thread = threading.Thread(
                target=_stream,
                args=(self.current_process.stdout, self._stdout_tail, "converter"),
                daemon=True
            )
            stderr_thread = threading.Thread(
                target=_stream,
                args=(self.current_process.stderr, self._stderr_tail, "converter"),
                daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()

            while True:
                if self.stop_requested:
                    self.current_process.terminate()
                    return False, "Conversion cancelled by user."
                self._emit_progress_metrics()
                return_code = self.current_process.poll()
                if return_code is not None:
                    break
                time.sleep(0.2)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
        except FileNotFoundError as exc:
            return False, f"Converter executable not found: {exc}"
        except Exception as exc:
            return False, str(exc)
        finally:
            self.current_process = None

        if return_code != 0:
            snippet = list(self._stderr_tail) or list(self._stdout_tail)
            tail = "\n".join(snippet[-12:]) if snippet else "Conversion failed."
            return False, tail
        return True, ""

    def _build_subprocess_env(self):
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)
        return env

    def _emit_progress_metrics(self):
        if not self._total_input_bytes:
            return
        output_dir = os.path.join(self.model_dir, "_ct2_conversion")
        if not os.path.isdir(output_dir):
            return
        now = time.time()
        if self._last_metric_time and now - self._last_metric_time < 0.8:
            return
        output_size = 0
        try:
            for root, _, files in os.walk(output_dir):
                for filename in files:
                    path = os.path.join(root, filename)
                    try:
                        output_size += os.path.getsize(path)
                    except Exception:
                        continue
        except Exception:
            return
        if output_size <= 0:
            self._last_metric_time = now
            self.progress_stats.emit("Converting model...", -1)
            return

        percent = int((output_size / self._total_input_bytes) * 100)
        if percent < 0:
            percent = 0
        if percent > 95:
            percent = 95
        eta_text = ""
        if self._last_metric_time:
            dt = now - self._last_metric_time
            delta = output_size - self._last_output_size
            if dt > 0 and delta > 0:
                rate = delta / dt
                remaining = max(self._total_input_bytes - output_size, 0)
                eta_seconds = int(remaining / rate) if rate > 0 else 0
                if eta_seconds > 0:
                    minutes = eta_seconds // 60
                    seconds = eta_seconds % 60
                    if minutes:
                        eta_text = f" • ETA {minutes}m {seconds:02d}s"
                    else:
                        eta_text = f" • ETA {seconds}s"
        self._last_metric_time = now
        self._last_output_size = output_size
        message = f"Converting model... {percent}%{eta_text}"
        self.progress_stats.emit(message, percent)

    def _check_python_deps(self, python_path):
        if not python_path:
            return False, "Python interpreter not found."
        cmd = [
            python_path,
            "-c",
            "import ctranslate2, transformers, torch, safetensors"
        ]
        try:
            result = run_hidden_subprocess(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=self._build_subprocess_env(),
            )
        except Exception as exc:
            return False, f"Failed to probe Python dependencies: {exc}"
        if result.returncode == 0:
            return True, ""
        stderr = (result.stderr or result.stdout or "").strip()
        env = os.environ.get("CONDA_DEFAULT_ENV")
        if self.use_bundle:
            base_hint = (
                "The converter bundle is missing required packages.\n"
                "Rebuild the bundle (Python 3.11 recommended) and re-upload it."
            )
            override_hint = ""
        else:
            base_hint = (
                "Conda detected. Install deps in the active env:\n"
                "  conda install -c conda-forge ctranslate2\n"
                "  pip install transformers[torch] safetensors"
            ) if env else (
                "Install deps:\n"
                "  pip install ctranslate2 transformers[torch] safetensors"
            )
            override_hint = (
                "If deps are installed in another environment, set:\n"
                "  FWHISPER_CONVERTER_PYTHON=<path to python.exe>\n"
                "or configure it in Advanced Settings (Converter Python)."
            )
        detail = stderr or "Missing conversion dependencies."
        if override_hint:
            return False, f"{detail}\n\n{base_hint}\n\n{override_hint}"
        return False, f"{detail}\n\n{base_hint}"

    def run(self):
        try:
            if not self.model_dir or not os.path.isdir(self.model_dir):
                self.finished.emit(False, "Model directory not found.")
                return
            model_bin = os.path.join(self.model_dir, "model.bin")
            if os.path.isfile(model_bin):
                self.finished.emit(True, "Model already converted.")
                return

            if self.use_bundle:
                self.progress.emit("Preparing converter bundle...")
                python_path = ensure_converter_bundle(
                    progress_cb=self._bundle_progress,
                    cancel_cb=self._is_cancelled
                )
            else:
                python_path = get_fallback_python()

            if not python_path or not os.path.isfile(python_path):
                self.finished.emit(False, "Python interpreter not found.")
                return
            ok, detail = self._check_python_deps(python_path)
            if not ok and self.use_bundle:
                self.progress.emit("Converter bundle invalid. Re-downloading...")
                bundle_dir = get_converter_bundle_dir()
                if bundle_dir:
                    shutil.rmtree(bundle_dir, ignore_errors=True)
                python_path = ensure_converter_bundle(
                    progress_cb=self._bundle_progress,
                    cancel_cb=self._is_cancelled
                )
                ok, detail = self._check_python_deps(python_path)
            if not ok:
                if self.use_bundle:
                    bundle_dir = get_converter_bundle_dir()
                    if bundle_dir:
                        detail = f"{detail}\n\nBundle cache:\n  {bundle_dir}"
                self.finished.emit(False, detail)
                return

            output_dir = os.path.join(self.model_dir, "_ct2_conversion")
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir, ignore_errors=True)
            os.makedirs(output_dir, exist_ok=True)

            weight_files = scan_transformers_weights(self.model_dir)
            if weight_files:
                total = 0
                for name in weight_files:
                    path = os.path.join(self.model_dir, name)
                    try:
                        total += os.path.getsize(path)
                    except Exception:
                        continue
                self._total_input_bytes = total
                self._last_metric_time = time.time()
                self._last_output_size = 0

            self.progress.emit("Converting model (this can take a while)...")
            command = [
                python_path,
                "-m",
                "ctranslate2.converters.transformers",
                "--model",
                self.model_dir,
                "--output_dir",
                output_dir,
                "--force",
            ]
            success, message = self._run_command(command)
            if not success:
                self.finished.emit(False, message)
                return

            self.progress.emit("Finalizing model files...")
            for name in os.listdir(output_dir):
                source_path = os.path.join(output_dir, name)
                dest_path = os.path.join(self.model_dir, name)
                if os.path.isdir(dest_path):
                    shutil.rmtree(dest_path, ignore_errors=True)
                elif os.path.exists(dest_path):
                    os.remove(dest_path)
                shutil.move(source_path, dest_path)
            shutil.rmtree(output_dir, ignore_errors=True)

            if not os.path.isfile(model_bin):
                self.finished.emit(False, "Conversion finished but model.bin was not created.")
                return

            self.finished.emit(True, "Conversion completed successfully.")
        except Exception as exc:
            logging.error(f"Model conversion failed: {exc}")
            self.finished.emit(False, f"Conversion failed: {exc}")


class YtDlpVersionCheckWorker(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, url, timeout=5, headers=None, parent=None):
        super().__init__(parent)
        self.url = url
        self.timeout = timeout
        self.headers = headers or {}

    def run(self):
        result = {"ok": False, "status_code": None, "latest_version": None, "error": None}
        try:
            response = requests.get(self.url, timeout=self.timeout, headers=self.headers)
            result["status_code"] = response.status_code
            if response.status_code == 200:
                data = response.json()
                result["latest_version"] = data.get("tag_name")
            result["ok"] = True
        except Exception as exc:
            result["error"] = str(exc)
        self.finished.emit(result)


class ConverterBundleRepairWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    def run(self):
        try:
            self.progress.emit("Clearing converter bundle cache...", -1)
            bundle_dir = get_converter_bundle_dir()
            if bundle_dir and os.path.isdir(bundle_dir):
                shutil.rmtree(bundle_dir, ignore_errors=True)
            if self.stop_requested:
                self.finished.emit(False, "Repair cancelled.")
                return

            def progress_cb(message, downloaded, total):
                if self.stop_requested:
                    return
                percent = -1
                if total:
                    try:
                        percent = int((downloaded / total) * 100)
                    except Exception:
                        percent = -1
                self.progress.emit(message, percent)

            ensure_converter_bundle(progress_cb=progress_cb, cancel_cb=lambda: self.stop_requested)
            if self.stop_requested:
                self.finished.emit(False, "Repair cancelled.")
            else:
                self.finished.emit(True, "Converter bundle repaired.")
        except Exception as exc:
            if self.stop_requested:
                self.finished.emit(False, "Repair cancelled.")
            else:
                self.finished.emit(False, str(exc))


class ConverterBundleVerifyWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    def _build_env(self):
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)
        return env

    def run(self):
        try:
            bundle_dir = get_converter_bundle_dir()
            python_path = get_converter_python_path(bundle_dir)
            if not python_path or not os.path.isfile(python_path):
                self.finished.emit(False, "Converter bundle not installed.")
                return
            self.progress.emit("Verifying converter bundle...")
            result = run_hidden_subprocess(
                [
                    python_path,
                    "-c",
                    (
                        "import ctranslate2, transformers, torch, safetensors;"
                        "import ctranslate2.converters.transformers;"
                        "print('ctranslate2', ctranslate2.__version__);"
                        "print('transformers', transformers.__version__);"
                        "print('torch', torch.__version__);"
                        "print('safetensors', safetensors.__version__)"
                    )
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=self._build_env(),
            )
            if result.returncode == 0:
                self.finished.emit(True, "Converter bundle is ready.")
            else:
                detail = (result.stderr or result.stdout or "").strip()
                if not detail:
                    detail = "Missing required packages."
                self.finished.emit(False, detail)
        except Exception as exc:
            if self.stop_requested:
                self.finished.emit(False, "Verification cancelled.")
            else:
                self.finished.emit(False, str(exc))


class LoudnessAnalysisWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object, object, bool)

    def __init__(self, ffmpeg_path, file_paths, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.file_paths = list(file_paths)
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    def run(self):
        results = []
        failed = []
        total = len(self.file_paths)
        for index, path in enumerate(self.file_paths, start=1):
            if self.stop_requested:
                break
            self.progress.emit(index, total, os.path.basename(path))
            data, error = self._analyze_loudness_file(path)
            if data is None:
                failed.append((path, error))
            else:
                results.append((path, data))
        canceled = self.stop_requested
        self.progress.emit(total, total, "Done")
        self.finished.emit(results, failed, canceled)

    def _analyze_loudness_file(self, input_file):
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-i",
            input_file,
            "-filter_complex",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
        result = run_hidden_subprocess(command, capture_output=True, text=True)
        output = (result.stderr or "") + (result.stdout or "")
        match = re.findall(r"\{.*?\}", output, flags=re.DOTALL)
        if not match:
            return None, "parse_failed"
        try:
            return json.loads(match[-1]), None
        except json.JSONDecodeError:
            return None, "decode_failed"


class AudioPreprocessWorker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, ffmpeg_path, input_file, output_dir, filters, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.input_file = input_file
        self.output_dir = output_dir
        self.filters = list(filters or [])
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    def run(self):
        if not self.filters:
            self.finished.emit({"ok": True, "path": self.input_file})
            return
        base_name = os.path.splitext(os.path.basename(self.input_file))[0]
        stamp = int(time.time() * 1000)
        output_path = os.path.join(self.output_dir, f"{base_name}_preprocessed_{stamp}.wav")
        command = [
            self.ffmpeg_path,
            "-y",
            "-i",
            self.input_file,
            "-filter_complex",
            ",".join(self.filters),
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
        result = run_hidden_subprocess(command, capture_output=True, text=True)
        if self.stop_requested:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            self.finished.emit({"ok": False, "error": "canceled"})
            return
        if result.returncode != 0:
            snippet = (result.stderr or result.stdout or "").strip()
            if snippet:
                snippet = snippet.splitlines()[-1]
            self.finished.emit({"ok": False, "error": snippet or "ffmpeg failed"})
            return
        self.finished.emit({"ok": True, "path": output_path})


class VerifyModelsWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)

    def __init__(self, executable_path, checks, audio_path, device_type, compute_type, parent=None):
        super().__init__(parent)
        self.executable_path = executable_path
        self.checks = list(checks)
        self.audio_path = audio_path
        self.device_type = device_type
        self.compute_type = compute_type
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        results = []
        total = len(self.checks)
        for index, item in enumerate(self.checks, start=1):
            if self._cancel_event.is_set():
                results.append((item["name"], "Cancelled", "Verification cancelled by user."))
                break
            self.progress.emit(index, total, item["name"])
            temp_dir = tempfile.mkdtemp(prefix="fw-verify-")
            cmd = [
                self.executable_path,
                "--check_files",
                "-m",
                item["name"],
                "--model_dir",
                item["model_dir_cli"],
                "--device",
                self.device_type,
                "--compute_type",
                self.compute_type,
                "--output_dir",
                temp_dir,
                self.audio_path,
            ]
            try:
                result = run_hidden_subprocess(cmd, capture_output=True, text=True, timeout=45)
            except Exception as exc:
                results.append((item["name"], "Failed", f"Failed to run check: {exc}"))
                shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
            output = output.strip()
            shutil.rmtree(temp_dir, ignore_errors=True)
            if result.returncode == 0:
                results.append((item["name"], "OK", output or "Model check passed."))
            else:
                message = output or "Model check failed."
                if result.returncode == -1073741819:
                    message += (
                        "\nThe backend crashed while loading the model. This usually means the model "
                        "conversion is incompatible with the bundled faster-whisper-xxl binary."
                    )
                results.append((item["name"], "Failed", message))
        self.finished.emit(results)
