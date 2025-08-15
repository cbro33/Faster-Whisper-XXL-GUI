import sys
import os
import json
import platform
import shutil
import requests
import threading
import logging
import subprocess
import tempfile
from pathlib import Path
from PyQt6.QtWidgets import (QProgressBar, QGridLayout, QDialog, QSplitter, QWidget, 
                             QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QTabWidget, 
                             QGroupBox, QFormLayout, QLineEdit, QPushButton, QCheckBox, 
                             QTextEdit, QDoubleSpinBox, QSpinBox, QScrollArea, QMessageBox, 
                             QProgressDialog, QApplication, QMainWindow, QFileDialog, QCompleter)
from PyQt6.QtCore import pyqtSignal, QThread, Qt, QTimer, QProcess, QByteArray
from PyQt6.QtGui import QIcon, QPalette, QColor, QTextCursor, QFont
import yt_dlp


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


def get_execution_environment():
    """ Detect how the application is running and what update capabilities are available """
    try:
        # Check if running as PyInstaller executable
        is_frozen = getattr(sys, 'frozen', False)
        
        if not is_frozen:
            # Running from source - can always update
            return "source"
        
        # Running as exe - check if system Python is available
        python_commands = ["python", "python3", "py"]
        
        for cmd in python_commands:
            try:
                # Check if Python command exists and has pip
                result = subprocess.run([cmd, "-m", "pip", "--version"], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return "exe_with_python"
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue
        
        # No usable Python found
        return "exe_no_python"
        
    except Exception as e:
        logging.warning(f"Could not determine execution environment: {e}")
        return "unknown"


def can_update_yt_dlp():
    """ Check if yt-dlp can be updated in the current environment """
    env = get_execution_environment()
    return env in ["source", "exe_with_python"]


def get_update_command():
    """ Get the appropriate command to update yt-dlp based on environment """
    env = get_execution_environment()
    
    if env == "source":
        return [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
    elif env == "exe_with_python":
        # Find the best Python command to use
        python_commands = ["python", "python3", "py"]
        for cmd in python_commands:
            try:
                result = subprocess.run([cmd, "-m", "pip", "--version"], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return [cmd, "-m", "pip", "install", "--upgrade", "yt-dlp"]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue
    
    return None


def show_yt_dlp_unavailable(parent):
    """ Show informative dialog for exe users who can't update yt-dlp """
    msg = create_always_on_top_message_box(
        parent, 
        QMessageBox.Icon.Information,
        "yt-dlp Update Not Available",
        "Your system doesn't have Python installed, so yt-dlp cannot be updated automatically.\n\n"
        "To update yt-dlp, you have these options:\n\n"
        "• Install Python from python.org and restart this application\n"
        "• Download a newer version of this application (may include updated yt-dlp)\n"
        "• Continue using the current version (may have limitations with some videos)\n\n"
        "Would you like to open the Python download page?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No
    )
    
    reply = msg.exec()
    if reply == QMessageBox.StandardButton.Yes:
        import webbrowser
        webbrowser.open("https://python.org/downloads/")


def get_ytdlp_installation_info():
    """ Detect which yt-dlp installation is actually being used """
    try:
        import yt_dlp
        current_version = yt_dlp.version.__version__
        
        # Get the location of the yt-dlp module
        ytdlp_path = yt_dlp.__file__
        
        # Determine if it's bundled with exe or system installation
        env = get_execution_environment()
        is_frozen = getattr(sys, 'frozen', False)
        
        if is_frozen:
            # If running as exe, check if yt-dlp is bundled or from system
            exe_dir = os.path.dirname(sys.executable)
            if ytdlp_path.startswith(exe_dir):
                installation_type = "bundled"
            else:
                installation_type = "system"
        else:
            installation_type = "development"
        
        return {
            "version": current_version,
            "path": ytdlp_path,
            "installation_type": installation_type,
            "environment": env
        }
    except ImportError:
        return {
            "version": None,
            "path": None,
            "installation_type": "missing",
            "environment": get_execution_environment()
        }


def load_ytdlp_update_status():
    """ Load persistent yt-dlp update tracking data """
    try:
        portable_dir = get_portable_settings_directory()
        update_file = os.path.join(portable_dir, "ytdlp_updates.json")
        
        if os.path.exists(update_file):
            with open(update_file, "r") as f:
                return json.load(f)
        else:
            # Initialize default structure
            return {
                "ytdlp_updates": {
                    "source": {},
                    "exe_with_python": {},
                    "exe_no_python": {}
                }
            }
    except Exception as e:
        logging.warning(f"Could not load yt-dlp update status: {e}")
        return {
            "ytdlp_updates": {
                "source": {},
                "exe_with_python": {},
                "exe_no_python": {}
            }
        }


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


def should_check_ytdlp_update():
    """ Determine if we should check for yt-dlp updates based on persistent tracking """
    try:
        # Get current installation info
        install_info = get_ytdlp_installation_info()
        env = install_info["environment"]
        current_version = install_info["version"]
        
        if not current_version:
            return True  # yt-dlp not found, should check
        
        # Load persistent update status
        update_data = load_ytdlp_update_status()
        env_data = update_data["ytdlp_updates"].get(env, {})
        
        # Check if we recently updated successfully
        last_updated_version = env_data.get("last_updated_version")
        last_update_timestamp = env_data.get("last_update_timestamp")
        
        # If we have a record of updating to the current version recently, skip
        if (last_updated_version == current_version and 
            is_within_update_cooldown(last_update_timestamp)):
            logging.info(f"yt-dlp update skipped: recently updated to {current_version} in {env} mode")
            return False
        
        # For bundled exe installations, be more conservative
        if install_info["installation_type"] == "bundled":
            # Check if we already informed user about bundled version recently
            last_check = env_data.get("last_bundled_check_timestamp")
            if is_within_update_cooldown(last_check, cooldown_hours=72):  # 3 day cooldown for bundled
                logging.info("Bundled yt-dlp update check skipped: recently informed user")
                return False
        
        return True
        
    except Exception as e:
        logging.error(f"Error checking if should update yt-dlp: {e}")
        return True  # Default to checking if unsure


def record_ytdlp_update_success(updated_version):
    """ Record a successful yt-dlp update """
    try:
        import time
        
        # Get current environment
        env = get_execution_environment()
        
        # Load existing data
        update_data = load_ytdlp_update_status()
        
        # Update the data for current environment
        if env not in update_data["ytdlp_updates"]:
            update_data["ytdlp_updates"][env] = {}
        
        update_data["ytdlp_updates"][env].update({
            "last_updated_version": updated_version,
            "last_update_timestamp": time.time()
        })
        
        # Save the updated data
        save_ytdlp_update_status(update_data)
        
        logging.info(f"Recorded successful yt-dlp update to {updated_version} in {env} mode")
        
    except Exception as e:
        logging.error(f"Could not record yt-dlp update success: {e}")


def record_ytdlp_bundled_check():
    """ Record that we checked/informed about bundled yt-dlp """
    try:
        import time
        
        # Get current environment  
        env = get_execution_environment()
        
        # Load existing data
        update_data = load_ytdlp_update_status()
        
        # Update the data for current environment
        if env not in update_data["ytdlp_updates"]:
            update_data["ytdlp_updates"][env] = {}
        
        update_data["ytdlp_updates"][env]["last_bundled_check_timestamp"] = time.time()
        
        # Save the updated data
        save_ytdlp_update_status(update_data)
        
        logging.info(f"Recorded bundled yt-dlp check in {env} mode")
        
    except Exception as e:
        logging.error(f"Could not record bundled yt-dlp check: {e}")


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


# --- Setup Logging ---
# Create a dedicated logs directory
log_dir = os.path.join(get_app_directory(), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "debug_log.txt")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(module)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file, mode='w', encoding='utf-8'),
        logging.StreamHandler() # Also print to console
    ]
)

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
    
    def __init__(self, update_command, environment):
        super().__init__()
        self.update_command = update_command
        self.environment = environment
        self.stop_requested = False
        
    def run(self):
        try:
            logging.info(f"Starting yt-dlp update with command: {' '.join(self.update_command)}")
            self.progress.emit("Initializing update...")
            
            if self.stop_requested:
                self.finished.emit(False, "Update cancelled by user")
                return
            
            self.progress.emit("Downloading latest yt-dlp version...")
            
            # Run the update command with timeout protection
            result = subprocess.run(
                self.update_command, 
                capture_output=True, 
                text=True, 
                timeout=60  # 60 second timeout
            )
            
            if self.stop_requested:
                self.finished.emit(False, "Update cancelled by user")
                return
            
            if result.returncode == 0:
                success_msg = "yt-dlp has been updated successfully!\nThe new version will be used for future downloads."
                if self.environment == "exe_with_python":
                    success_msg += "\n\nNote: The update was applied to your system Python installation."
                
                logging.info("yt-dlp updated successfully via thread")
                self.finished.emit(True, success_msg)
            else:
                error_msg = f"Failed to update yt-dlp:\n{result.stderr or result.stdout}"
                if self.environment == "exe_with_python":
                    error_msg += "\n\nTry running the update command as administrator or check your Python installation."
                
                logging.error(f"yt-dlp update failed via thread: {result.stderr}")
                self.finished.emit(False, error_msg)
                
        except subprocess.TimeoutExpired:
            logging.warning("yt-dlp update timed out via thread")
            self.finished.emit(False, "yt-dlp update timed out. Please try again later.\nThis may happen if you have a slow internet connection.")
        except Exception as e:
            logging.error(f"yt-dlp update error via thread: {e}")
            self.finished.emit(False, f"An error occurred while updating yt-dlp:\n{str(e)}")
    
    def stop(self):
        """Request the update to stop"""
        self.stop_requested = True


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

        self.cancel_button = QPushButton("Cancel", self)
        layout.addWidget(self.cancel_button)
        
        self.cancel_button.clicked.connect(self.cancel)
        self.download_progress.connect(self.update_download_progress)
        self.extraction_progress.connect(self.update_extraction_progress)
        self.error_occurred.connect(self.on_error)
        self.download_finished_signal.connect(self.start_extraction)
        self.extraction_finished_signal.connect(self.on_extraction_finished)

        QTimer.singleShot(100, self.start_download)

    def start_download(self):
        self.details_label.setText("Starting download...")
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
                             "On Windows, install from 7-zip.org. On Linux, use e.g., 'sudo apt install p7zip-full'.")
                raise FileNotFoundError(error_msg)

            logging.info(f"Using 7-Zip executable: {sevenzip_executable}")
            self.extraction_progress.emit(0, 0, "Extracting archive using 7-Zip... (This may take a moment)")
            command = [sevenzip_executable, 'x', self.archive_path, f'-o{extract_dir}', '-y']
            logging.info(f"Executing command: {' '.join(command)}")
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
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
            self.error_occurred.emit(f"Extraction process failed: {e}")
        finally:
            if not self.error_string:
                self.cleanup_archive_and_dir(extract_dir)

    def cleanup_archive_and_dir(self, dir_path):
        self.cleanup_archive()
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path, ignore_errors=True)

    def update_extraction_progress(self, value, total, text):
        if total == 0:
            self.progress_bar.setRange(0, 0)
        else:
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


class WhisperGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.process = None
        self.downloader = None
        self.stop_requested = False
        self.output_format_checkboxes = {}
        
        # Use portable settings location (same directory as exe/source)
        portable_dir = get_portable_settings_directory()
        self.settings_file = os.path.join(portable_dir, "settings.json")
        self.old_roaming_settings_file = os.path.join(get_settings_directory(), "settings.json")  # For migration FROM roaming
        self.settings = {}
        
        # yt-dlp update tracking to prevent multiple checks
        self.yt_dlp_update_checked = False
        self.yt_dlp_update_in_progress = False
        self.yt_dlp_update_session_complete = False
        
        self.executable_path = None
        self.executable_name = None
        # Use application directory for persistent bin folder location
        app_dir = get_app_directory()
        self.bin_dir = os.path.join(app_dir, "bin")
        
        self.output_buffer = ""
        self.last_line_was_overwrite = False
        self.transcription_completed_successfully = False
        
        if not self.check_and_setup_dependencies():
            QTimer.singleShot(0, self.close)
            return

        self.init_ui()
        self.load_settings()
        self.setup_realtime_saving()
        
        # Check yt-dlp version after UI is ready (only if not already checked this session)
        if not self.yt_dlp_update_checked:
            QTimer.singleShot(1000, self.check_yt_dlp_version)

    def check_and_setup_dependencies(self):
        if sys.platform == "win32":
            self.executable_name = "faster-whisper-xxl.exe"
            url = "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z"
            self.files_to_check = [self.executable_name, "ffmpeg.exe"]
        elif sys.platform in ["linux", "darwin"]:
            self.executable_name = "faster-whisper-xxl"
            url = "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_linux.7z"
            self.files_to_check = [self.executable_name, "ffmpeg"]
        else:
            show_setup_critical(self, "Unsupported OS", f"Your OS '{sys.platform}' is not supported.")
            return False

        local_executable_path = os.path.join(self.bin_dir, self.executable_name)
        all_files_in_bin = all(os.path.exists(os.path.join(self.bin_dir, f)) for f in self.files_to_check)

        if all_files_in_bin:
            self.executable_path = os.path.abspath(local_executable_path)
            logging.info(f"Found all required files in: {self.bin_dir}")
            return True

        path_in_system = shutil.which(self.executable_name)
        if path_in_system:
            self.executable_path = path_in_system
            logging.info(f"Found executable in system PATH: {path_in_system}")
            return True

        reply = show_setup_question(self, "Download Required Files?",
                                f"The core components (e.g., '{self.executable_name}') were not found in the 'bin' directory or system PATH.\n\n"
                                "Would you like to download and set them up automatically? (Approx. 1.4 GB)\n\n"
                                "This is a one-time setup.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.Yes)
        
        if reply == QMessageBox.StandardButton.No:
            show_setup_warning(self, "Setup Incomplete", "Application cannot run without the required files.")
            return False

        self.download_manager = DownloadManager(url, self.files_to_check, self.bin_dir, self)
        if self.download_manager.exec() == QDialog.DialogCode.Accepted:
            self.executable_path = os.path.abspath(local_executable_path)
            if sys.platform != "win32" and os.path.exists(self.executable_path):
                os.chmod(self.executable_path, 0o755)
            show_setup_information(self, "Setup Complete", f"Dependencies have been installed to the '{self.bin_dir}' folder.")
            return True
        else:
            error_message = self.download_manager.error_string or "Download or extraction was cancelled or failed."
            detailed_error = f"Failed to set up dependencies: {error_message}\n\nCheck 'logs/debug_log.txt' for details."
            show_setup_critical(self, "Setup Failed", detailed_error)
            return False

    def init_ui(self):
        self.setWindowTitle("Faster Whisper XXL GUI")
        self.setGeometry(100, 100, 1200, 800)
        self.setMinimumSize(1000, 600)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        central_layout = QHBoxLayout(central_widget)
        central_layout.addWidget(self.main_splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        left_layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        header_label = QLabel("Faster Whisper XXL")
        header_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark", "AMOLED"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        header_layout.addWidget(self.theme_combo)
        left_layout.addLayout(header_layout)

        self.tabs = QTabWidget()
        left_layout.addWidget(self.tabs)

        self.file_tab = QWidget()
        self.setup_file_tab(self.file_tab)
        self.tabs.addTab(self.file_tab, "File")

        self.youtube_tab = QWidget()
        self.setup_youtube_tab(self.youtube_tab)
        self.tabs.addTab(self.youtube_tab, "yt-dlp")

        advanced_tab = QWidget()
        self.setup_advanced_tab(advanced_tab)
        self.tabs.addTab(advanced_tab, "Advanced")

        vad_tab = QWidget()
        self.setup_vad_tab(vad_tab)
        self.tabs.addTab(vad_tab, "VAD")

        audio_tab = QWidget()
        self.setup_audio_tab(audio_tab)
        self.tabs.addTab(audio_tab, "Audio")

        global_settings_group = QGroupBox("Global Settings")
        global_settings_layout = QFormLayout(global_settings_group)
        self.setup_global_settings(global_settings_layout)
        left_layout.addWidget(global_settings_group)

        left_layout.addStretch()

        button_layout = self.create_button_layout()
        left_layout.addLayout(button_layout)

        right_panel = self.create_output_console()

        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setSizes([450, 750])

    def setup_file_tab(self, tab):
        layout = QFormLayout(tab)
        file_input_layout = QHBoxLayout()
        self.file_path = QLineEdit()
        self.file_path.setPlaceholderText("Select or drop an audio/video file...")
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_file)
        file_input_layout.addWidget(self.file_path, 1)
        file_input_layout.addWidget(self.browse_btn)
        layout.addRow("Input File:", file_input_layout)

        output_dir_layout = QHBoxLayout()
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Defaults to 'output' folder")
        self.output_dir_btn = QPushButton("Browse")
        self.output_dir_btn.clicked.connect(self.browse_output_dir)
        output_dir_layout.addWidget(self.output_dir)
        output_dir_layout.addWidget(self.output_dir_btn)
        layout.addRow("Output Directory:", output_dir_layout)

    def setup_youtube_tab(self, tab):
        layout = QFormLayout(tab)
        self.youtube_url = QLineEdit()
        self.youtube_url.setPlaceholderText("Enter YouTube URL...")
        layout.addRow("YouTube URL:", self.youtube_url)
        self.audio_only_checkbox = QCheckBox("Audio-only (Recommended)")
        self.audio_only_checkbox.setObjectName("audio_only_checkbox")
        self.audio_only_checkbox.setToolTip("If checked, only downloads the audio as MP3. Uncheck to download the full video.")
        self.audio_only_checkbox.setChecked(True)
        layout.addRow(self.audio_only_checkbox)

    def setup_global_settings(self, layout):
        self.model_combo = QComboBox()
        self.model_combo.addItems(['tiny', 'base', 'small', 'medium', 'large', 'large-v2', 'large-v3'])
        layout.addRow("Model:", self.model_combo)
        self.task_combo = QComboBox()
        self.task_combo.addItems(['transcribe', 'translate'])
        layout.addRow("Task:", self.task_combo)
        self.language_combo = QComboBox()
        languages = ['auto'] + ['af', 'am', 'ar', 'as', 'az', 'ba', 'be', 'bg', 'bn', 'bo', 'br', 'bs', 'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'es', 'et', 'eu', 'fa', 'fi', 'fo', 'fr', 'gl', 'gu', 'ha', 'haw', 'he', 'hi', 'hr', 'ht', 'hu', 'hy', 'id', 'is', 'it', 'ja', 'jw', 'ka', 'kk', 'km', 'kn', 'ko', 'la', 'lb', 'ln', 'lo', 'lt', 'lv', 'mg', 'mi', 'mk', 'ml', 'mn', 'mr', 'ms', 'mt', 'my', 'ne', 'nl', 'nn', 'no', 'oc', 'pa', 'pl', 'ps', 'pt', 'ro', 'ru', 'sa', 'sd', 'si', 'sk', 'sl', 'sn', 'so', 'sq', 'sr', 'su', 'sv', 'sw', 'ta', 'te', 'tg', 'th', 'tk', 'tl', 'tr', 'tt', 'uk', 'ur', 'uz', 'vi', 'yi', 'yo', 'yue', 'zh']
        self.language_combo.addItems(languages)
        self.language_combo.setEditable(True)
        self.language_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Safe completer access with null check
        if completer := self.language_combo.completer():
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        layout.addRow("Language:", self.language_combo)
        self.compute_combo = QComboBox()
        self.compute_combo.addItems(['default', 'auto', 'int8', 'int8_float16', 'int8_float32', 'int8_bfloat16', 'int16', 'float16', 'float32', 'bfloat16'])
        layout.addRow("Compute Type:", self.compute_combo)
        self.device_combo = QComboBox()
        self.device_combo.addItems(['cuda', 'cpu'])
        layout.addRow("Device:", self.device_combo)
        output_format_group = QWidget()
        container_layout = QHBoxLayout(output_format_group)
        container_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(40)
        grid_layout.setVerticalSpacing(10)
        formats = ['json', 'vtt', 'srt', 'lrc', 'txt', 'tsv', 'all']
        num_rows = 4
        for i, fmt in enumerate(formats):
            checkbox = QCheckBox(fmt)
            checkbox.setObjectName(f"format_checkbox_{fmt}")
            self.output_format_checkboxes[fmt] = checkbox
            row = i % num_rows
            col = i // num_rows
            grid_layout.addWidget(checkbox, row, col)
        container_layout.addLayout(grid_layout)
        container_layout.addStretch()
        self.output_format_checkboxes['all'].toggled.connect(self.handle_all_formats_toggle)
        layout.addRow("Output Format:", output_format_group)

    def create_output_console(self):
        output_group = QGroupBox("Console Output")
        layout = QVBoxLayout(output_group)
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        font = QFont("Courier New" if sys.platform == "win32" else "Monospace")
        font.setPointSize(10)
        self.output_text.setFont(font)
        layout.addWidget(self.output_text)
        return output_group

    def create_button_layout(self):
        button_layout = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.setToolTip("Run the process based on the active tab (File or yt-dlp).")
        self.run_btn.clicked.connect(self.start_processing)
        self.run_btn.setMinimumHeight(40)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_processing)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setMinimumHeight(40)
        button_layout.addWidget(self.run_btn)
        button_layout.addWidget(self.stop_btn)
        return button_layout

    def handle_all_formats_toggle(self, checked):
        all_checkbox = self.output_format_checkboxes['all']
        all_checkbox.blockSignals(True)
        for fmt, checkbox in self.output_format_checkboxes.items():
            if fmt != 'all':
                checkbox.setChecked(checked)
        all_checkbox.blockSignals(False)

    def setup_advanced_tab(self, tab):
        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QFormLayout(scroll_widget)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 1.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setDecimals(1)
        scroll_layout.addRow("Temperature:", self.temperature)
        self.beam_size = QSpinBox()
        self.beam_size.setRange(1, 100)
        scroll_layout.addRow("Beam Size:", self.beam_size)
        self.best_of = QSpinBox()
        self.best_of.setRange(1, 100)
        scroll_layout.addRow("Best Of:", self.best_of)
        self.patience = QDoubleSpinBox()
        self.patience.setRange(0.0, 10.0)
        self.patience.setSingleStep(0.1)
        self.patience.setDecimals(1)
        scroll_layout.addRow("Patience:", self.patience)
        self.initial_prompt = QTextEdit()
        self.initial_prompt.setMaximumHeight(80)
        scroll_layout.addRow("Initial Prompt:", self.initial_prompt)
        self.word_timestamps = QCheckBox("Word Timestamps")
        self.word_timestamps.setObjectName("word_timestamps_checkbox")
        scroll_layout.addRow(self.word_timestamps)
        self.without_timestamps = QCheckBox("Without Timestamps")
        self.without_timestamps.setObjectName("without_timestamps_checkbox")
        scroll_layout.addRow(self.without_timestamps)
        self.verbose = QCheckBox("Verbose")
        self.verbose.setObjectName("verbose_checkbox")
        scroll_layout.addRow(self.verbose)
        self.print_progress = QCheckBox("Print Progress")
        self.print_progress.setObjectName("print_progress_checkbox")
        scroll_layout.addRow(self.print_progress)
        self.highlight_words = QCheckBox("Highlight Words")
        self.highlight_words.setObjectName("highlight_words_checkbox")
        scroll_layout.addRow(self.highlight_words)
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        layout = QVBoxLayout(tab)
        layout.addWidget(scroll)

    def setup_vad_tab(self, tab):
        layout = QFormLayout(tab)
        self.vad_filter = QCheckBox("Enable VAD Filter")
        self.vad_filter.setObjectName("vad_filter_checkbox")
        layout.addRow(self.vad_filter)
        self.vad_method = QComboBox()
        self.vad_method.addItems(['silero_v4_fw', 'silero_v5_fw', 'silero_v3', 'silero_v4', 'silero_v5', 'pyannote_v3', 'pyannote_onnx_v3', 'auditok', 'webrtc'])
        layout.addRow("VAD Method:", self.vad_method)
        self.vad_threshold = QDoubleSpinBox()
        self.vad_threshold.setRange(0.0, 1.0)
        self.vad_threshold.setSingleStep(0.01)
        self.vad_threshold.setDecimals(2)
        layout.addRow("VAD Threshold:", self.vad_threshold)
        self.vad_min_speech = QSpinBox()
        self.vad_min_speech.setRange(0, 10000)
        self.vad_min_speech.setSuffix(" ms")
        layout.addRow("Min Speech Duration:", self.vad_min_speech)

    def setup_audio_tab(self, tab):
        layout = QFormLayout(tab)
        self.ff_mp3 = QCheckBox("Convert to MP3")
        self.ff_mp3.setObjectName("ff_mp3_checkbox")
        layout.addRow(self.ff_mp3)
        self.ff_loudnorm = QCheckBox("Loudness Normalization")
        self.ff_loudnorm.setObjectName("ff_loudnorm_checkbox")
        layout.addRow(self.ff_loudnorm)
        self.ff_speechnorm = QCheckBox("Speech Normalization")
        self.ff_speechnorm.setObjectName("ff_speechnorm_checkbox")
        layout.addRow(self.ff_speechnorm)
        self.ff_tempo = QDoubleSpinBox()
        self.ff_tempo.setRange(0.5, 2.0)
        self.ff_tempo.setSingleStep(0.1)
        self.ff_tempo.setDecimals(1)
        self.ff_tempo.setEnabled(False)
        self.tempo_checkbox = QCheckBox("Adjust Tempo")
        self.tempo_checkbox.setObjectName("tempo_checkbox")
        self.tempo_checkbox.toggled.connect(self.ff_tempo.setEnabled)
        tempo_layout = QHBoxLayout()
        tempo_layout.addWidget(self.tempo_checkbox)
        tempo_layout.addWidget(self.ff_tempo)
        layout.addRow("Tempo:", tempo_layout)

    def apply_theme(self, theme_name):
        self.settings["theme"] = theme_name.lower()
        qss_path = ""
        if theme_name.lower() == "light":
            qss_path = resource_path("light_theme.qss")
        elif theme_name.lower() == "dark":
            qss_path = resource_path("dark_theme.qss")
        elif theme_name.lower() == "amoled":
            qss_path = resource_path("amoled_theme.qss")
        
        if qss_path and os.path.exists(qss_path):
            with open(qss_path, "r") as f:
                self.setStyleSheet(f.read())
        else:
            self.setStyleSheet("") 
            if qss_path: 
                logging.warning(f"Theme file not found: {qss_path}")
        
        # Save theme change immediately
        self.save_settings_to_file()

    def check_for_transcription_success(self, text):
        """Check if the output indicates successful transcription completion"""
        success_indicators = [
            "Operation finished in:",
            "Subtitles are written to",
            "Transcription speed:",
            "audio seconds/s"
        ]
        
        for indicator in success_indicators:
            if indicator in text:
                self.transcription_completed_successfully = True
                break

    def get_system_theme(self):
        """Detect system theme preference"""
        try:
            # Try to detect system theme using Qt's palette
            palette = QApplication.palette()
            bg_color = palette.color(QPalette.ColorRole.Window)
            # If background is dark (low lightness), system is in dark mode
            if bg_color.lightness() < 128:
                return "dark"
            else:
                return "light"
        except Exception as e:
            logging.warning(f"Could not detect system theme: {e}")
            return "dark"  # Default fallback

    def check_yt_dlp_version(self):
        """Check if yt-dlp needs updating and prompt user (with persistent tracking)"""
        # Skip if update is already in progress
        if self.yt_dlp_update_in_progress:
            logging.info("yt-dlp update in progress, skipping version check")
            return
        
        # Use persistent tracking to determine if we should check
        if not should_check_ytdlp_update():
            return
        
        # Mark as checked for this session to prevent multiple checks
        self.yt_dlp_update_checked = True
        
        try:
            # Get current installation info
            install_info = get_ytdlp_installation_info()
            current_version = install_info["version"]
            installation_type = install_info["installation_type"]
            env = install_info["environment"]
            
            if not current_version:
                logging.error("yt-dlp not found")
                return
            
            logging.info(f"Current yt-dlp version: {current_version}, type: {installation_type}, env: {env}")
            
            # Check environment capabilities
            can_update = can_update_yt_dlp()
            
            # Handle bundled installations specially
            if installation_type == "bundled":
                info_msg = f"Your yt-dlp version ({current_version}) is bundled with this application.\n\n"
                info_msg += "System updates won't affect the bundled version.\n\n"
                info_msg += "To get the latest yt-dlp features:\n"
                info_msg += "• Download a newer version of this application\n"
                info_msg += "• Or install Python and yt-dlp separately for auto-updates"
                
                show_setup_information(self, "yt-dlp Update Info", info_msg)
                record_ytdlp_bundled_check()  # Record that we informed the user
                self.yt_dlp_update_session_complete = True
                return
            
            # Check for latest version (with timeout to avoid blocking)
            try:
                response = requests.get("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest", timeout=5)
                if response.status_code == 200:
                    latest_data = response.json()
                    latest_version = latest_data["tag_name"]
                    
                    if current_version != latest_version:
                        if can_update:
                            # Standard update prompt for environments that support updates
                            update_msg = f"Your yt-dlp version ({current_version}) is outdated.\n"
                            update_msg += f"Latest version is {latest_version}.\n\n"
                            update_msg += "Would you like to update it now?\n"
                            update_msg += "(This may fix 403 unauthorized errors for video downloads)"
                            
                            if env == "exe_with_python":
                                update_msg += "\n\nNote: Update will use your system Python installation."
                            
                            reply = show_setup_question(
                                self, "yt-dlp Update Available",
                                update_msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.Yes
                            )
                            
                            if reply == QMessageBox.StandardButton.Yes:
                                self.update_yt_dlp()
                            else:
                                # User declined, record this version was offered
                                record_ytdlp_update_success(current_version)
                                self.yt_dlp_update_session_complete = True
                        else:
                            # Inform exe users without Python about the update
                            info_msg = f"A new yt-dlp version ({latest_version}) is available.\n"
                            info_msg += f"Your current version is {current_version}.\n\n"
                            info_msg += "Since you're using the standalone version without Python, "
                            info_msg += "automatic updates are not available.\n\n"
                            info_msg += "To get the latest yt-dlp version:\n"
                            info_msg += "• Install Python to enable automatic updates\n"
                            info_msg += "• Or download a newer version of this application"
                            
                            show_setup_information(self, "yt-dlp Update Available", info_msg)
                            record_ytdlp_bundled_check()  # Record that we informed the user
                            self.yt_dlp_update_session_complete = True
                    else:
                        logging.info("yt-dlp is up to date")
                        # Record successful check with current version
                        record_ytdlp_update_success(current_version)
                        self.yt_dlp_update_session_complete = True
                else:
                    logging.warning("Could not check for yt-dlp updates")
            except requests.RequestException as e:
                logging.warning(f"Failed to check yt-dlp version: {e}")
                
        except Exception as e:
            logging.error(f"Error checking yt-dlp version: {e}")

    def update_yt_dlp(self):
        """Update yt-dlp using threaded non-blocking approach"""
        # Prevent multiple simultaneous updates
        if self.yt_dlp_update_in_progress:
            logging.info("yt-dlp update already in progress, ignoring request")
            return
        
        self.yt_dlp_update_in_progress = True
        
        try:
            # Check if update is possible in current environment
            if not can_update_yt_dlp():
                # Show helpful information for exe users without Python
                show_yt_dlp_unavailable(self)
                return
            
            # Get the appropriate update command
            update_command = get_update_command()
            if not update_command:
                show_setup_critical(self, "Update Error", 
                                  "Could not determine how to update yt-dlp in your environment.")
                return
            
            # Determine environment for user feedback
            env = get_execution_environment()
            
            # Create the progress dialog
            self.update_progress_dialog = UpdateProgressDialog(self)
            
            # Create and configure the worker thread
            self.update_worker = YtDlpUpdateWorker(update_command, env)
            
            # Connect signals
            self.update_worker.progress.connect(self.update_progress_dialog.update_progress)
            self.update_worker.finished.connect(self.on_update_finished)
            
            # Connect dialog rejection (cancel) to worker stop
            self.update_progress_dialog.rejected.connect(self.cancel_update)
            
            # Show the dialog and start the worker
            self.update_progress_dialog.show()
            self.update_worker.start()
            
        except Exception as e:
            logging.error(f"Error starting yt-dlp update: {e}")
            show_setup_critical(self, "Update Error", 
                              f"An error occurred while starting the yt-dlp update:\n{str(e)}")
            self.yt_dlp_update_in_progress = False
    
    def cancel_update(self):
        """Cancel the ongoing yt-dlp update"""
        if hasattr(self, 'update_worker') and self.update_worker.isRunning():
            logging.info("User cancelled yt-dlp update")
            self.update_worker.stop()
            # Wait a short time for the worker to finish
            if not self.update_worker.wait(2000):  # 2 second timeout
                self.update_worker.terminate()
                self.update_worker.wait()
        
        self.cleanup_update_resources()
    
    def on_update_finished(self, success, message):
        """Handle completion of yt-dlp update"""
        try:
            if hasattr(self, 'update_progress_dialog'):
                self.update_progress_dialog.set_completion_state(success, message if not success else None)
                
                # Auto-close dialog after a brief delay for successful updates
                if success:
                    QTimer.singleShot(1500, self.update_progress_dialog.accept)
            
            if success:
                show_setup_information(self, "Update Successful", message)
                logging.info("yt-dlp updated successfully via threaded approach")
                
                # Record the successful update in persistent storage
                try:
                    # Get the new version after update
                    import yt_dlp
                    updated_version = yt_dlp.version.__version__
                    record_ytdlp_update_success(updated_version)
                    logging.info(f"Recorded successful yt-dlp update to version {updated_version}")
                except Exception as e:
                    logging.warning(f"Could not record update success: {e}")
                
                # Mark session as complete to prevent any future update checks
                self.yt_dlp_update_session_complete = True
                logging.info("yt-dlp update session marked as complete - no more checks this session")
            else:
                show_setup_warning(self, "Update Failed", message)
                logging.error(f"yt-dlp update failed via threaded approach: {message}")
                
        except Exception as e:
            logging.error(f"Error handling update completion: {e}")
        finally:
            self.cleanup_update_resources()
    
    def cleanup_update_resources(self):
        """Clean up update-related resources"""
        try:
            # Clean up worker thread
            if hasattr(self, 'update_worker'):
                if self.update_worker.isRunning():
                    self.update_worker.stop()
                    self.update_worker.wait(1000)
                self.update_worker.deleteLater()
                delattr(self, 'update_worker')
            
            # Clean up progress dialog
            if hasattr(self, 'update_progress_dialog'):
                self.update_progress_dialog.deleteLater()
                delattr(self, 'update_progress_dialog')
                
        except Exception as e:
            logging.warning(f"Error cleaning up update resources: {e}")
        finally:
            # Always reset the update flag
            self.yt_dlp_update_in_progress = False

    def setup_realtime_saving(self):
        """Connect UI elements to save settings in real-time"""
        # Connect combo boxes
        self.model_combo.currentTextChanged.connect(self.save_combo_setting)
        self.task_combo.currentTextChanged.connect(self.save_combo_setting)
        self.language_combo.currentTextChanged.connect(self.save_combo_setting)
        self.compute_combo.currentTextChanged.connect(self.save_combo_setting)
        self.device_combo.currentTextChanged.connect(self.save_combo_setting)
        self.vad_method.currentTextChanged.connect(self.save_combo_setting)
        
        # Connect spin boxes
        self.temperature.valueChanged.connect(self.save_spinbox_setting)
        self.beam_size.valueChanged.connect(self.save_spinbox_setting)
        self.best_of.valueChanged.connect(self.save_spinbox_setting)
        self.patience.valueChanged.connect(self.save_spinbox_setting)
        self.vad_threshold.valueChanged.connect(self.save_spinbox_setting)
        self.vad_min_speech.valueChanged.connect(self.save_spinbox_setting)
        self.ff_tempo.valueChanged.connect(self.save_spinbox_setting)
        
        # Connect text fields
        self.output_dir.textChanged.connect(self.save_text_setting)
        self.initial_prompt.textChanged.connect(self.save_text_setting)
        
        # Connect checkboxes (they'll save when toggled)
        for checkbox in self.findChildren(QCheckBox):
            if checkbox.objectName():
                checkbox.toggled.connect(self.save_checkbox_setting)
        
        # Connect output format checkboxes
        for fmt, checkbox in self.output_format_checkboxes.items():
            checkbox.toggled.connect(self.save_output_format_setting)
        
        # Connect splitter movement
        self.main_splitter.splitterMoved.connect(self.save_splitter_setting)

    def save_combo_setting(self):
        """Save combo box settings immediately"""
        self.settings["model"] = self.model_combo.currentText()
        self.settings["task"] = self.task_combo.currentText()
        self.settings["language"] = self.language_combo.currentText()
        self.settings["compute_type"] = self.compute_combo.currentText()
        self.settings["device"] = self.device_combo.currentText()
        self.settings["vad_method"] = self.vad_method.currentText()
        self.save_settings_to_file()

    def save_spinbox_setting(self):
        """Save spinbox settings immediately"""
        self.settings["temperature"] = self.temperature.value()
        self.settings["beam_size"] = self.beam_size.value()
        self.settings["best_of"] = self.best_of.value()
        self.settings["patience"] = self.patience.value()
        self.settings["vad_threshold"] = self.vad_threshold.value()
        self.settings["vad_min_speech"] = self.vad_min_speech.value()
        self.settings["ff_tempo"] = self.ff_tempo.value()
        self.save_settings_to_file()

    def save_text_setting(self):
        """Save text field settings immediately"""
        self.settings["output_dir"] = self.output_dir.text()
        self.settings["initial_prompt"] = self.initial_prompt.toPlainText()
        self.save_settings_to_file()

    def save_checkbox_setting(self):
        """Save checkbox settings immediately"""
        checkbox_settings = {cb.objectName(): cb.isChecked() for cb in self.findChildren(QCheckBox) if cb.objectName()}
        self.settings["checkboxes"] = checkbox_settings
        self.save_settings_to_file()

    def save_output_format_setting(self):
        """Save output format settings immediately"""
        output_formats = [fmt for fmt, cb in self.output_format_checkboxes.items() if cb.isChecked()]
        self.settings["output_formats"] = output_formats
        self.save_settings_to_file()

    def save_splitter_setting(self):
        """Save splitter position immediately"""
        self.settings["splitter_sizes"] = self.main_splitter.sizes()
        self.save_settings_to_file()

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Audio/Video File", "", "Audio/Video Files (*.mp3 *.wav *.m4a *.mp4 *.avi *.mov *.mkv);;All Files (*.*)")
        if file_path:
            self.file_path.setText(file_path)

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.output_dir.setText(dir_path)

    def get_output_dir(self):
        dir_path = self.output_dir.text()
        if not dir_path:
            dir_path = os.path.join(get_app_directory(), "output")
        os.makedirs(dir_path, exist_ok=True)
        return dir_path

    def build_command(self, input_file):
        if not self.executable_path or not os.path.exists(self.executable_path):
            QMessageBox.critical(self, "Error", f"Core executable not found at: {self.executable_path}. Please restart the application to run the setup.")
            return None
        
        # Check if executable has proper permissions
        if not os.access(self.executable_path, os.X_OK):
            QMessageBox.critical(self, "Error", f"Executable at {self.executable_path} does not have execute permissions.")
            return None
        if not input_file or not os.path.exists(input_file):
            QMessageBox.warning(self, "Warning", f"Input file not found: {input_file}")
            return None
        
        cmd = [self.executable_path, input_file]
        options = {
            "-m": self.model_combo.currentText(), "--task": self.task_combo.currentText(),
            "-l": self.language_combo.currentText() if self.language_combo.currentText() != 'auto' else None,
            "--compute_type": self.compute_combo.currentText(), "--device": self.device_combo.currentText(),
            "--temperature": str(self.temperature.value()) if self.temperature.value() > 0 else None,
            "--beam_size": str(self.beam_size.value()) if self.beam_size.value() != 5 else None,
            "--best_of": str(self.best_of.value()) if self.best_of.value() != 5 else None,
            "--patience": str(self.patience.value()) if self.patience.value() != 1.0 else None,
            "--initial_prompt": self.initial_prompt.toPlainText() if self.initial_prompt.toPlainText() else None,
            "--output_dir": self.get_output_dir(),
            "--vad_method": self.vad_method.currentText() if self.vad_filter.isChecked() else None,
            "--vad_threshold": str(self.vad_threshold.value()) if self.vad_filter.isChecked() else None,
            "--vad_min_speech_duration_ms": str(self.vad_min_speech.value()) if self.vad_filter.isChecked() else None,
            "--ff_tempo": str(self.ff_tempo.value()) if self.tempo_checkbox.isChecked() else None,
        }
        for option, value in options.items():
            if value is not None:
                cmd.extend([option, value])
        
        checkboxes = {
            "--word_timestamps": self.word_timestamps, "--without_timestamps": self.without_timestamps,
            "--verbose": self.verbose, "--print_progress": self.print_progress, "--highlight_words": self.highlight_words,
            "--ff_mp3": self.ff_mp3, "--ff_loudnorm": self.ff_loudnorm,
            "--ff_speechnorm": self.ff_speechnorm,
        }
        for option, checkbox in checkboxes.items():
            if checkbox.isChecked():
                cmd.append(option)
        
        selected_formats = [fmt for fmt, cb in self.output_format_checkboxes.items() if fmt != 'all' and cb.isChecked()]
        if not selected_formats and not self.output_format_checkboxes['all'].isChecked():
            selected_formats = ['srt']
        elif self.output_format_checkboxes['all'].isChecked():
            selected_formats = ['all']

        if selected_formats:
            cmd.extend(["--output_format"] + selected_formats)
        return cmd

    def start_processing(self):
        self.stop_requested = False
        self.output_buffer = ""
        self.last_line_was_overwrite = False
        self.transcription_completed_successfully = False

        active_tab = self.tabs.currentWidget()
        if active_tab == self.file_tab:
            self.run_transcription(self.file_path.text())
        elif active_tab == self.youtube_tab:
            self.download_and_transcribe()

    def run_transcription(self, input_file):
        if not input_file:
            QMessageBox.warning(self, "Warning", "Please select an input file in the 'File' tab.")
            return

        command = self.build_command(input_file)
        if not command: return
        
        self.output_text.clear()
        
        quoted_command_parts = []
        for arg in command:
            if ' ' in arg or '"' in arg or "'" in arg:
                processed_arg = arg.replace('"', '\\"')
                quoted_command_parts.append(f'"{processed_arg}"')
            else:
                quoted_command_parts.append(arg)
        display_command = ' '.join(quoted_command_parts)
        
        logging.info(f"Starting QProcess with command: {command}")
        self._append_text_to_console(f"Running command:\n{display_command}\n" + "="*50 + "\n")

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.on_finished)
        
        self.process.errorOccurred.connect(self.on_process_error)

        self.process.start(command[0], command[1:])

    def stop_processing(self):
        self.stop_requested = True
        if self.downloader and self.downloader.isRunning():
            self._append_text_to_console("\nRequesting download cancellation...\n")
            self.downloader.stop()
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self._append_text_to_console("\nTerminating process...\n")
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self._append_text_to_console("Process did not terminate gracefully, killing it.\n")
                self.process.kill()


    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
        self.check_for_transcription_success(data)
        self._append_text_to_console(data)

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode('utf-8', errors='ignore')
        self.check_for_transcription_success(data)
        self._append_text_to_console(data)

    def _append_text_to_console(self, text_chunk, is_html=False):
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if is_html:
            cursor.insertHtml(text_chunk)
            self.last_line_was_overwrite = False 
            self.output_text.ensureCursorVisible()
            return

        self.output_buffer += text_chunk.replace('\r\n', '\n')

        while '\n' in self.output_buffer or '\r' in self.output_buffer:
            r_pos = self.output_buffer.find('\r')
            n_pos = self.output_buffer.find('\n')
            
            if r_pos != -1 and (r_pos < n_pos or n_pos == -1):
                break_pos = r_pos
                line_ending = '\r'
            else:
                break_pos = n_pos
                line_ending = '\n'
            
            line = self.output_buffer[:break_pos]
            self.output_buffer = self.output_buffer[break_pos + 1:]

            if self.last_line_was_overwrite:
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)

            cursor.insertText(line)

            if line_ending == '\n':
                cursor.insertText('\n')  # Explicitly add newline to ensure proper line breaks
                self.last_line_was_overwrite = False
            else: # line_ending == '\r'
                self.last_line_was_overwrite = True
        
        self.output_text.ensureCursorVisible()

    def on_finished(self, exit_code, exit_status):
        logging.info(f"QProcess finished. Exit Code: {exit_code}, Exit Status: {exit_status}")
        
        if self.output_buffer:
            self._append_text_to_console(self.output_buffer + '\n')
            self.output_buffer = ""
        
        if self.last_line_was_overwrite:
            self.output_text.append("")
        
        self._append_text_to_console("="*50 + "\n")
        if self.stop_requested:
            self._append_text_to_console("Process stopped by user.\n")
        elif exit_code == 0 or self.transcription_completed_successfully:
            # Consider it successful if exit code is 0 OR we detected success indicators
            self._append_text_to_console("Process completed successfully.\n")
        else:
            status_str = "Crashed" if exit_status == QProcess.ExitStatus.CrashExit else "Failed"
            self._append_text_to_console(f"Process {status_str} with exit code {exit_code}.\n")
        
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.process = None
        self.downloader = None
        self.stop_requested = False
        
    def on_process_error(self, error):
        # Don't show crash errors if we already detected successful transcription
        if error == QProcess.ProcessError.Crashed and self.transcription_completed_successfully:
            logging.info("Process crashed after successful transcription completion - ignoring crash error")
            return
            
        error_map = {
            QProcess.ProcessError.FailedToStart: "Failed to start: The process failed to start. Check if the executable exists, has the correct permissions, and if all required libraries are available.",
            QProcess.ProcessError.Crashed: "Crashed: The process crashed some time after starting.",
            QProcess.ProcessError.Timedout: "Timed out: The last waitFor...() function timed out.",
            QProcess.ProcessError.ReadError: "Read Error: An error occurred when attempting to read from the process.",
            QProcess.ProcessError.WriteError: "Write Error: An error occurred when attempting to write to the process.",
            QProcess.ProcessError.UnknownError: "Unknown Error: An unknown error occurred."
        }
        error_message = error_map.get(error, "An unspecified error occurred.")
        logging.error(f"QProcess ErrorOccurred: {error_message}")
        self._append_text_to_console(f"{'='*50}\nPROCESS ERROR:\n{error_message}\n{'='*50}\n")

    def download_and_transcribe(self):
        url = self.youtube_url.text()
        if not url:
            QMessageBox.warning(self, "Warning", "Please enter a YouTube URL!")
            return
        
        output_path = self.get_output_dir()
        audio_only = self.audio_only_checkbox.isChecked()
        
        self.downloader = YouTubeDownloader(url, output_path, audio_only)
        self.downloader.finished.connect(self.on_download_finished)
        self.downloader.error.connect(self.on_download_error)
        self.downloader.progress.connect(self.handle_download_progress)
        
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.output_text.clear()
        self._append_text_to_console(f"Starting download from: {url}\n" + "="*50 + "\n")
        self.downloader.start()

    def handle_download_progress(self, text):
        """Handle yt-dlp download progress - use carriage return for same-line updates"""
        if "Downloading:" in text:
            # Use \r for progress updates to overwrite the same line
            self._append_text_to_console(text + "\r")
        else:
            # Use \n for other messages (like "Download finished")
            self._append_text_to_console(text + "\n")

    def on_download_finished(self, file_path):
        if self.stop_requested:
            self.on_finished(0, QProcess.ExitStatus.NormalExit) 
            return

        self._append_text_to_console(f"Download finished, output file:\n{file_path}\n" + "="*50 + "\n")
        self.run_transcription(input_file=file_path)

    def on_download_error(self, error_message):
        if "cancelled by user" in error_message and self.stop_requested:
            self.on_finished(0, QProcess.ExitStatus.NormalExit)
            return

        self._append_text_to_console(f"YouTube Download Error:\n{error_message}\n")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.downloader = None

    def closeEvent(self, event):
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.stop_processing()
            self.process.waitForFinished(1000)

        if hasattr(self, 'main_splitter'):
            self.save_settings()
        
        super().closeEvent(event)

    def save_settings_to_file(self):
        """Save current settings dictionary to file atomically"""
        try:
            # Write to temporary file first for atomic operation
            temp_file = self.settings_file + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(self.settings, f, indent=4)
            
            # Atomic rename operation
            if os.path.exists(temp_file):
                if os.path.exists(self.settings_file):
                    os.remove(self.settings_file)
                os.rename(temp_file, self.settings_file)
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")
            # Clean up temp file if it exists
            temp_file = self.settings_file + ".tmp"
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    def save_settings(self):
        """Collect all current settings and save to file"""
        self.settings["geometry"] = self.saveGeometry().data().hex()
        self.settings["splitter_sizes"] = self.main_splitter.sizes()
        self.settings["output_dir"] = self.output_dir.text()
        self.settings["model"] = self.model_combo.currentText()
        self.settings["task"] = self.task_combo.currentText()
        self.settings["language"] = self.language_combo.currentText()
        self.settings["compute_type"] = self.compute_combo.currentText()
        self.settings["device"] = self.device_combo.currentText()
        self.settings["temperature"] = self.temperature.value()
        self.settings["beam_size"] = self.beam_size.value()
        self.settings["best_of"] = self.best_of.value()
        self.settings["patience"] = self.patience.value()
        self.settings["initial_prompt"] = self.initial_prompt.toPlainText()
        
        # Save VAD settings
        self.settings["vad_method"] = self.vad_method.currentText()
        self.settings["vad_threshold"] = self.vad_threshold.value()
        self.settings["vad_min_speech"] = self.vad_min_speech.value()
        self.settings["ff_tempo"] = self.ff_tempo.value()
        
        checkbox_settings = {cb.objectName(): cb.isChecked() for cb in self.findChildren(QCheckBox) if cb.objectName()}
        self.settings["checkboxes"] = checkbox_settings

        output_formats = [fmt for fmt, cb in self.output_format_checkboxes.items() if cb.isChecked()]
        self.settings["output_formats"] = output_formats
        
        self.save_settings_to_file()

    def load_settings(self):
        try:
            # Check if portable settings file exists
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.settings = json.load(f)
            # Check if roaming settings file exists and migrate it back to portable
            elif os.path.exists(self.old_roaming_settings_file):
                logging.info("Migrating settings from roaming folder back to portable location")
                with open(self.old_roaming_settings_file, "r") as f:
                    self.settings = json.load(f)
                # Save to portable location
                self.save_settings_to_file()
                logging.info("Settings migrated back to portable location")
            # Check for original settings.json in current directory  
            elif os.path.exists("settings.json"):
                logging.info("Found original settings.json, keeping in portable location")
                with open("settings.json", "r") as f:
                    self.settings = json.load(f)
                # Save to portable location if not already there
                if not os.path.samefile("settings.json", self.settings_file):
                    self.save_settings_to_file()
            else:
                self.settings = {}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.warning(f"Could not load settings file: {e}. Using defaults.")
            self.settings = {}

        # Get system theme if no theme is saved
        if "theme" not in self.settings:
            system_theme = self.get_system_theme()
            theme = system_theme
        else:
            theme = self.settings.get("theme", "dark")
        
        self.theme_combo.blockSignals(True)
        # Map theme names to dropdown display names
        theme_display_map = {
            "light": "Light",
            "dark": "Dark", 
            "amoled": "AMOLED"
        }
        display_name = theme_display_map.get(theme.lower(), "Dark")
        self.theme_combo.setCurrentText(display_name)
        self.theme_combo.blockSignals(False)
        self.apply_theme(theme)

        if geometry_hex := self.settings.get("geometry"):
            try:
                # Validate hex string before using it
                if isinstance(geometry_hex, str) and all(c in '0123456789abcdefABCDEF' for c in geometry_hex):
                    self.restoreGeometry(QByteArray.fromHex(bytes(geometry_hex, 'utf-8')))
                else:
                    logging.warning("Invalid geometry data in settings")
            except Exception as e:
                logging.warning(f"Failed to restore window geometry: {e}")
        if splitter_sizes := self.settings.get("splitter_sizes"):
            self.main_splitter.setSizes(splitter_sizes)

        self.output_dir.setText(self.settings.get("output_dir", ""))
        self.model_combo.setCurrentText(self.settings.get("model", "large-v3"))
        self.task_combo.setCurrentText(self.settings.get("task", "transcribe"))
        self.language_combo.setCurrentText(self.settings.get("language", "auto"))
        self.compute_combo.setCurrentText(self.settings.get("compute_type", "float16"))
        self.device_combo.setCurrentText(self.settings.get("device", "cuda"))
        self.temperature.setValue(self.settings.get("temperature", 0.0))
        self.beam_size.setValue(self.settings.get("beam_size", 5))
        self.best_of.setValue(self.settings.get("best_of", 5))
        self.patience.setValue(self.settings.get("patience", 1.0))
        self.initial_prompt.setPlainText(self.settings.get("initial_prompt", ""))
        
        # Load VAD settings
        self.vad_method.setCurrentText(self.settings.get("vad_method", "silero_v4_fw"))
        self.vad_threshold.setValue(self.settings.get("vad_threshold", 0.5))
        self.vad_min_speech.setValue(self.settings.get("vad_min_speech", 250))
        self.ff_tempo.setValue(self.settings.get("ff_tempo", 1.0))

        all_checkboxes = {cb.objectName(): cb for cb in self.findChildren(QCheckBox) if cb.objectName()}
        checkbox_settings = self.settings.get("checkboxes", {})
        for name, checked in checkbox_settings.items():
            if name in all_checkboxes:
                all_checkboxes[name].setChecked(checked)
        
        output_formats = self.settings.get("output_formats", ["srt"])
        for fmt, cb in self.output_format_checkboxes.items():
            cb.setChecked(fmt in output_formats)

def main():
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    
    window = WhisperGUI()
    
    if window.executable_path:
        window.show()
        sys.exit(app.exec())
    else:
        logging.info("Exiting application because dependencies are not met.")
        sys.exit(0)


if __name__ == '__main__':
    main()