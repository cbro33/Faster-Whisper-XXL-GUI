import os
import sys
import logging
import requests
import webbrowser
import shutil
import tempfile
import threading
import re
from PyQt6.QtWidgets import (
    QDialog,
    QProgressBar,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QWidget,
    QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from utils import get_window_stays_on_top_flag, run_hidden_subprocess, get_app_directory
from gpu_utils import detect_hardware_capabilities, get_recommended_settings
from config import SUPPORTED_EXTENSIONS

def create_always_on_top_message_box(parent, icon, title, text, buttons=None, default_button=None):
    """ Create a message box that always stays on top """
    msg = QMessageBox(parent)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText(text)
    
    # Set always on top flag with compatibility
    stay_on_top_flag = get_window_stays_on_top_flag()
    if stay_on_top_flag is not None:
        try:
            msg.setWindowFlags(msg.windowFlags() | stay_on_top_flag)
        except Exception as e:
            logging.warning(f"Could not set always-on-top flag: {e}")
    
    if buttons:
        msg.setStandardButtons(buttons)
    if default_button:
        msg.setDefaultButton(default_button)
    
    # Ensure dialog appears on top and is activated
    msg.activateWindow()
    msg.raise_()
    
    return msg


def show_setup_critical(parent, title, text):
    """ Show critical error dialog for setup that stays on top """
    msg = create_always_on_top_message_box(parent, QMessageBox.Icon.Critical, title, text)
    return msg.exec()


def show_setup_warning(parent, title, text):
    """ Show warning dialog for setup that stays on top """
    msg = create_always_on_top_message_box(parent, QMessageBox.Icon.Warning, title, text)
    return msg.exec()


def show_setup_information(parent, title, text):
    """ Show information dialog for setup that stays on top """
    msg = create_always_on_top_message_box(parent, QMessageBox.Icon.Information, title, text)
    
    # Prevent event cascading by temporarily blocking further processing
    if hasattr(parent, 'blockSignals'):
        parent.blockSignals(True)
    
    try:
        result = msg.exec()
        # Small delay to prevent immediate event cascading
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        return result
    finally:
        if hasattr(parent, 'blockSignals'):
            parent.blockSignals(False)


def show_setup_question(parent, title, text, buttons, default_button=None):
    """ Show question dialog for setup that stays on top """
    msg = create_always_on_top_message_box(parent, QMessageBox.Icon.Question, title, text, buttons, default_button)
    return msg.exec()


_MODEL_DEBUG_ENABLED = False


def set_model_download_logging_enabled(enabled):
    global _MODEL_DEBUG_ENABLED
    _MODEL_DEBUG_ENABLED = bool(enabled)
    if _MODEL_DEBUG_ENABLED:
        get_model_download_logger()


def get_model_download_log_path():
    log_dir = os.path.join(get_app_directory(), "logs")
    return os.path.join(log_dir, "debug_log.txt")


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        message = re.sub(r"[A-Za-z]:\\\\[^\\s]+", "<path>", message)
        message = re.sub(r"\\\\\\\\[^\\s]+", "<path>", message)
        message = re.sub(r"(?<![A-Za-z0-9])/(?:[^\\s]+)", "<path>", message)
        return message


def get_model_download_logger():
    logger = logging.getLogger("model_download")
    if not _MODEL_DEBUG_ENABLED:
        logger.disabled = True
        logger.propagate = False
        if not getattr(logger, "_null_configured", False):
            logger.addHandler(logging.NullHandler())
            logger._null_configured = True
        return logger
    if getattr(logger, "_configured", False):
        logger.disabled = False
        return logger
    log_dir = os.path.join(get_app_directory(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = get_model_download_log_path()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = RedactingFormatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger._configured = True
    logger.disabled = False
    logger.info("Model download logging initialized")
    return logger

def show_yt_dlp_unavailable(parent, reason="python_missing", plan=None):
    """ Show informative dialog when automatic updates are not possible """
    if reason == "pip_missing":
        python_info = plan.get("python_info") if plan else None
        python_label = python_info.get("display_name") if python_info else "Python"
        extra = f"Detected interpreter: {python_label}"
        text = (
            "Python was detected, but pip is missing so yt-dlp cannot be updated automatically.\n\n"
            "To fix this, run `python -m ensurepip --upgrade` for the interpreter above (or reinstall Python with pip)," 
            "then restart this application.\n\n"
            f"{extra}\n"
            "Would you like to open the Python download page?"
        )
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        default = QMessageBox.StandardButton.No
        icon = QMessageBox.Icon.Warning
    elif reason == "target_unwritable":
        target_dir = plan.get("target_directory") if plan else None
        text = (
            "We could not prepare a writable folder for updated yt-dlp files, so the update was aborted.\n\n"
            "Please ensure you have write permissions to the application settings directory"
        )
        if target_dir:
            text += f"\nTarget directory: {target_dir}"
        buttons = QMessageBox.StandardButton.Ok
        default = QMessageBox.StandardButton.Ok
        icon = QMessageBox.Icon.Critical
    else:
        text = (
            "Your system doesn't have Python installed, so yt-dlp cannot be updated automatically.\n\n"
            "To update yt-dlp, you have these options:\n\n"
            "• Install Python from python.org and restart this application\n"
            "• Download a newer version of this application (may include updated yt-dlp)\n"
            "• Continue using the current version (may have limitations with some videos)\n\n"
            "Would you like to open the Python download page?"
        )
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        default = QMessageBox.StandardButton.No
        icon = QMessageBox.Icon.Information

    msg = create_always_on_top_message_box(
        parent,
        icon,
        "yt-dlp Update Not Available",
        text,
        buttons,
        default
    )

    reply = msg.exec()
    if buttons != QMessageBox.StandardButton.Ok and reply == QMessageBox.StandardButton.Yes:
        import webbrowser
        webbrowser.open("https://python.org/downloads/")

class UpdateProgressDialog(QDialog):
    """Progress dialog for yt-dlp updates with cancellation support"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Updating yt-dlp")
        self.setModal(True)
        self.setMinimumSize(400, 150)
        
        # Make dialog stay on top during update
        stay_on_top_flag = get_window_stays_on_top_flag()
        if stay_on_top_flag is not None:
            try:
                self.setWindowFlags(self.windowFlags() | stay_on_top_flag)
            except Exception as e:
                logging.warning(f"Could not set always-on-top flag for UpdateProgressDialog: {e}")
        
        self.setup_ui()
        self.activateWindow()
        self.raise_()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Status label
        self.status_label = QLabel("Preparing to update yt-dlp...", self)
        layout.addWidget(self.status_label)
        
        # Progress bar (indeterminate for now)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        layout.addWidget(self.progress_bar)
        
        # Cancel button
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        
        # Center the cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
    
    def update_progress(self, message):
        """Update the progress message"""
        self.status_label.setText(message)
        # Process events to ensure UI updates
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
    
    def set_completion_state(self, success, message=None):
        """Set dialog state when update completes"""
        if success:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            self.status_label.setText("Update completed successfully!")
            self.cancel_button.setText("Close")
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            if message:
                self.status_label.setText(f"Update failed: {message}")
            else:
                self.status_label.setText("Update failed")
            self.cancel_button.setText("Close")


class DownloadManager(QDialog):
    download_progress = pyqtSignal(int, int, str)
    extraction_progress = pyqtSignal(int, int, str)
    error_occurred = pyqtSignal(str)
    download_finished_signal = pyqtSignal()
    extraction_finished_signal = pyqtSignal()

    def __init__(self, url, files_to_extract, destination_dir, parent=None):
        super().__init__(parent)
        self.url = url
        self.files_to_extract = files_to_extract
        self.destination_dir = destination_dir
        self.error_string = None
        self.archive_path = "whisper_essentials.7z"
        self.worker_thread = None
        self.cancelled = False
        self.thread = None
        self.keep_archive_on_error = False
        self.release_page_url = "https://github.com/Purfview/whisper-standalone-win/releases/tag/Faster-Whisper-XXL"
        self.windows_download_url = (
            "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/"
            "Faster-Whisper-XXL_r245.4_windows.7z"
        )
        self.linux_download_url = (
            "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/"
            "Faster-Whisper-XXL_r245.4_linux.7z"
        )

        self.setWindowTitle("Setup Progress")
        self.setModal(True)
        
        # Make dialog stay on top during setup
        stay_on_top_flag = get_window_stays_on_top_flag()
        if stay_on_top_flag is not None:
            try:
                self.setWindowFlags(self.windowFlags() | stay_on_top_flag)
            except Exception as e:
                logging.warning(f"Could not set always-on-top flag for DownloadManager: {e}")
        self.activateWindow()
        self.raise_()
        layout = QVBoxLayout(self)
        self.status_label = QLabel(f"Downloading from: {self.url}", self)
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar(self)
        layout.addWidget(self.progress_bar)
        self.details_label = QLabel("Initializing...", self)
        layout.addWidget(self.details_label)

        self.manual_instructions_label = QLabel(self)
        self.manual_instructions_label.setWordWrap(True)
        manual_text = (
            "Manual setup (if needed):\n"
            "1) Download the archive for your OS.\n"
            f"2) Extract it and copy the contents into:\n{self.destination_dir}\n"
            "3) Relaunch the app."
        )
        self.manual_instructions_label.setText(manual_text)
        layout.addWidget(self.manual_instructions_label)

        manual_buttons_layout = QHBoxLayout()
        self.open_release_page_button = QPushButton("Open Release Page", self)
        self.open_windows_download_button = QPushButton("Download Windows", self)
        self.open_linux_download_button = QPushButton("Download Linux", self)
        manual_buttons_layout.addWidget(self.open_release_page_button)
        manual_buttons_layout.addWidget(self.open_windows_download_button)
        manual_buttons_layout.addWidget(self.open_linux_download_button)
        layout.addLayout(manual_buttons_layout)

        self.cancel_button = QPushButton("Cancel", self)
        layout.addWidget(self.cancel_button)
        
        self.cancel_button.clicked.connect(self.cancel)
        self.open_release_page_button.clicked.connect(
            lambda: webbrowser.open(self.release_page_url)
        )
        self.open_windows_download_button.clicked.connect(
            lambda: webbrowser.open(self.windows_download_url)
        )
        self.open_linux_download_button.clicked.connect(
            lambda: webbrowser.open(self.linux_download_url)
        )
        self.download_progress.connect(self.update_download_progress)
        self.extraction_progress.connect(self.update_extraction_progress)
        self.error_occurred.connect(self.on_error)
        self.download_finished_signal.connect(self.start_extraction)
        self.extraction_finished_signal.connect(self.on_extraction_finished)

        QTimer.singleShot(100, self.start_download)

    def start_download(self):
        self.keep_archive_on_error = False
        if os.path.exists(self.archive_path) and os.path.getsize(self.archive_path) > 0:
            self.details_label.setText("Using existing download archive...")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            self.download_finished_signal.emit()
            return
        self.details_label.setText("Starting download...")
        import threading
        self.worker_thread = threading.Thread(target=self.download_worker, daemon=True)
        self.worker_thread.start()

    def download_worker(self):
        try:
            response = requests.get(self.url, stream=True, timeout=15)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))

            downloaded_size = 0
            with open(self.archive_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self.cancelled:
                        self.cleanup_archive()
                        return
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    status_text = f"{downloaded_size / (1024*1024):.2f} MB / {total_size / (1024*1024):.2f} MB"
                    self.download_progress.emit(downloaded_size, total_size, status_text)
            
            if not self.cancelled:
                self.download_finished_signal.emit()
        except Exception as e:
            self.error_occurred.emit(f"Download failed: {e}")

    def update_download_progress(self, value, total, text):
        if self.progress_bar.maximum() != total:
            self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(value)
        self.details_label.setText(text)

    def start_extraction(self):
        self.status_label.setText("Extracting files...")
        self.progress_bar.setValue(0)
        self.details_label.setText("Preparing to extract...")
        import threading
        self.worker_thread = threading.Thread(target=self.extraction_worker, daemon=True)
        self.worker_thread.start()

    def extraction_worker(self):
        # Use secure temporary directory instead of hardcoded name
        extract_dir = tempfile.mkdtemp(prefix="whisper_extract_")
        try:
            logging.info("--- Starting Extraction ---")
            if os.path.exists(extract_dir):
                logging.info(f"Removing existing temp directory: {extract_dir}")
                shutil.rmtree(extract_dir)
            os.makedirs(extract_dir, exist_ok=True)
            logging.info(f"Created temp directory: {extract_dir}")

            sevenzip_executable = shutil.which('7z')
            if not sevenzip_executable and sys.platform == "win32":
                prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
                prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
                possible_paths = [
                    os.path.join(prog_files, "7-Zip", "7z.exe"),
                    os.path.join(prog_files_x86, "7-Zip", "7z.exe")
                ]
                for path in possible_paths:
                    if os.path.exists(path):
                        sevenzip_executable = path
                        break
            if not sevenzip_executable:
                error_msg = ("7-Zip/p7zip executable not found. Please install it and ensure it's in your system's PATH. "
                             "On Windows, install from https://www.7-zip.org/. On Linux, use e.g., "
                             "'sudo apt install p7zip-full'.")
                raise FileNotFoundError(error_msg)

            logging.info(f"Using 7-Zip executable: {sevenzip_executable}")
            self.extraction_progress.emit(0, 0, "Extracting archive using 7-Zip... (This may take a moment)")
            command = [sevenzip_executable, 'x', self.archive_path, f'-o{extract_dir}', '-y']
            logging.info(f"Executing command: {' '.join(command)}")
            result = run_hidden_subprocess(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if result.returncode != 0:
                logging.error(f"7-Zip failed with code {result.returncode}\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
                raise RuntimeError(f"7-Zip extraction failed. Error: {result.stderr or result.stdout}")
            logging.info("Extraction complete.")

            self.extraction_progress.emit(1, 2, "Finalizing installation...")

            extracted_items = os.listdir(extract_dir)
            logging.info(f"Items in temp_extract: {extracted_items}")

            source_dir = None
            for item in extracted_items:
                path = os.path.join(extract_dir, item)
                if os.path.isdir(path):
                    source_dir = path
                    break
            if not source_dir:
                if any(f in extracted_items for f in self.files_to_extract):
                    source_dir = extract_dir
                else:
                    raise FileNotFoundError(f"Extraction failed: Could not find a source directory or required files within {extract_dir}.")

            logging.info(f"Source directory for moving files: {source_dir}")

            os.makedirs(self.destination_dir, exist_ok=True)
            logging.info(f"Ensured destination directory exists: {self.destination_dir}")

            for item_name in os.listdir(source_dir):
                source_path = os.path.join(source_dir, item_name)
                dest_path = os.path.join(self.destination_dir, item_name)
                logging.info(f"Moving '{source_path}' to '{dest_path}'")
                
                if os.path.isdir(dest_path):
                    shutil.rmtree(dest_path)
                elif os.path.exists(dest_path):
                    os.remove(dest_path)
                
                shutil.move(source_path, dest_path)

            self.extraction_progress.emit(2, 2, "Verifying files...")
            logging.info("Verifying extracted files...")

            for filename in self.files_to_extract:
                final_path = os.path.join(self.destination_dir, filename)
                if not os.path.exists(final_path) or os.path.getsize(final_path) == 0:
                    raise FileNotFoundError(f"Verification failed: '{filename}' is missing or empty in '{self.destination_dir}' after extraction.")
                logging.info(f"Verified '{final_path}' successfully.")

            if not self.cancelled:
                self.extraction_finished_signal.emit()
                logging.info("--- Extraction Successful ---")

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logging.error(f"--- Extraction Failed ---\n{error_details}")
            self.keep_archive_on_error = True
            self.error_occurred.emit(f"Extraction process failed: {e}")
        finally:
            self.cleanup_archive_and_dir(extract_dir)

    def cleanup_archive_and_dir(self, dir_path):
        if not self.keep_archive_on_error:
            self.cleanup_archive()
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path, ignore_errors=True)

    def update_extraction_progress(self, value, total, text):
        if self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(value)
        self.details_label.setText(text)

    def on_extraction_finished(self):
        self.accept()

    def on_error(self, message):
        self.error_string = message
        self.reject()

    def cancel(self):
        if not self.cancelled:
            self.cancelled = True
            self.status_label.setText("Cancelling...")
            self.cancel_button.setEnabled(False)
            self.error_string = "User cancelled."
            self.reject()

    def cleanup_archive(self):
        if os.path.exists(self.archive_path):
            os.remove(self.archive_path)

    def reject(self):
        self.cleanup_archive_and_dir("temp_extract")
        super().reject()


class ModelDownloadDialog(QDialog):
    download_progress = pyqtSignal(object, object, str)
    download_finished_signal = pyqtSignal()
    error_occurred = pyqtSignal(str)
    _debug_counter = 0

    def __init__(self, model_name, target_dir, parent=None):
        super().__init__(parent)
        self.model_name = model_name
        self.target_dir = target_dir
        self.repo_id = f"Systran/faster-whisper-{model_name}"
        self.cancelled = False
        self.worker_thread = None
        self._active_response = None
        self.file_sizes = {}  # Cache for API sizes
        self._logged_zero_total = set()
        self._debug_logger = get_model_download_logger()
        ModelDownloadDialog._debug_counter += 1
        self._debug_id = f"model-dialog-{ModelDownloadDialog._debug_counter}"
        self._progress_total = None
        self._progress_scale = 1
        self._debug_logger.info("[%s] init model=%s", self._debug_id, self.model_name)

        self.setWindowTitle("Model Download")
        self.setModal(True)
        self.setMinimumSize(520, 180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        stay_on_top_flag = get_window_stays_on_top_flag()
        if stay_on_top_flag is not None:
            try:
                self.setWindowFlags(self.windowFlags() | stay_on_top_flag)
            except Exception as e:
                logging.warning(f"Could not set always-on-top flag: {e}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(10)

        # 1. Header
        self.status_label = QLabel(f"Downloading model: {self.model_name}", self)
        self.status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        layout.addWidget(self.status_label)

        layout.addSpacing(5)

        # 2. Info Row (Filename ... Size/Percentage)
        info_layout = QHBoxLayout()
        self.file_label = QLabel("Initializing...", self)
        self.file_label.setStyleSheet("color: #dddddd; font-size: 14px;")
        
        self.details_label = QLabel("Connecting...", self)
        self.details_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.details_label.setStyleSheet("color: #dddddd; font-size: 14px; font-weight: bold; font-family: 'Consolas', 'Courier New', monospace;")
        
        info_layout.addWidget(self.file_label)
        info_layout.addStretch()
        info_layout.addWidget(self.details_label)
        layout.addLayout(info_layout)

        # 3. Progress Bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimumHeight(10)
        self.progress_bar.setMaximumHeight(10)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.progress_bar)

        # 4. Buttons
        layout.addStretch()
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setMinimumWidth(110)
        self.cancel_button.setObjectName("CancelButton")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setStyleSheet("font-size: 14px;")
        
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)
        
        self.cancel_button.clicked.connect(self.cancel)
        self.download_progress.connect(self.update_download_progress)
        self.download_finished_signal.connect(self.on_download_finished)
        self.error_occurred.connect(self.on_error)
        self.finished.connect(self._log_finished)
        self.rejected.connect(self._log_rejected)
        self.accepted.connect(self._log_accepted)

        QTimer.singleShot(100, self.start_download)

    def _log_finished(self, result):
        self._debug_logger.info(
            "[%s] finished result=%s cancelled=%s", self._debug_id, result, self.cancelled
        )

    def _log_rejected(self):
        self._debug_logger.info("[%s] rejected cancelled=%s", self._debug_id, self.cancelled)

    def _log_accepted(self):
        self._debug_logger.info("[%s] accepted", self._debug_id)

    def cancel(self):
        """Immediately flags the dialog as cancelled and rejects it."""
        if self.cancelled:
            self._debug_logger.info("[%s] cancel ignored (already cancelled)", self._debug_id)
            return
        self.cancelled = True
        self._debug_logger.info(
            "[%s] cancel requested active_response=%s",
            self._debug_id,
            self._active_response is not None,
        )
        
        # Update UI to show cancellation
        self.file_label.setText("Cancelling...")
        self.details_label.setText("")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Closing...")
        
        # Ensure UI updates are processed before rejecting
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # Close the network response to unblock the thread
        if self._active_response is not None:
            try:
                self._active_response.close()
            except Exception:
                pass
        
        self.reject()

    def start_download(self):
        self._debug_logger.info("[%s] start_download", self._debug_id)
        self.worker_thread = threading.Thread(target=self.download_worker, daemon=True)
        self.worker_thread.start()

    def _fetch_file_size(self, filename):
        """Safely get file size from URL, falling back to API if header is missing."""
        url = f"https://huggingface.co/{self.repo_id}/resolve/main/{filename}"
        try:
            # First, try HEAD request for Content-Length
            head_resp = requests.head(url, allow_redirects=True, timeout=5)
            if head_resp.status_code == 200:
                content_length = int(head_resp.headers.get("content-length", 0))
                if content_length > 0:
                    if filename == "model.bin":
                        self._debug_logger.info(
                            "[%s] size head bytes=%s", self._debug_id, content_length
                        )
                    return content_length
        except Exception:
            pass

        # If HEAD failed or gave 0, try GET request and parse content-length or use API
        try:
            get_resp = requests.get(url, stream=True, timeout=5)
            if get_resp.status_code == 200:
                content_length = int(get_resp.headers.get("content-length", 0))
                if content_length > 0:
                    if filename == "model.bin":
                        self._debug_logger.info(
                            "[%s] size get bytes=%s", self._debug_id, content_length
                        )
                    return content_length
                
                # If GET header is still 0, try API
                size = self._fetch_file_size_from_hf_api(filename)
                if filename == "model.bin":
                    self._debug_logger.info(
                        "[%s] size api bytes=%s", self._debug_id, size
                    )
                return size
        except Exception:
            pass
            
        # Final fallback: API lookup if all else fails
        size = self._fetch_file_size_from_hf_api(filename)
        if filename == "model.bin":
            self._debug_logger.info(
                "[%s] size api fallback bytes=%s", self._debug_id, size
            )
        return size

    def _fetch_file_size_from_hf_api(self, filename):
        """Fetch size from HuggingFace API, caching results."""
        cached_size = self.file_sizes.get(filename)
        if cached_size is not None:
            return cached_size
        
        try:
            api_url = f"https://huggingface.co/api/models/{self.repo_id}"
            resp = requests.get(api_url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                for sibling in data.get("siblings", []):
                    fname = sibling.get("rfilename")
                    fsize = sibling.get("size")
                    if fname and fsize:
                        self.file_sizes[fname] = fsize  # Cache it
                        if fname == filename:
                            return fsize
        except Exception:
            logging.warning(f"API fetch failed for {filename}")
            
        return 0  # Return 0 if size is unknown

    def download_worker(self):
        try:
            self._debug_logger.info("[%s] download_worker started", self._debug_id)
            os.makedirs(self.target_dir, exist_ok=True)
            
            # Fetch all sizes at the start
            self.download_progress.emit(0, 0, "Fetching metadata...@@")
            self.file_sizes["config.json"] = self._fetch_file_size("config.json")
            self.file_sizes["tokenizer.json"] = self._fetch_file_size("tokenizer.json")
            self.file_sizes["preprocessor_config.json"] = self._fetch_file_size("preprocessor_config.json")
            self.file_sizes["vocabulary.json"] = self._fetch_file_size("vocabulary.json")
            self.file_sizes["vocabulary.txt"] = self._fetch_file_size("vocabulary.txt")
            self.file_sizes["model.bin"] = self._fetch_file_size("model.bin")
            
            if self.cancelled:
                raise RuntimeError("Cancelled")

            # Download small files
            for filename in ["config.json", "tokenizer.json", "preprocessor_config.json"]:
                if self.cancelled:
                    raise RuntimeError("Cancelled")
                required = (filename != "preprocessor_config.json")
                self._download_file(filename, required=required)

            # Vocabulary
            if self.cancelled:
                raise RuntimeError("Cancelled")
            if not self._download_file("vocabulary.json", required=False):
                self._download_file("vocabulary.txt", required=False)

            # Model Binary
            if self.cancelled:
                raise RuntimeError("Cancelled")
            self._download_model_bin()

            if not self.cancelled:
                self.download_finished_signal.emit()
        except Exception as exc:
            if not self.cancelled:
                self.error_occurred.emit(str(exc))
        finally:
            self._debug_logger.info(
                "[%s] download_worker finished cancelled=%s", self._debug_id, self.cancelled
            )

    def _download_file(self, filename, required):
        if self.cancelled:
            return False
        target_path = os.path.join(self.target_dir, filename)
        
        total = self.file_sizes.get(filename, 0)  # Use cached API size or 0

        # Check if already downloaded
        if os.path.exists(target_path):
            existing_size = os.path.getsize(target_path)
            if total > 0 and existing_size == total:
                return True
            if total == 0 and existing_size > 0:
                return True  # Assume good if size is unknown but file exists
            if total == 0 and existing_size == 0:
                pass  # Re-download if both are zero

        url = f"https://huggingface.co/{self.repo_id}/resolve/main/{filename}"
        self.download_progress.emit(0, total, f"{filename}@@init")
        if filename != "model.bin":
            self._debug_logger.info(
                "[%s] aux download start total=%s", self._debug_id, total
            )
        
        response = requests.get(url, stream=True, timeout=30)
        self._active_response = response
        if response.status_code == 404:
            if required:
                raise FileNotFoundError(f"{filename} not found.")
            return False
        response.raise_for_status()
        
        # If total is still 0, use header size if available
        if total == 0:
            total = int(response.headers.get("content-length", 0))
            if filename != "model.bin":
                self._debug_logger.info(
                    "[%s] aux download header total=%s", self._debug_id, total
                )

        downloaded = 0
        self.download_progress.emit(0, total, f"{filename}@@downloading")
        
        with open(target_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if self.cancelled:
                    handle.close()
                    if os.path.exists(target_path):
                        os.remove(target_path)
                    return False
                if chunk:
                    handle.write(chunk)
                    downloaded += len(chunk)
                    self.download_progress.emit(downloaded, total, f"{filename}@@downloading")
        return True

    def _download_model_bin(self):
        if self.cancelled:
            return
        target_path = os.path.join(self.target_dir, "model.bin")
        filename = "model.bin"
        
        total = self.file_sizes.get(filename, 0)
        self._debug_logger.info(
            "[%s] model download start total_cached=%s", self._debug_id, total
        )

        if os.path.exists(target_path):
            if total > 0 and os.path.getsize(target_path) == total:
                return
            if total == 0 and os.path.getsize(target_path) > 0:
                return

        url = f"https://huggingface.co/{self.repo_id}/resolve/main/{filename}"
        self.download_progress.emit(0, 0, f"{filename}@@init")
        
        response = requests.get(url, stream=True, timeout=30)
        self._active_response = response
        if response.status_code == 404:
            raise FileNotFoundError(f"{filename} not found.")
        response.raise_for_status()
        
        if total == 0:  # If API size failed, try header size
            total = int(response.headers.get("content-length", 0))
            self._debug_logger.info(
                "[%s] model download header total=%s", self._debug_id, total
            )

        downloaded = 0
        self.download_progress.emit(0, total, f"{filename}@@downloading")
        
        with open(target_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if self.cancelled:
                    handle.close()
                    if os.path.exists(target_path):
                        os.remove(target_path)
                    return
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                self.download_progress.emit(downloaded, total, f"{filename}@@downloading")

    def format_size(self, size_in_bytes):
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_in_bytes)
        for unit in units:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def update_download_progress(self, value, total, text):
        if self.cancelled:
            return
        
        file_name = None
        if "@@" in text:
            file_name, _ = text.split("@@", 1)
            self.file_label.setText(file_name)
        
        if total > 0:
            max_qt_int = 2_147_483_647
            scale = 1 if total <= max_qt_int else 1024 * 1024
            scaled_total = max(1, int(total // scale))
            scaled_value = max(0, int(value // scale))
            if scaled_value > scaled_total:
                scaled_value = scaled_total
            if self._progress_total != scaled_total or self._progress_scale != scale:
                self.progress_bar.setRange(0, scaled_total)
                self._progress_total = scaled_total
                self._progress_scale = scale
            self.progress_bar.setValue(scaled_value)

            percent = int((value / total) * 100)
            current_str = self.format_size(value)
            total_str = self.format_size(total)
            
            self.details_label.setText(f"{percent}%  •  {current_str} / {total_str}")
        else:
            # Fallback if total size is unknown
            self.progress_bar.setRange(0, 0)
            if value > 0:
                self.details_label.setText(f"{self.format_size(value)} downloaded")
                if not self._logged_zero_total:
                    self._logged_zero_total.add("seen")
                    self._debug_logger.info(
                        "[%s] total unknown value=%s", self._debug_id, value
                    )
            else:
                self.details_label.setText("Connecting...")

    def on_download_finished(self):
        if self.cancelled:
            return
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.file_label.setText("Download complete")
        self.details_label.setText("100%")
        self._debug_logger.info("[%s] download_finished", self._debug_id)
        self.accept()

    def on_error(self, message):
        if self.cancelled:
            return  # Don't show error if we cancelled it
        self.error_string = message
        self.file_label.setText("Error occurred")
        self.details_label.setText("Failed")
        self._debug_logger.info("[%s] error message=%s", self._debug_id, message)
        self.reject()


class HardwareOptimizationDialog(QDialog):
    """Dialog for showing hardware detection results and optimization recommendations"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hardware Optimization")
        self.setModal(True)
        self.setMinimumSize(500, 400)
        
        # Detect hardware
        self.hardware_info = detect_hardware_capabilities()
        self.recommendations = get_recommended_settings(self.hardware_info)
        self.user_accepted = False
        
        self.init_ui()
        
        # Set always on top flag
        stay_on_top_flag = get_window_stays_on_top_flag()
        if stay_on_top_flag is not None:
            try:
                self.setWindowFlags(self.windowFlags() | stay_on_top_flag)
            except Exception as e:
                logging.warning(f"Could not set always-on-top flag: {e}")
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Title
        title_label = QLabel("Hardware Optimization")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(title_label)
        
        # Hardware detection results
        hw_group = QGroupBox("Detected Hardware")
        hw_layout = QVBoxLayout(hw_group)
        
        # GPU info with detection details
        gpu_layout = QHBoxLayout()
        if self.hardware_info["has_cuda"]:
            detection_method = self.hardware_info.get("detection_method", "unknown")
            gpu_text = f"✅ GPU: {self.hardware_info['gpu_name']} ({self.hardware_info['gpu_memory_gb']:.1f}GB VRAM)"
            if detection_method != "unknown":
                gpu_text += f" [Detected via: {detection_method}]"
            gpu_label = QLabel(gpu_text)
        else:
            gpu_text = "⚠️ GPU: No CUDA-compatible GPU detected"
            gpu_label = QLabel(gpu_text)
            
            # Add "Show Details" button for failed detection
            if self.hardware_info.get("detection_details"):
                self.details_button = QPushButton("Show Details")
                self.details_button.clicked.connect(self.show_detection_details)
                gpu_layout.addWidget(gpu_label)
                gpu_layout.addWidget(self.details_button)
                gpu_layout.addStretch()
                hw_layout.addLayout(gpu_layout)
            else:
                hw_layout.addWidget(gpu_label)
        
        if self.hardware_info["has_cuda"]:
            hw_layout.addWidget(gpu_label)
        
        # RAM info
        ram_text = f"✅ System RAM: {self.hardware_info['ram_gb']:.1f}GB"
        ram_label = QLabel(ram_text)
        hw_layout.addWidget(ram_label)
        
        # CPU info
        cpu_text = f"✅ CPU: {self.hardware_info['cpu_cores']} cores detected"
        cpu_label = QLabel(cpu_text)
        hw_layout.addWidget(cpu_label)
        
        layout.addWidget(hw_group)
        
        # Recommendations
        rec_group = QGroupBox("Recommended Settings")
        rec_layout = QFormLayout(rec_group)
        
        # Device recommendation
        device_text = self.recommendations["device"].upper()
        if device_text == "CUDA":
            device_text += " (faster processing)"
        else:
            device_text += " (most compatible)"
        rec_layout.addRow("Device:", QLabel(device_text))
        
        # Model recommendation
        model_text = self.recommendations["model"]
        if model_text == "large-v2":
            model_text += " (highest quality)"
        elif model_text == "medium":
            model_text += " (good balance)"
        else:
            model_text += " (fastest)"
        rec_layout.addRow("Model:", QLabel(model_text))
        
        # Compute type
        compute_text = self.recommendations["compute_type"]
        if compute_text == "float16":
            compute_text += " (best quality)"
        elif compute_text == "int8_float16":
            compute_text += " (balanced)"
        else:
            compute_text += " (memory efficient)"
        rec_layout.addRow("Compute Type:", QLabel(compute_text))
        
        # VAD method
        vad_text = self.recommendations["vad_method"]
        if "pyannote" in vad_text:
            vad_text += " (best accuracy)"
        else:
            vad_text += " (CPU friendly)"
        rec_layout.addRow("VAD Method:", QLabel(vad_text))
        
        layout.addWidget(rec_group)
        
        # Note about large-v2
        note_label = QLabel("Note: large-v2 provides the best transcription quality")
        note_label.setStyleSheet("color: #666; font-style: italic; margin: 10px 0;")
        layout.addWidget(note_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.apply_button = QPushButton("Apply Recommendations")
        self.apply_button.clicked.connect(self.accept_recommendations)
        self.apply_button.setDefault(True)
        
        self.skip_button = QPushButton("Use Defaults")
        self.skip_button.clicked.connect(self.reject)
        
        self.customize_button = QPushButton("Customize Later")
        self.customize_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.customize_button)
        button_layout.addWidget(self.skip_button)
        
        layout.addLayout(button_layout)
    
    def accept_recommendations(self):
        self.user_accepted = True
        self.accept()
    
    def show_detection_details(self):
        """ Show detailed GPU detection troubleshooting information """
        details_text = "GPU Detection Failed - Troubleshooting Information\n\n"
        
        if self.hardware_info.get("detection_details"):
            details_text += "Detection Results:\n"
            for detail in self.hardware_info["detection_details"]:
                details_text += f"• {detail}\n"
        
        details_text += "\nPossible Solutions:\n"
        details_text += "• Install PyTorch with CUDA support\n"
        details_text += "  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121\n\n"
        details_text += "• Update NVIDIA drivers from nvidia.com\n\n"
        details_text += "• Install CUDA Toolkit (if not included with drivers)\n\n"
        details_text += "• Check if GPU is properly connected and powered\n\n"
        details_text += "• Restart computer after driver installation\n\n"
        details_text += "The application will use CPU mode for now, which is slower but still functional."
        
        show_setup_information(self, "GPU Detection Details", details_text)


class FileDropLineEdit(QLineEdit):
    """Custom QLineEdit that accepts audio/video file drops"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Handle drag enter events - accept audio/video files"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and len(urls) == 1:
                file_path = urls[0].toLocalFile()
                # Check if it's a supported audio/video file
                if file_path.lower().endswith(SUPPORTED_EXTENSIONS):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        """Handle file drop events - set dropped file path"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and len(urls) == 1:
                file_path = urls[0].toLocalFile()
                if file_path.lower().endswith(SUPPORTED_EXTENSIONS):
                    self.setText(file_path)
                    event.acceptProposedAction()
                    return
        event.ignore()


class FileDropListWidget(QListWidget):
    """Custom QListWidget that accepts audio/video file or folder drops"""

    def __init__(self, add_files_callback=None, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._add_files_callback = add_files_callback
        self.setDragEnabled(False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(self._is_supported_drop(url.toLocalFile()) for url in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if self._add_files_callback:
                self._add_files_callback(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(self._is_supported_drop(url.toLocalFile()) for url in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def _is_supported_drop(self, path):
        if not path:
            return False
        if os.path.isdir(path):
            return True
        return path.lower().endswith(SUPPORTED_EXTENSIONS)


class LinkDropListWidget(QListWidget):
    """Custom QListWidget that accepts URL drops or pasted text."""

    def __init__(self, add_links_callback=None, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._add_links_callback = add_links_callback
        self.setDragEnabled(False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        links = []
        if event.mimeData().hasUrls():
            links.extend(url.toString() for url in event.mimeData().urls())
        if event.mimeData().hasText():
            links.append(event.mimeData().text())
        if links and self._add_links_callback:
            self._add_links_callback(links)
            event.acceptProposedAction()
            return
        event.ignore()

class FileDropGroupBox(QGroupBox):
    """Group box that accepts audio/video files or folders drops."""

    def __init__(self, title, add_files_callback=None, parent=None):
        super().__init__(title, parent)
        self.setAcceptDrops(True)
        self._add_files_callback = add_files_callback

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(self._is_supported_drop(url.toLocalFile()) for url in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if self._add_files_callback:
                self._add_files_callback(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def _is_supported_drop(self, path):
        if not path:
            return False
        if os.path.isdir(path):
            return True
        return path.lower().endswith(SUPPORTED_EXTENSIONS)


class FileDropWidget(QWidget):
    """Generic widget that accepts audio/video file or folder drops."""

    def __init__(self, add_files_callback=None, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._add_files_callback = add_files_callback

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(self._is_supported_drop(url.toLocalFile()) for url in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if self._add_files_callback:
                self._add_files_callback(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def _is_supported_drop(self, path):
        if not path:
            return False
        if os.path.isdir(path):
            return True
        return path.lower().endswith(SUPPORTED_EXTENSIONS)
