import os
import logging
import subprocess
import yt_dlp
from PyQt6.QtCore import pyqtSignal, QThread
from utils import popen_hidden_subprocess
from ytdlp_utils import log_ytdlp_update_debug
from python_utils import refresh_python_detection_cache

class YouTubeDownloader(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, url, output_path, audio_only=True):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.audio_only = audio_only
        self.stop_requested = False

    def progress_hook(self, d):
        if self.stop_requested:
            raise yt_dlp.utils.DownloadError("Download cancelled by user.")
        if d['status'] == 'downloading':
            # Sanitize the output to prevent weird formatting issues
            percent_str = d.get('_percent_str', 'N/A').strip()
            total_bytes_str = d.get('_total_bytes_str', 'N/A').strip()
            speed_str = d.get('_speed_str', 'N/A').strip()
            self.progress.emit(f"Downloading: {percent_str} of {total_bytes_str} at {speed_str}")
        elif d['status'] == 'finished':
            self.progress.emit("Download finished, now processing...")

    def run(self):
        try:
            output_template = os.path.join(self.output_path, '%(title)s.%(ext)s')
            if self.audio_only:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'outtmpl': output_template,
                    'noplaylist': True,
                    'progress_hooks': [self.progress_hook],
                    'logger': logging.getLogger('yt_dlp'),
                }
            else:
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': output_template,
                    'noplaylist': True,
                    'progress_hooks': [self.progress_hook],
                    'logger': logging.getLogger('yt_dlp'),
                }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(self.url, download=True)
                final_filename = ydl.prepare_filename(info_dict)
                
                if self.audio_only:
                    base, _ = os.path.splitext(final_filename)
                    final_filename = base + '.mp3'
                
                if not os.path.exists(final_filename):
                    raise FileNotFoundError(f"Post-processing failed. Expected file not found: {final_filename}")

                self.finished.emit(final_filename)
        except Exception as e:
            logging.error(f"yt-dlp thread error: {e}", exc_info=True)
            self.error.emit(str(e))

    def stop(self):
        self.stop_requested = True


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
