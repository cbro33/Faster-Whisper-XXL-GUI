import sys
import os
import json
import logging
import time
import shutil
import threading
import requests
import webbrowser
import platform
import re
import ntpath
import shlex
from collections import deque
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QHBoxLayout, QVBoxLayout, QLabel, 
    QComboBox, QTabWidget, QGroupBox, QFormLayout, QPushButton, 
    QSizePolicy, QCheckBox, QTextEdit, QDoubleSpinBox, QSpinBox, 
    QScrollArea, QFileDialog, QCompleter, QListWidget, QAbstractItemView,
    QSpacerItem, QMessageBox, QApplication, QGridLayout, QLineEdit, QDialog, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QProgressDialog,
    QMenu, QToolButton, QDialogButtonBox, QStyle, QFrame, QToolTip, QToolBar
)
from PyQt6.QtCore import Qt, QTimer, QProcess, QProcessEnvironment, QByteArray, QUrl, QThread, pyqtSignal, QModelIndex
from PyQt6.QtGui import QIcon, QPalette, QColor, QTextCursor, QFont, QDesktopServices, QFontMetrics, QAction

from config import APP_VERSION, SUPPORTED_EXTENSIONS, HTTP_HEADERS
from utils import (
    executable_word,
    get_app_directory, get_settings_directory, get_portable_settings_directory,
    resource_path, format_path_for_display, detect_faster_whisper_binary_version,
    run_hidden_subprocess, resolve_ffmpeg_location,
    redact_path_text, looks_like_path_token, sanitize_command_display,
    is_windows_path, windows_to_posix_path, sanitize_model_name,
    parse_hf_repo_id, detect_model_arch_from_dir,
    filter_verbose_output, strip_terminal_escapes, extract_links_from_text,
    find_7zip, get_7zip_install_command, download_7zr_portable,
    normalize_version, version_tuple, text_indicates_transcription_success,
)
from python_utils import (
    enumerate_python_runtimes, get_execution_environment, get_executable_fallback_path,
    get_python_probe_log
)
from ytdlp_utils import (
    get_system_yt_dlp_info, evaluate_yt_dlp_version_status, can_update_yt_dlp,
    get_python_update_plan, should_check_ytdlp_update, record_ytdlp_update_success,
    record_ytdlp_update_failure, clear_ytdlp_update_failure, is_within_update_cooldown,
    get_ytdlp_installation_info, refresh_yt_dlp_module_after_update, remove_external_yt_dlp,
    select_yt_dlp_source, set_ytdlp_debug_logging_enabled
)
from workers import (
    YouTubeDownloader, YtDlpUpdateWorker, YtDlpVersionCheckWorker,
    ConverterBundleRepairWorker, ConverterBundleVerifyWorker,
    LoudnessAnalysisWorker, AudioPreprocessWorker, VerifyModelsWorker,
)
from gui_components import (
    DownloadManager, HardwareOptimizationDialog, FileDropGroupBox, FileDropListWidget,
    FileDropWidget, LinkDropGroupBox, LinkDropListWidget, UpdateProgressDialog, show_setup_critical,
    show_setup_question, show_setup_warning, show_setup_information,
    show_yt_dlp_unavailable, ModelDownloadDialog, set_model_download_logging_enabled,
    get_model_download_log_path, get_model_download_logger,
    confirm_transformers_conversion, run_transformers_conversion_dialog, ConverterProgressDialog,
    LoudnessProgressDialog, AudioPreprocessDialog,
)
from gpu_utils import detect_hardware_capabilities
from converter_utils import (
    scan_transformers_weights,
    get_converter_bundle_dir,
    get_converter_python_path,
)
from cuda_errors import (
    detect_cuda_oom,
    detect_cuda_kernel_incompatible,
    detect_cublas_not_supported,
    get_compute_fallback,
    COMPUTE_FALLBACK_ORDER,
)
from output_formats import (
    parse_txt_timestamp,
    parse_srt_timestamp,
    format_lrc_timestamp,
    read_speaker_segments_from_srt,
    read_speaker_segments_from_txt,
    match_speaker_for_segment,
    replace_speaker_labels_in_text,
    apply_speaker_names_to_json,
    inject_speakers_into_json,
    create_sentences_only,
    build_lrc_lines,
    extra_args_has_flag,
    find_existing_outputs,
)

MODEL_DIR_PREFIX = "faster-whisper-"
BUILTIN_MODELS = [
    "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
    "large-v1", "large-v2", "large-v3", "large-v3-turbo",
    "distil-large-v2", "distil-large-v3", "distil-medium.en", "distil-small.en",
]
GITHUB_HEADERS = dict(HTTP_HEADERS)

from model_manager import ModelManagerDialog  # noqa: E402 — after MODULE_DIR_PREFIX is defined
from gui_tabs import TabSetupMixin
from gui_info_dialogs import InfoDialogsMixin  # noqa: E402


class WhisperGUI(QMainWindow, TabSetupMixin, InfoDialogsMixin):
    def __init__(self):
        super().__init__()
        self.process = None
        self.downloader = None
        self.stop_requested = False
        self.downloads_completed = True
        self.serial_download_waiting = False
        self.output_format_checkboxes = {}
        self.pending_files = []
        self.batch_total = 0
        self.batch_index = 0
        self.batch_total_known = False
        self._download_seen_paths = set()
        
        # Use portable settings location (same directory as exe/source)
        portable_dir = get_portable_settings_directory()
        self.settings_file = os.path.join(portable_dir, "settings.json")
        self.old_roaming_settings_file = os.path.join(get_settings_directory(), "settings.json")  # For migration FROM roaming
        self.settings = {}
        self.load_settings_file_only()
        self._models_registry_dirty = False
        self._ensure_models_registry()
        self._output_basename_map_limit = 200
        self._output_basename_map = {}
        self._load_output_basename_map()
        
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
        self._single_output_line_emitted = False
        self._auto_highlight_notice_shown = False
        self._current_processed_audio = None
        self._current_original_audio = None
        self._current_output_basename = None
        self._force_output_suffix = False
        self._output_basename_registry = {}
        self.last_downloaded_file = None
        self.preprocess_worker = None
        self.preprocess_dialog = None
        self._hardware_info_cache = None
        self._recent_stderr_lines = deque(maxlen=20)
        self._last_run_cuda_oom = False
        self._last_cuda_oom_snippet = None
        self._last_run_cuda_kernel_incompatible = False
        self._last_cuda_kernel_snippet = None
        self._last_run_cublas_not_supported = False
        self._last_cublas_not_supported_snippet = None
        self._cublas_compute_retry_attempted = False
        self._vad_cpu_fallback_active = False
        self._vad_oom_retry_files = set()
        self._last_command = None
        self._last_display_command = None
        self._custom_model_retry_attempted = False
        self._compute_type_override = None
        self._cpu_compute_override_applied = False
        self._review_link_ready = False
        self._last_review_output_dir = None
        self._last_review_output_base = None
        self._shown_ytdlp_403_hint = False
        self._skipped_existing_count = 0

        if not self.check_and_setup_dependencies():
            QTimer.singleShot(0, self.close)
            return

        self.init_ui()
        self.load_settings()
        set_model_download_logging_enabled(self.settings.get("debug_model_download_logging", False))
        set_ytdlp_debug_logging_enabled(self.settings.get("debug_model_download_logging", False))
        self.setup_realtime_saving()
        
        # Check for hardware optimization on first run
        QTimer.singleShot(500, self.check_hardware_optimization)
        
        # Check yt-dlp version after UI is ready (only if not already checked this session)
        if not self.yt_dlp_update_checked:
            QTimer.singleShot(1000, self.check_yt_dlp_version)
        QTimer.singleShot(1200, self.check_app_update)

    def check_and_setup_dependencies(self):
        override_exe = self.settings.get("fw_executable_override")
        if override_exe and os.path.exists(override_exe):
            self.executable_path = os.path.abspath(override_exe)
            self.executable_name = os.path.basename(self.executable_path)
            logging.info(f"Using override executable: {self.executable_path}")
            return True
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
        missing_files = [f for f in self.files_to_check
                         if not os.path.exists(os.path.join(self.bin_dir, f))]

        if not missing_files:
            self.executable_path = os.path.abspath(local_executable_path)
            logging.info(f"Found all required files in: {self.bin_dir}")
            return True

        path_in_system = shutil.which(self.executable_name)
        if path_in_system:
            self.executable_path = path_in_system
            logging.info(f"Found executable in system PATH: {path_in_system}")
            return True

        logging.info(f"Missing from {self.bin_dir}: {', '.join(missing_files)}")
        missing_list = "\n".join(f"    {name}" for name in missing_files)
        reply = show_setup_question(self, "Download Required Files?",
                                f"These files were not found in the 'bin' directory or on your PATH:\n\n"
                                f"{missing_list}\n\n"
                                f"Looked in: {os.path.abspath(self.bin_dir)}\n\n"
                                "Would you like to download and set them up automatically? (Approx. 1.4 GB)\n\n"
                                "This is a one-time setup.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.Yes)
        
        if reply == QMessageBox.StandardButton.No:
            show_setup_warning(self, "Setup Incomplete", "Application cannot run without the required files.")
            return False

        if not find_7zip():
            if not self._prompt_install_7zip():
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
            detailed_error = f"Failed to set up dependencies: {error_message}"
            if getattr(self.download_manager, "keep_archive_on_error", False):
                archive_path = getattr(self.download_manager, "archive_path", None)
                if archive_path and os.path.exists(archive_path):
                    detailed_error += f"\n\nThe downloaded archive was kept at:\n{os.path.abspath(archive_path)}"
            show_setup_critical(self, "Setup Failed", detailed_error)
            return False

    def _prompt_install_7zip(self):
        """Prompt user to install 7-Zip when it's not found. Returns True if 7z becomes available."""
        if sys.platform == "win32":
            msg = (
                "7-Zip is required to extract the downloaded archive but was not found.\n\n"
                "Would you like to download it automatically? (< 1 MB)"
            )
            reply = show_setup_question(
                self, "7-Zip Required", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                path = download_7zr_portable(self.bin_dir)
                if path and os.path.exists(path):
                    show_setup_information(self, "7-Zip Ready", "7-Zip portable has been downloaded successfully.")
                    return True
                else:
                    show_setup_critical(
                        self, "Download Failed",
                        "Could not download 7-Zip.\n\n"
                        "Please install 7-Zip manually from https://www.7-zip.org/ and restart."
                    )
                    return False
            return False

        install_cmd = get_7zip_install_command()
        msg = "7-Zip is required to extract the downloaded archive but was not found.\n\n"
        if install_cmd:
            msg += f"Please run the following command in a terminal, then click Retry:\n\n{install_cmd}"
        else:
            msg += "Please install p7zip using your system's package manager and click Retry."

        while True:
            reply = QMessageBox.question(
                self, "7-Zip Required", msg,
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Retry
            )
            if reply == QMessageBox.StandardButton.Retry:
                if find_7zip():
                    return True
                QMessageBox.warning(self, "Not Found", "7-Zip still not found. Please install it and try again.")
            else:
                return False

    def init_ui(self):
        self.setWindowTitle(f"Faster Whisper XXL GUI v{APP_VERSION}")
        QToolTip.setFont(QApplication.font())
        if getattr(sys, "frozen", False):
            self.setGeometry(100, 100, 1331, 880)
        else:
            self.setGeometry(100, 100, 1331, 880)
        self.setMinimumSize(1250, 800)

        # Create menu bar
        self.create_menu_bar()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        central_layout = QHBoxLayout(central_widget)
        central_layout.addWidget(self.main_splitter)

        left_panel = QWidget()
        self.left_panel = left_panel
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
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tabs.setMaximumHeight(280)
        left_layout.addWidget(self.tabs)

        self.file_tab = FileDropWidget(add_files_callback=self.add_input_files)
        self.setup_file_tab(self.file_tab)
        self.tabs.addTab(self.file_tab, "File")

        self.youtube_tab = QWidget()
        self.setup_youtube_tab(self.youtube_tab)
        self.tabs.addTab(self.youtube_tab, "yt-dlp")

        overrides_tab = QWidget()
        self.setup_overrides_tab(overrides_tab)
        self.tabs.addTab(overrides_tab, "Paths and Overrides")

        advanced_tab = QWidget()
        self.setup_advanced_tab(advanced_tab)
        self.tabs.addTab(advanced_tab, "Advanced")

        vad_tab = QWidget()
        self.setup_vad_tab(vad_tab)
        self.tabs.addTab(vad_tab, "VAD")

        audio_tab = QWidget()
        self.setup_audio_tab(audio_tab)
        self.tabs.addTab(audio_tab, "Audio")

        self._apply_tab_minimums()

        global_settings_group = QGroupBox("Global Settings")
        global_settings_layout = QFormLayout(global_settings_group)
        self.setup_global_settings(global_settings_layout)
        global_settings_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.global_settings_scroll = QScrollArea()
        self.global_settings_scroll.setWidgetResizable(True)
        self.global_settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.global_settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.global_settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.global_settings_scroll.setWidget(global_settings_group)
        left_layout.addWidget(self.global_settings_scroll)

        button_layout = self.create_button_layout()
        left_layout.addLayout(button_layout)

        left_layout.setStretch(1, 0)
        left_layout.setStretch(2, 1)
        left_layout.setStretch(3, 0)

        right_panel = self.create_output_console()
        self.right_panel = right_panel

        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(right_panel)
        if getattr(sys, "frozen", False):
            self.main_splitter.setSizes([545, 758])
        else:
            self.main_splitter.setSizes([560, 940])
        QTimer.singleShot(0, self._apply_tab_minimums)
        QTimer.singleShot(0, self._enforce_splitter_sizes_for_frozen)

    def _required_tab_bar_width(self):
        """Width the tab bar needs to show every tab without scroll arrows.

        QTabBar.tabSizeHint is protected, and the bar QTabWidget creates for us
        lives on the C++ side, so calling it raises RuntimeError under PyQt6.
        That made this whole function throw in frozen builds, where it is the
        only caller path, leaving the tab bar permanently clipped. The bar's
        public sizeHint already accounts for every tab.
        """
        if not getattr(self, "tabs", None):
            return None
        try:
            hint = self.tabs.tabBar().sizeHint().width()
        except Exception as exc:
            logging.debug("Could not measure tab bar width: %s", exc)
            return None
        if hint <= 0:
            return None
        return hint + 20  # room for the panel's own margins

    def _apply_tab_minimums(self):
        if not getattr(sys, "frozen", False):
            return
        min_width = self._required_tab_bar_width()
        if not min_width:
            return
        if getattr(self, "left_panel", None):
            current_min = self.left_panel.minimumWidth()
            if min_width > current_min:
                self.left_panel.setMinimumWidth(min_width)
        if getattr(self, "main_splitter", None):
            sizes = self.main_splitter.sizes()
            if len(sizes) >= 2:
                total = sum(sizes)
                desired_left = max(sizes[0], min_width)
                min_right = 520
                if total < desired_left + min_right:
                    self.setMinimumWidth(desired_left + min_right + 40)
                    self.resize(desired_left + min_right + 40, self.height())
                    total = sum(self.main_splitter.sizes())
                if total > desired_left + min_right:
                    self.main_splitter.setSizes([desired_left, total - desired_left])

    def _enforce_splitter_sizes_for_frozen(self):
        if not getattr(sys, "frozen", False):
            return
        if not getattr(self, "main_splitter", None):
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) < 2:
            return
        min_left = self._required_tab_bar_width() or 0
        min_right = 520
        target_width = max(self.width(), self.minimumWidth())
        if self.width() < target_width:
            self.resize(target_width, self.height())
        total = self.main_splitter.width() or target_width
        desired_left = max(min_left, int(total * 0.45))
        desired_right = max(min_right, total - desired_left)
        if total < desired_left + desired_right:
            total = desired_left + desired_right
            self.resize(total + 40, self.height())
            total = self.main_splitter.width() or self.width()
            desired_right = max(min_right, total - desired_left)
        self.main_splitter.setSizes([desired_left, desired_right])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(sys, "frozen", False):
            QTimer.singleShot(0, self._apply_tab_minimums)


    def browse_executable_path(self, target_line_edit):
        path, _ = QFileDialog.getOpenFileName(self, "Select Executable")
        if path:
            target_line_edit.setText(path)
            self.save_config_settings()

    def browse_directory_path(self, target_line_edit):
        path = QFileDialog.getExistingDirectory(self, "Select Directory")
        if path:
            target_line_edit.setText(path)
            self.save_config_settings()

    def clear_config_path(self, target_line_edit):
        target_line_edit.clear()
        self.save_config_settings()

    def save_config_settings(self):
        self.settings["fw_executable_override"] = self.fw_exe_path.text().strip()
        self.settings["model_dir_override"] = self.model_dir_path.text().strip()
        self.settings["yt_dlp_source"] = self.ytdlp_source_combo.currentData()
        self.settings["yt_dlp_exe_override"] = self.ytdlp_exe_path.text().strip()
        self.settings["ffmpeg_override"] = self.ffmpeg_path_input.text().strip()
        self.apply_config_settings()
        self.save_settings_to_file()

    def apply_config_settings(self):
        ffmpeg_override = self.settings.get("ffmpeg_override")
        if ffmpeg_override:
            os.environ["FWHISPER_FFMPEG_PATH"] = ffmpeg_override
        else:
            os.environ.pop("FWHISPER_FFMPEG_PATH", None)
        source = self.settings.get("yt_dlp_source", "bundled")
        if source == "bundled":
            select_yt_dlp_source(source)
        fw_override = self.settings.get("fw_executable_override")
        if fw_override and os.path.exists(fw_override):
            self.executable_path = os.path.abspath(fw_override)

    def test_faster_whisper_path(self):
        path = self.fw_exe_path.text().strip()
        if not path:
            QMessageBox.warning(self, "Whisper XXL", "Please select an executable path first.")
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Whisper XXL", "Executable not found.")
            return
        try:
            result = run_hidden_subprocess([path, "--version"], capture_output=True, text=True, timeout=5)
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0 and output:
                QMessageBox.information(self, "Whisper XXL", f"Detected: {output.splitlines()[0]}")
            else:
                QMessageBox.warning(self, "Whisper XXL", "Executable found, but version check failed.")
        except Exception as exc:
            QMessageBox.warning(self, "Whisper XXL", f"Failed to run executable: {exc}")

    def test_model_dir_path(self):
        path = self.model_dir_path.text().strip()
        if not path:
            QMessageBox.warning(self, "Model Directory", "Please select a model directory first.")
            return
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Model Directory", f"Directory not found:\n{path}")
            return
        def format_size(bytes_size):
            if bytes_size is None:
                return ""
            if bytes_size >= 1024 * 1024:
                return f"{bytes_size / (1024 * 1024):.1f} MB"
            return f"{bytes_size / 1024:.0f} KB"

        def detect_model_entry(model_path, name_override=None):
            if not os.path.isdir(model_path):
                return None
            name = name_override or os.path.basename(model_path)
            model_bin = os.path.join(model_path, "model.bin")
            if os.path.isfile(model_bin):
                size = None
                try:
                    size = os.path.getsize(model_bin)
                except Exception:
                    size = None
                return {
                    "name": name,
                    "path": model_bin,
                    "size": format_size(size),
                    "arch": "CT2",
                }
            config_path = os.path.join(model_path, "config.json")
            weight_files = []
            try:
                for filename in os.listdir(model_path):
                    lowered = filename.lower()
                    if lowered in ("model.safetensors", "pytorch_model.bin"):
                        weight_files.append(os.path.join(model_path, filename))
                    elif lowered.startswith("model-") and lowered.endswith(".safetensors"):
                        weight_files.append(os.path.join(model_path, filename))
                    elif lowered.startswith("pytorch_model-") and lowered.endswith(".bin"):
                        weight_files.append(os.path.join(model_path, filename))
            except Exception:
                weight_files = []
            if os.path.isfile(config_path) or weight_files:
                total_size = None
                sample_path = config_path if os.path.isfile(config_path) else None
                if weight_files:
                    try:
                        total_size = sum(os.path.getsize(path) for path in weight_files if os.path.isfile(path))
                        sample_path = weight_files[0]
                    except Exception:
                        total_size = None
                        sample_path = weight_files[0]
                return {
                    "name": name,
                    "path": sample_path or model_path,
                    "size": format_size(total_size),
                    "arch": "Transformers",
                }
            return None

        model_entries = []
        try:
            direct_entry = detect_model_entry(path, os.path.basename(path))
            if direct_entry:
                model_entries.append(direct_entry)
            for entry in sorted(os.listdir(path)):
                entry_path = os.path.join(path, entry)
                if not os.path.isdir(entry_path):
                    continue
                detected_entry = detect_model_entry(entry_path, entry)
                if detected_entry:
                    model_entries.append(detected_entry)
        except Exception as exc:
            QMessageBox.warning(self, "Model Directory", f"Failed to scan directory:\n{exc}")
            return

        if model_entries:
            dialog = QDialog(self)
            dialog.setWindowTitle("Model Directory")
            dialog.setModal(True)
            dialog.setMinimumSize(900, 360)

            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(12, 10, 12, 12)
            layout.setSpacing(10)

            status_label = QLabel(f"Found {len(model_entries)} model folders.")
            status_label.setWordWrap(True)
            status_label.setStyleSheet("font-size: 14px; font-weight: bold;")
            layout.addWidget(status_label)

            table = QTableWidget(dialog)
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["Model", "Type", "Path", "Size"])
            table.setRowCount(len(model_entries))
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

            for row, entry in enumerate(model_entries):
                table.setItem(row, 0, QTableWidgetItem(entry["name"]))
                table.setItem(row, 1, QTableWidgetItem(entry.get("arch", "")))
                path_item = QTableWidgetItem(entry["path"])
                path_item.setToolTip(entry["path"])
                table.setItem(row, 2, path_item)
                table.setItem(row, 3, QTableWidgetItem(entry["size"]))
            layout.addWidget(table)

            button_row = QHBoxLayout()
            close_button = QPushButton("Close")
            button_row.addStretch()
            button_row.addWidget(close_button)
            layout.addLayout(button_row)

            close_button.clicked.connect(dialog.accept)
            dialog.exec()
        else:
            QMessageBox.information(
                self,
                "Model Directory",
                "Directory exists, but no model files were found yet.\n"
                "Models will be downloaded on first run."
            )

    def test_ytdlp_path(self):
        source = self.ytdlp_source_combo.currentData()
        if source == "bundled":
            info = get_ytdlp_installation_info()
            version = info.get("version")
            location = info.get("path")
            if version:
                QMessageBox.information(self, "yt-dlp", f"Bundled module: {version}\n{location}")
            else:
                QMessageBox.warning(self, "yt-dlp", "Bundled yt-dlp not found.")
            return
        path = self.ytdlp_exe_path.text().strip()
        if not path:
            path = shutil.which("yt-dlp")
            if not path:
                QMessageBox.warning(self, "yt-dlp", "yt-dlp not found in PATH.")
                return
        if not os.path.exists(path):
            QMessageBox.warning(self, "yt-dlp", "yt-dlp executable not found.")
            return
        try:
            result = run_hidden_subprocess([path, "--version"], capture_output=True, text=True, timeout=5)
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0 and output:
                QMessageBox.information(self, "yt-dlp", f"Detected: {output.splitlines()[0]}\n{path}")
            else:
                QMessageBox.warning(self, "yt-dlp", "Executable found, but version check failed.")
        except Exception as exc:
            QMessageBox.warning(self, "yt-dlp", f"Failed to run yt-dlp: {exc}")

    def get_ytdlp_exe_version(self):
        path = self.settings.get("yt_dlp_exe_override") or shutil.which("yt-dlp")
        if not path:
            return None, None, "yt-dlp executable not found in PATH."
        try:
            result = run_hidden_subprocess([path, "--version"], capture_output=True, text=True, timeout=5)
        except Exception as exc:
            return None, path, str(exc)
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0 or not output:
            return None, path, output or f"Command failed (exit {result.returncode})."
        match = re.search(r"\d{4}\.\d{2}\.\d{2}", output)
        if match:
            return match.group(0), path, None
        return output.split()[0], path, None

    def show_ytdlp_path_picker(self):
        exe_names = ["yt-dlp.exe"] if sys.platform == "win32" else ["yt-dlp"]
        candidates = self.find_path_candidates(exe_names, ["--version"])
        self.show_executable_picker("Select yt-dlp from PATH", candidates, self.ytdlp_exe_path)

    def show_faster_whisper_path_picker(self):
        exe_names = ["faster-whisper-xxl.exe"] if sys.platform == "win32" else ["faster-whisper-xxl"]
        candidates = self.find_path_candidates(exe_names, ["--version"])
        self.show_executable_picker("Select Faster Whisper XXL from PATH", candidates, self.fw_exe_path)

    def show_ffmpeg_path_picker(self):
        exe_names = ["ffmpeg.exe"] if sys.platform == "win32" else ["ffmpeg"]
        candidates = self.find_path_candidates(exe_names, ["-version"])
        self.show_executable_picker("Select FFMPEG from PATH", candidates, self.ffmpeg_path_input)

    def show_executable_picker(self, title, candidates, target_line_edit):
        if not candidates:
            QMessageBox.information(self, "Executables", "No matching executables found in PATH.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumSize(900, 360)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        table = QTableWidget(dialog)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Path", "Version", "Size"])
        table.setRowCount(len(candidates))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for row, entry in enumerate(candidates):
            path_item = QTableWidgetItem(entry["path"])
            path_item.setToolTip(entry["path"])
            table.setItem(row, 0, path_item)
            table.setItem(row, 1, QTableWidgetItem(entry.get("version") or "Unknown"))
            table.setItem(row, 2, QTableWidgetItem(entry.get("size") or ""))
        layout.addWidget(table)

        button_row = QHBoxLayout()
        select_button = QPushButton("Use Selected")
        close_button = QPushButton("Close")
        button_row.addStretch()
        button_row.addWidget(select_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        def apply_selection():
            row = table.currentRow()
            if row < 0:
                return
            path = table.item(row, 0).text()
            target_line_edit.setText(path)
            self.save_config_settings()
            dialog.accept()

        select_button.clicked.connect(apply_selection)
        close_button.clicked.connect(dialog.reject)
        table.itemDoubleClicked.connect(lambda _item: apply_selection())

        dialog.exec()

    def find_path_candidates(self, exe_names, version_args):
        candidates = []
        seen = set()
        for folder in os.environ.get("PATH", "").split(os.pathsep):
            if not folder:
                continue
            for exe_name in exe_names:
                candidate = os.path.join(folder, exe_name)
                if not os.path.isfile(candidate):
                    continue
                normalized = os.path.abspath(candidate)
                if normalized in seen:
                    continue
                seen.add(normalized)
                size = ""
                try:
                    bytes_size = os.path.getsize(normalized)
                    if bytes_size >= 1024 * 1024:
                        size = f"{bytes_size / (1024 * 1024):.1f} MB"
                    else:
                        size = f"{bytes_size / 1024:.0f} KB"
                except Exception:
                    size = ""
                version = None
                try:
                    result = run_hidden_subprocess([normalized, *version_args], capture_output=True, text=True, timeout=5)
                    output = (result.stdout or result.stderr or "").strip()
                    if result.returncode == 0 and output:
                        version = output.splitlines()[0]
                except Exception:
                    version = None
                candidates.append({"path": normalized, "version": version, "size": size})
        return candidates

    def test_ffmpeg_path(self):
        path = self.ffmpeg_path_input.text().strip() or resolve_ffmpeg_location()
        if not path:
            QMessageBox.warning(self, "ffmpeg", "ffmpeg not found.")
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "ffmpeg", "ffmpeg executable not found.")
            return
        try:
            result = run_hidden_subprocess([path, "-version"], capture_output=True, text=True, timeout=5)
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0 and output:
                first_line = output.splitlines()[0]
                match = re.search(r"ffmpeg version ([^\\s]+)", first_line, re.IGNORECASE)
                version = match.group(1) if match else "unknown"
                build_tag_match = re.search(r"(?:-([\\w\\.]+_build[^\\s]*))", first_line)
                build_tag = build_tag_match.group(1) if build_tag_match else None
                years = None
                year_match = re.search(r"Copyright \\(c\\) (\\d{4})-(\\d{4})", output)
                if year_match:
                    years = f"{year_match.group(1)}-{year_match.group(2)}"
                parts = [f"ffmpeg {version}"]
                if build_tag:
                    parts.append(f"({build_tag})")
                if years:
                    parts.append(f"{years}")
                message = " ".join(parts) + f"\n{path}"
                self.show_info_dialog("FFMPEG", message, min_width=520, min_height=180)
            else:
                QMessageBox.warning(self, "ffmpeg", "Executable found, but version check failed.")
        except Exception as exc:
            QMessageBox.warning(self, "ffmpeg", f"Failed to run ffmpeg: {exc}")

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

        self._apply_menu_bar_font_scaling()
        
        # Save theme change immediately
        self.save_settings_to_file()

    def update_audio_preprocess_controls(self, enabled):
        if hasattr(self, "audio_norm_group"):
            self.audio_norm_group.setVisible(enabled)
        self.keep_preprocessed_audio.setVisible(enabled)
        self.audio_gain.setVisible(enabled)
        self.audio_gain_label.setVisible(enabled)
        self.audio_normalize.setVisible(enabled)
        self.update_audio_normalize_controls(self.audio_normalize.isChecked())

    def update_audio_normalize_controls(self, enabled):
        preprocess_enabled = self.audio_preprocess_enable.isChecked()
        active = enabled and preprocess_enabled
        self.audio_lufs_target.setVisible(active)
        self.audio_lufs_target_label.setVisible(active)
        self.audio_true_peak_enable.setVisible(active)
        self.audio_true_peak.setVisible(active and self.audio_true_peak_enable.isChecked())
        self.audio_true_peak_label.setVisible(active and self.audio_true_peak_enable.isChecked())
        self.audio_lra.setVisible(active)
        self.audio_lra_label.setVisible(active)

    def update_output_dir_mode(self, checked=None, save_text=True):
        if not getattr(self, "output_dir_source_checkbox", None):
            return
        if checked is None:
            checked = self.output_dir_source_checkbox.isChecked()
        if checked:
            if not hasattr(self, "_output_dir_manual_value"):
                self._output_dir_manual_value = self.output_dir.text().strip()
            if not save_text:
                self.output_dir.blockSignals(True)
            self.output_dir.setText("source")
            if not save_text:
                self.output_dir.blockSignals(False)
            self.output_dir.setEnabled(False)
            if getattr(self, "browse_out_btn", None):
                self.browse_out_btn.setEnabled(False)
        else:
            self.output_dir.setEnabled(True)
            if getattr(self, "browse_out_btn", None):
                self.browse_out_btn.setEnabled(True)
            if self.output_dir.text().strip().lower() == "source":
                restore = getattr(self, "_output_dir_manual_value", "")
                if not save_text:
                    self.output_dir.blockSignals(True)
                self.output_dir.setText(restore)
                if not save_text:
                    self.output_dir.blockSignals(False)

    def update_diarization_controls(self, enabled=None):
        if enabled is None:
            enabled = bool(getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked())
        if getattr(self, "diarize_controls_container", None):
            self.diarize_controls_container.setVisible(bool(enabled))
        self._update_diarize_review_link()
        if not enabled:
            return
        if not getattr(self, "diarize_num_speakers", None):
            return
        fixed = self.diarize_num_speakers.value()
        min_speakers = self.diarize_min_speakers.value()
        max_speakers = self.diarize_max_speakers.value()
        if min_speakers > 0 and max_speakers > 0 and max_speakers < min_speakers:
            self.diarize_max_speakers.blockSignals(True)
            self.diarize_max_speakers.setValue(min_speakers)
            self.diarize_max_speakers.blockSignals(False)
            max_speakers = min_speakers
        if fixed > 0:
            self.diarize_num_speakers.setEnabled(True)
            self.diarize_min_speakers.setEnabled(False)
            self.diarize_max_speakers.setEnabled(False)
        elif min_speakers > 0 or max_speakers > 0:
            self.diarize_num_speakers.setEnabled(False)
            self.diarize_min_speakers.setEnabled(True)
            self.diarize_max_speakers.setEnabled(True)
        else:
            self.diarize_num_speakers.setEnabled(True)
            self.diarize_min_speakers.setEnabled(True)
            self.diarize_max_speakers.setEnabled(True)

    def _update_diarize_review_link(self):
        review_widget = None
        if getattr(self, "diarize_review_button", None):
            review_widget = self.diarize_review_button
        elif getattr(self, "diarize_review_link", None):
            review_widget = self.diarize_review_link
        if review_widget is None:
            return
        diarize_active = bool(getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked())
        if not diarize_active and self._extra_args_has_flag("--diarize"):
            diarize_active = True
        review_widget.setVisible(bool(self._review_link_ready and diarize_active))
        if getattr(self, "diarize_review_prompt_checkbox", None):
            self.diarize_review_prompt_checkbox.setVisible(bool(diarize_active))

    def toggle_quiet_speech_preset(self, enabled):
        if enabled:
            self.audio_preprocess_enable.setChecked(True)
            self.audio_gain.setValue(12.0)
            self.audio_normalize.setChecked(True)
            self.audio_lufs_target.setValue(-16.0)
            self.audio_lra.setValue(11.0)
            self.audio_true_peak_enable.setChecked(True)
            self.audio_true_peak.setValue(-1.5)
        else:
            self.audio_preprocess_enable.setChecked(False)
            self.audio_gain.setValue(0.0)
            self.audio_normalize.setChecked(False)
            self.audio_lufs_target.setValue(-16.0)
            self.audio_lra.setValue(11.0)
            self.audio_true_peak_enable.setChecked(True)
            self.audio_true_peak.setValue(-1.5)
        self.update_audio_preprocess_controls(self.audio_preprocess_enable.isChecked())
        self.update_audio_normalize_controls(self.audio_normalize.isChecked())

    def analyze_loudness(self):
        items = self.file_list.selectedItems()
        if not items and self.file_list.count() == 0:
            QMessageBox.warning(
                self,
                "Analyze Loudness",
                "<span style='font-size:14px;'>"
                "Please select one or more local files in the File tab."
                "</span>",
            )
            return
        if items:
            file_paths = [item.text() for item in items]
        else:
            file_paths = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        ffmpeg_path = resolve_ffmpeg_location()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "Analyze Loudness",
                "<span style='font-size:14px;'>"
                "ffmpeg was not found. Please install or bundle it."
                "</span>",
            )
            return
        self.loudness_progress_dialog = LoudnessProgressDialog(len(file_paths), self)
        self.loudness_worker = LoudnessAnalysisWorker(ffmpeg_path, file_paths, self)
        self.loudness_worker.progress.connect(self.loudness_progress_dialog.update_progress)
        self.loudness_worker.finished.connect(self.on_loudness_analysis_finished)
        self.loudness_progress_dialog.rejected.connect(self.cancel_loudness_analysis)
        self.loudness_progress_dialog.show()
        self.loudness_worker.start()

    def cancel_loudness_analysis(self):
        if getattr(self, "loudness_worker", None) and self.loudness_worker.isRunning():
            self.loudness_worker.stop()

    def on_loudness_analysis_finished(self, results, failed, canceled):
        if getattr(self, "loudness_progress_dialog", None):
            self.loudness_progress_dialog.accept()
            self.loudness_progress_dialog = None
        self.loudness_worker = None
        if not results:
            message = "Loudness analysis failed for all selected files."
            if canceled:
                message = "Loudness analysis was canceled."
            QMessageBox.warning(
                self,
                "Analyze Loudness",
                "<span style='font-size:14px;'>"
                f"{message}"
                "</span>",
            )
            return
        if canceled:
            QMessageBox.information(
                self,
                "Analyze Loudness",
                "<span style='font-size:14px;'>"
                "Loudness analysis was canceled. Showing partial results."
                "</span>",
            )
        if failed:
            preview = "<br>".join(format_path_for_display(p) for p, _ in failed[:8])
            more = ""
            if len(failed) > 8:
                more = f"<br>…and {len(failed) - 8} more."
            QMessageBox.warning(
                self,
                "Analyze Loudness",
                "<span style='font-size:14px;'>"
                f"Some files could not be analyzed ({len(failed)}):<br>{preview}{more}"
                "</span>",
            )
        self.show_loudness_table(results)

    def show_loudness_table(self, results):
        dialog = QDialog(self)
        dialog.setWindowTitle("Loudness Analysis")
        dialog.setModal(True)
        dialog.setMinimumSize(760, 320)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        table = QTableWidget(dialog)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["File", "LUFS", "True Peak (dB)", "LRA"])
        table.setRowCount(len(results))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        for row, (path, data) in enumerate(results):
            file_item = QTableWidgetItem(os.path.basename(path))
            file_item.setToolTip(format_path_for_display(path))
            table.setItem(row, 0, file_item)
            table.setItem(row, 1, QTableWidgetItem(str(data.get("input_i", ""))))
            table.setItem(row, 2, QTableWidgetItem(str(data.get("input_tp", ""))))
            table.setItem(row, 3, QTableWidgetItem(str(data.get("input_lra", ""))))

        layout.addWidget(table)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()


    def preprocess_audio(self, input_file):
        if not self.audio_preprocess_enable.isChecked():
            return input_file
        ffmpeg_path = resolve_ffmpeg_location()
        if not ffmpeg_path:
            QMessageBox.warning(self, "Audio Pre-Processing", "ffmpeg was not found. Please install or bundle it.")
            return None
        filters = []
        gain = self.audio_gain.value()
        if abs(gain) >= 0.05:
            filters.append(f"volume={gain}dB")
        if self.audio_normalize.isChecked():
            lufs = self.audio_lufs_target.value()
            lra = self.audio_lra.value()
            if self.audio_true_peak_enable.isChecked():
                tp = self.audio_true_peak.value()
                filters.append(f"loudnorm=I={lufs}:TP={tp}:LRA={lra}")
            else:
                filters.append(f"loudnorm=I={lufs}:LRA={lra}")
        if not filters:
            return input_file
        output_dir = self.get_output_dir(input_file)
        if not output_dir:
            return None
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        stamp = int(time.time() * 1000)
        output_path = os.path.join(output_dir, f"{base_name}_preprocessed_{stamp}.wav")
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            input_file,
            "-filter_complex",
            ",".join(filters),
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
        result = run_hidden_subprocess(command, capture_output=True, text=True)
        if result.returncode != 0:
            snippet = (result.stderr or result.stdout or "").strip()
            if snippet:
                snippet = snippet.splitlines()[-1]
            QMessageBox.warning(
                self,
                "Audio Pre-Processing Failed",
                "ffmpeg could not process the audio.\n"
                + (f"\nDetails: {snippet}" if snippet else "")
            )
            return None
        self._current_processed_audio = output_path
        return output_path

    def get_preprocess_filters(self):
        if not self.audio_preprocess_enable.isChecked():
            return []
        filters = []
        gain = self.audio_gain.value()
        if abs(gain) >= 0.05:
            filters.append(f"volume={gain}dB")
        if self.audio_normalize.isChecked():
            lufs = self.audio_lufs_target.value()
            lra = self.audio_lra.value()
            if self.audio_true_peak_enable.isChecked():
                tp = self.audio_true_peak.value()
                filters.append(f"loudnorm=I={lufs}:TP={tp}:LRA={lra}")
            else:
                filters.append(f"loudnorm=I={lufs}:LRA={lra}")
        return filters

    def cleanup_processed_audio(self):
        if getattr(self, "keep_preprocessed_audio", None) and self.keep_preprocessed_audio.isChecked():
            self._current_processed_audio = None
            return
        path = getattr(self, "_current_processed_audio", None)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as exc:
                logging.debug(f"Failed to remove temp audio file {path}: {exc}")
        self._current_processed_audio = None

    def rename_outputs_for_current_run(self):
        original = (
            getattr(self, "_current_original_audio", None)
            or getattr(self, "current_input_file", None)
            or ""
        )
        if not original:
            return
        output_dir = self.get_output_dir(original)
        if not output_dir:
            return
        original_base = os.path.splitext(os.path.basename(original))[0]
        target_base = self._current_output_basename or original_base
        if self._current_processed_audio:
            source_base = os.path.splitext(os.path.basename(self._current_processed_audio))[0]
        else:
            source_base = original_base
        if not source_base or source_base == target_base:
            return
        overwrite = self._get_existing_output_mode() == "overwrite"
        extensions = ["srt", "vtt", "txt", "tsv", "json", "lrc"]
        for ext in extensions:
            src = os.path.join(output_dir, f"{source_base}.{ext}")
            if not os.path.exists(src):
                continue
            dst = os.path.join(output_dir, f"{target_base}.{ext}")
            if os.path.exists(dst) and not overwrite:
                logging.warning("Output already exists, skipping rename: %s", dst)
                continue
            try:
                # os.replace overwrites atomically; plain rename fails on Windows
                # when the destination exists.
                os.replace(src, dst)
            except Exception as exc:
                logging.warning("Failed to rename output %s -> %s: %s", src, dst, exc)

    def _get_existing_output_mode(self):
        """How to handle a file whose outputs already exist: suffix/skip/overwrite."""
        combo = getattr(self, "existing_output_combo", None)
        if combo is None:
            return "suffix"
        mode = combo.currentData()
        return mode if mode in ("suffix", "skip", "overwrite") else "suffix"

    def _get_selected_output_format_labels(self):
        """GUI format labels the user has checked, defaulting to srt when none."""
        if self.output_format_checkboxes.get('all') and self.output_format_checkboxes['all'].isChecked():
            return ['all']
        labels = [
            fmt for fmt, cb in self.output_format_checkboxes.items()
            if fmt != 'all' and cb.isChecked()
        ]
        return labels or ['srt']

    def _check_existing_outputs(self, input_file_path):
        """Decide whether *input_file_path* can be skipped because it is done.

        Returns ``(should_skip, note)``; *note* is an optional console line
        explaining the decision, and is only produced when some outputs exist.
        """
        if self._get_existing_output_mode() != "skip":
            return False, None
        output_dir = self.get_output_dir(input_file_path)
        if not output_dir:
            return False, None
        basename = self._get_existing_output_basename(input_file_path, output_dir)
        existing, missing = find_existing_outputs(
            output_dir, basename, self._get_selected_output_format_labels()
        )
        if existing and not missing:
            return True, f"Skipped: outputs already exist for {basename}"
        if existing:
            # Partial outputs mean an interrupted run, so redo it but say why.
            missing_names = ", ".join(os.path.basename(path) for path in missing)
            return False, f"Not skipped: {basename} is missing {missing_names}"
        return False, None

    def _report_skipped_summary(self):
        """Print how many files a finished batch skipped, then reset the count."""
        count = getattr(self, "_skipped_existing_count", 0)
        if count:
            plural = "file" if count == 1 else "files"
            self._append_text_to_console(
                f"\nSkipped {count} {plural} with existing outputs.\n"
            )
        self._skipped_existing_count = 0

    def _get_existing_output_basename(self, input_file_path, output_dir):
        """Basename a previous run used for this input, else the plain input stem.

        Checking against the recorded basename matters when an earlier run was
        suffixed (``video_mp4``), because its outputs live under that name, not
        ``video``.
        """
        map_key = self._make_output_map_key(input_file_path, output_dir)
        stored_base = self._output_basename_map.get(map_key)
        if stored_base:
            return stored_base
        return os.path.splitext(os.path.basename(input_file_path))[0]

    def _compute_output_basename(self, input_file_path, output_dir, force_suffix=False):
        original_base = os.path.splitext(os.path.basename(input_file_path))[0]
        original_ext = os.path.splitext(input_file_path)[1].lstrip(".").lower()
        if not original_ext:
            return original_base
        if force_suffix:
            return f"{original_base}_{original_ext}"
        if self._get_existing_output_mode() == "overwrite":
            # Reuse whatever name the previous run wrote so we replace it in
            # place instead of adding a collision suffix.
            return self._get_existing_output_basename(input_file_path, output_dir)
        map_key = self._make_output_map_key(input_file_path, output_dir)
        stored_base = self._output_basename_map.get(map_key)
        if stored_base:
            return stored_base
        registry = self._get_output_basename_registry(output_dir)
        existing_exts = registry.get(original_base, set())
        if existing_exts and original_ext not in existing_exts:
            return f"{original_base}_{original_ext}"
        extensions = ["srt", "vtt", "txt", "tsv", "json", "lrc"]
        for ext in extensions:
            if os.path.exists(os.path.join(output_dir, f"{original_base}.{ext}")):
                return f"{original_base}_{original_ext}"
        sentences_path = os.path.join(output_dir, f"{original_base}_sentences.txt")
        if os.path.exists(sentences_path):
            return f"{original_base}_{original_ext}"
        return original_base

    def _get_output_basename_registry(self, output_dir):
        key = os.path.abspath(output_dir).lower()
        registry = getattr(self, "_output_basename_registry", None)
        if registry is None:
            self._output_basename_registry = {}
            registry = self._output_basename_registry
        return registry.setdefault(key, {})

    def _register_output_basename(self, input_file_path, output_dir):
        original_base = os.path.splitext(os.path.basename(input_file_path))[0]
        original_ext = os.path.splitext(input_file_path)[1].lstrip(".").lower()
        if not original_ext:
            return
        registry = self._get_output_basename_registry(output_dir)
        registry.setdefault(original_base, set()).add(original_ext)

    def check_for_transcription_success(self, text):
        """Check if the output indicates successful transcription completion"""
        if text_indicates_transcription_success(text):
            self.transcription_completed_successfully = True

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
        if self.yt_dlp_update_in_progress:
            logging.info("yt-dlp update in progress, skipping version check")
            return

        try:
            source = self.settings.get("yt_dlp_source", "bundled")
            if source == "path":
                current_version, exe_path, error = self.get_ytdlp_exe_version()
                env = get_execution_environment()
                if not current_version:
                    logging.warning(
                        "yt-dlp EXE version check failed: %s",
                        error or "yt-dlp executable not found",
                    )
                    return
                if not should_check_ytdlp_update(current_version=current_version, env=env):
                    return
                self.yt_dlp_update_checked = True
                install_info = {
                    "version": current_version,
                    "environment": env,
                    "installation_type": "exe_path",
                    "path": exe_path,
                }
            else:
                if not should_check_ytdlp_update():
                    return
                self.yt_dlp_update_checked = True
                install_info = get_ytdlp_installation_info()
                current_version = install_info["version"]
                env = install_info["environment"]

            if not current_version:
                logging.error("yt-dlp not found")
                return

            logging.info(
                f"Current yt-dlp version: {current_version}, type: {install_info['installation_type']}, env: {env}"
            )

            plan = get_python_update_plan()
            if getattr(self, "yt_dlp_version_worker", None) and self.yt_dlp_version_worker.isRunning():
                logging.info("yt-dlp version check already running")
                return

            self._ytdlp_version_context = {
                "current_version": current_version,
                "env": env,
                "plan": plan,
                "source": source,
                "exe_path": install_info.get("path"),
            }
            self.yt_dlp_version_worker = YtDlpVersionCheckWorker(
                "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
                timeout=5,
                headers=GITHUB_HEADERS,
                parent=self,
            )
            self.yt_dlp_version_worker.finished.connect(self.on_yt_dlp_version_check_finished)
            self.yt_dlp_version_worker.start()

        except Exception as e:
            logging.error(f"Error checking yt-dlp version: {e}")

    def on_yt_dlp_version_check_finished(self, result):
        self.yt_dlp_version_worker = None
        if not result.get("ok"):
            logging.warning(f"Failed to check yt-dlp version: {result.get('error')}")
            return
        if result.get("status_code") != 200:
            logging.warning("Could not check for yt-dlp updates")
            return
        latest_version = result.get("latest_version")
        if not latest_version:
            logging.warning("Could not parse latest yt-dlp version")
            return

        context = getattr(self, "_ytdlp_version_context", {}) or {}
        current_version = context.get("current_version")
        env = context.get("env")
        plan = context.get("plan") or {}
        plan_status = plan.get("status")
        source = context.get("source")
        exe_path = context.get("exe_path")

        if not current_version:
            logging.warning("yt-dlp current version missing; skipping update prompt")
            return
        if source == "path":
            if current_version != latest_version:
                update_msg = (
                    f"Your yt-dlp {executable_word(False)} version ({current_version}) is outdated.\n"
                    f"Latest version is {latest_version}.\n\n"
                    "Would you like to open the yt-dlp downloads page?"
                )
                if exe_path:
                    update_msg += f"\n\nCurrent {executable_word()}:\n{format_path_for_display(exe_path)}"
                reply = show_setup_question(
                    self,
                    "yt-dlp Update Available",
                    update_msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    webbrowser.open("https://github.com/yt-dlp/yt-dlp/releases/latest")
            else:
                logging.info("yt-dlp EXE is up to date")
                record_ytdlp_update_success(current_version)
                clear_ytdlp_update_failure(env)
            self.yt_dlp_update_session_complete = True
            return

        if current_version != latest_version:
            if plan_status == "ready":
                update_msg = (
                    f"Your yt-dlp version ({current_version}) is outdated.\n"
                    f"Latest version is {latest_version}.\n\n"
                    "Would you like to update it now?\n"
                    "(This may fix 403 unauthorized errors for video downloads.)"
                )
                python_info = plan.get("python_info")
                target_dir = plan.get("target_directory")
                if python_info and env == "exe_with_python":
                    update_msg += (
                        f"\n\nDetected Python: {python_info.get('display_name')} "
                        f"(version {python_info.get('version')})."
                    )
                if target_dir:
                    update_msg += f"\nUpdated files will be stored in:\n{target_dir}"

                reply = show_setup_question(
                    self,
                    "yt-dlp Update Available",
                    update_msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )

                if reply == QMessageBox.StandardButton.Yes:
                    self.update_yt_dlp()
                else:
                    self.yt_dlp_update_session_complete = True
            else:
                self.handle_update_plan_blockers(plan)
                self.yt_dlp_update_session_complete = True
        else:
            logging.info("yt-dlp is up to date")
            record_ytdlp_update_success(current_version)
            clear_ytdlp_update_failure(env)
            self.yt_dlp_update_session_complete = True

    def handle_update_plan_blockers(self, plan):
        """Show contextual guidance when we cannot update yt-dlp automatically"""
        status = plan.get("status") if plan else None
        if status == "pip_missing":
            show_yt_dlp_unavailable(self, "pip_missing", plan)
        elif status == "target_unwritable":
            show_yt_dlp_unavailable(self, "target_unwritable", plan)
        elif status == "missing_python":
            show_yt_dlp_unavailable(self, "python_missing", plan)
        elif status == "error":
            detail = plan.get("status_detail", "An unknown error occurred.")
            show_setup_warning(self, "yt-dlp Update", f"Cannot prepare the update:\n{detail}")
        else:
            detail = plan.get("status_detail", "Automatic updates are currently unavailable.")
            show_setup_warning(self, "yt-dlp Update", detail)

    def update_yt_dlp(self):
        """Update yt-dlp using threaded non-blocking approach"""
        if self.yt_dlp_update_in_progress:
            logging.info("yt-dlp update already in progress, ignoring request")
            return
        
        plan = get_python_update_plan()
        if plan.get("status") != "ready":
            self.handle_update_plan_blockers(plan)
            return
        
        self.yt_dlp_update_in_progress = True
        
        try:
            self.update_progress_dialog = UpdateProgressDialog(self)
            python_info = plan.get("python_info")
            if python_info:
                self.update_progress_dialog.update_progress(
                    f"Using {python_info.get('display_name')} to update yt-dlp..."
                )

            logging.info("Starting yt-dlp update with command: %s", " ".join(plan["update_command"]))
            python_info = plan.get("python_info") or {}
            self.update_worker = YtDlpUpdateWorker(plan)
            self.update_worker.progress.connect(self.update_progress_dialog.update_progress)
            self.update_worker.finished.connect(self.on_update_finished)
            self.update_progress_dialog.rejected.connect(self.cancel_update)
            
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
            if not self.update_worker.wait(2000):
                self.update_worker.terminate()
                self.update_worker.wait()
        
        self.cleanup_update_resources()
    
    def on_update_finished(self, success, message):
        """Handle completion of yt-dlp update"""
        try:
            if hasattr(self, 'update_progress_dialog'):
                self.update_progress_dialog.set_completion_state(success, message if not success else None)
                if success:
                    QTimer.singleShot(1500, self.update_progress_dialog.accept)
            
            if success:
                try:
                    updated_module = refresh_yt_dlp_module_after_update()
                    updated_version = getattr(getattr(updated_module, "version", None), "__version__", None) if updated_module else None
                    if updated_version:
                        show_setup_information(self, "Update Successful", message)
                        logging.info("yt-dlp updated successfully via threaded approach")
                        record_ytdlp_update_success(updated_version)
                        logging.info(f"Recorded successful yt-dlp update to version {updated_version}")
                    else:
                        install_info = get_ytdlp_installation_info()
                        current_version = install_info.get("version")
                        failure_reason = "Update completed but yt-dlp could not be loaded."
                        remove_external_yt_dlp("update_reload_failed")
                        record_ytdlp_update_failure(failure_reason, current_version)
                        show_setup_warning(
                            self,
                            "Update Warning",
                            "yt-dlp updated, but the new files could not be loaded.\n"
                            "The external copy was removed and the bundled version will be used instead."
                        )
                except Exception as e:
                    logging.warning(f"Could not record update success: {e}")
                
                self.yt_dlp_update_session_complete = True
                logging.info("yt-dlp update session marked as complete - no more checks this session")
            else:
                show_setup_warning(self, "Update Failed", message)
                logging.error(f"yt-dlp update failed via threaded approach: {message}")
                try:
                    install_info = get_ytdlp_installation_info()
                    current_version = install_info.get("version")
                    record_ytdlp_update_failure(message, current_version)
                except Exception as e:
                    logging.warning(f"Could not record update failure: {e}")
                
        except Exception as e:
            logging.error(f"Error handling update completion: {e}")
        finally:
            self.cleanup_update_resources()
    
    def cleanup_update_resources(self):
        """Clean up update-related resources"""
        try:
            if hasattr(self, 'update_worker'):
                if self.update_worker.isRunning():
                    self.update_worker.stop()
                    self.update_worker.wait(1000)
                self.update_worker.deleteLater()
                delattr(self, 'update_worker')
            
            if hasattr(self, 'update_progress_dialog'):
                self.update_progress_dialog.deleteLater()
                delattr(self, 'update_progress_dialog')
                
        except Exception as e:
            logging.warning(f"Error cleaning up update resources: {e}")
        finally:
            self.yt_dlp_update_in_progress = False

    def check_hardware_optimization(self):
        """ Check if hardware optimization should be offered """
        try:
            if not self.settings.get("hardware_optimized", False):
                reply = show_setup_question(
                    self, "Hardware Optimization",
                    "Would you like to optimize settings based on your hardware?\n\n"
                    "This will detect your GPU, RAM, and CPU to recommend optimal settings for the best performance.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    self.show_hardware_optimization_dialog()
                else:
                    self.settings["hardware_optimized"] = True
                    self.save_settings_to_file()
        except Exception as e:
            logging.error(f"Error in hardware optimization check: {e}")

    def _normalize_version(self, version_str):
        return normalize_version(version_str)

    def _version_tuple(self, version_str):
        return version_tuple(version_str)

    def check_app_update(self):
        """Check GitHub releases for a newer GUI version and prompt once."""
        try:
            ignore_version = self.settings.get("ignore_update_version")
            current_version = self._normalize_version(APP_VERSION)

            if ignore_version and self._version_tuple(ignore_version) >= self._version_tuple(current_version):
                return

            response = requests.get(
                "https://api.github.com/repos/cbro33/Faster-Whisper-XXL-GUI/releases/latest",
                timeout=5,
                headers=GITHUB_HEADERS
            )
            if response.status_code != 200:
                return

            latest_tag = response.json().get("tag_name", "")
            latest_version = self._normalize_version(latest_tag)
            if not latest_version:
                return

            if self._version_tuple(latest_version) <= self._version_tuple(current_version):
                return

            message = (
                "A new version is available.\n\n"
                f"Current: {current_version}\n"
                f"Latest: {latest_version}\n\n"
                "Would you like to open the releases page?"
            )
            reply = show_setup_question(
                self,
                "Update Available",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                webbrowser.open("https://github.com/cbro33/Faster-Whisper-XXL-GUI/releases")
            else:
                self.settings["ignore_update_version"] = latest_version
                self.save_settings_to_file()
        except requests.RequestException:
            return
        except Exception as e:
            logging.warning(f"App update check failed: {e}")

    def show_hardware_optimization_dialog(self):
        """ Show hardware optimization dialog and apply recommendations """
        try:
            dialog = HardwareOptimizationDialog(self)
            result = dialog.exec()
            
            if result == QDialog.DialogCode.Accepted and dialog.user_accepted:
                self.apply_hardware_recommendations(dialog.recommendations, dialog.hardware_info)
                self.settings["hardware_optimized"] = True
                self.settings["hardware_info"] = dialog.hardware_info
                self.settings["optimization_applied"] = dialog.recommendations
                self.save_settings_to_file()
                
                show_setup_information(
                    self, "Optimization Applied", 
                    "Hardware-optimized settings have been applied!\n\n"
                    "You can always re-optimize later from the Help menu."
                )
            else:
                self.settings["hardware_optimized"] = True
                self.save_settings_to_file()
                
        except Exception as e:
            logging.error(f"Error showing hardware optimization dialog: {e}")

    def show_model_manager(self):
        dialog = ModelManagerDialog(self)
        dialog.exec()
        self._refresh_model_combo(preferred_name=self._get_selected_model_name())
        self.settings["model"] = self._get_selected_model_name()
        if self._models_registry_dirty:
            self.save_settings_to_file()
            self._models_registry_dirty = False

    def verify_model_files(self, model_names, parent=None):
        if isinstance(model_names, str):
            model_names = [model_names]
        model_names = [name for name in model_names if name]
        if not model_names:
            return
        if not self.executable_path or not os.path.exists(self.executable_path):
            QMessageBox.warning(self, "Verify Model", "Backend executable not found.")
            return
        audio_path = getattr(self, "current_input_file", None)
        if not audio_path or not os.path.isfile(audio_path):
            audio_path, _ = QFileDialog.getOpenFileName(
                parent or self,
                "Select an audio/video file for model check",
                "",
                "Audio/Video Files (*.*)"
            )
        if not audio_path:
            return
        compute_type = self._get_effective_compute_type()
        device_type = self.device_combo.currentText()
        if device_type == "cpu" and compute_type in ("float16", "int8_float16", "int8_bfloat16", "bfloat16"):
            compute_type = "float32"

        registry_entries = {entry.get("name"): entry for entry in self._get_models_registry() if entry.get("name")}
        immediate_results = []
        checks = []
        for model_name in model_names:
            _, local_model_dir, _ = self._get_model_root_dirs(model_name)
            if not local_model_dir:
                immediate_results.append((model_name, "Skipped", "Model directory is not available yet."))
                entry = registry_entries.get(model_name)
                if entry:
                    entry["verify_status"] = "skipped"
                    entry["verify_message"] = "Model directory is not available yet."
                continue
            model_dir_cli, _, _ = self._get_model_root_dirs(model_name)
            model_folder = self._get_model_folder_name(model_name)
            model_path = os.path.join(local_model_dir, model_folder)
            model_bin = os.path.join(model_path, "model.bin")
            if not os.path.isfile(model_bin):
                immediate_results.append(
                    (model_name, "Not Downloaded", "Model files not found. Download the model to verify.")
                )
                entry = registry_entries.get(model_name)
                if entry:
                    entry["verify_status"] = "missing"
                    entry["verify_message"] = "Model files not found. Download the model to verify."
                continue
            checks.append({
                "name": model_name,
                "model_dir_cli": model_dir_cli,
            })

        total_count = len(model_names)
        progress = QProgressDialog("Verifying models...", "Cancel", 0, total_count, parent or self)
        progress.setWindowTitle("Verify Models")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(len(immediate_results))

        if not checks:
            progress.setValue(total_count)
            summary_lines = [f"{model_name}: {status}" for model_name, status, _detail in immediate_results]
            summary_text = "\n".join(summary_lines).strip()
            if summary_text:
                summary_text += "\n\nDetails are shown in Manage Models > Status."
            QMessageBox.information(parent or self, "Verify Models", summary_text or "No models were verified.")
            if registry_entries:
                self._models_registry_dirty = True
                self.save_settings_to_file()
                self._models_registry_dirty = False
            if parent and hasattr(parent, "_load_table"):
                parent._load_table()
            return

        self._verify_progress_dialog = progress
        worker = VerifyModelsWorker(
            self.executable_path,
            checks,
            audio_path,
            device_type,
            compute_type,
            parent=self,
        )
        self._verify_worker = worker

        def on_progress(index, total, name):
            progress.setLabelText(f"Verifying {name} ({len(immediate_results) + index}/{total_count})")
            progress.setValue(len(immediate_results) + index)

        def on_cancel():
            if self._verify_worker:
                self._verify_worker.cancel()

        def on_finished(results):
            if self._verify_progress_dialog:
                self._verify_progress_dialog.setValue(total_count)
                self._verify_progress_dialog.close()
                self._verify_progress_dialog = None
            combined = immediate_results + results
            for model_name, status, detail in combined:
                entry = registry_entries.get(model_name)
                if not entry:
                    continue
                normalized = (status or "").strip().lower()
                if normalized == "ok":
                    entry["verify_status"] = "ok"
                elif normalized in ("not downloaded", "missing"):
                    entry["verify_status"] = "missing"
                elif normalized in ("skipped", "cancelled", "canceled"):
                    entry["verify_status"] = "skipped"
                else:
                    entry["verify_status"] = "failed"
                entry["verify_message"] = detail
            if registry_entries:
                self._models_registry_dirty = True
                self.save_settings_to_file()
                self._models_registry_dirty = False
            if parent and hasattr(parent, "_load_table"):
                parent._load_table()
            summary_lines = [f"{model_name}: {status}" for model_name, status, _detail in combined]
            summary_text = "\n".join(summary_lines).strip()
            if summary_text:
                summary_text += "\n\nDetails are shown in Manage Models > Status."
            QMessageBox.information(parent or self, "Verify Models", summary_text or "No models were verified.")

        progress.canceled.connect(on_cancel)
        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.start()

    def apply_hardware_recommendations(self, recommendations, hardware_info):
        """ Apply hardware recommendations to the UI """
        try:
            self.blockSignals(True)
            
            if "device" in recommendations:
                device_text = recommendations["device"].upper()
                index = self.device_combo.findText(device_text)
                if index >= 0:
                    self.device_combo.setCurrentIndex(index)
            
            if "model" in recommendations:
                model_text = recommendations["model"]
                if not self._set_model_selection_by_name(model_text):
                    index = self.model_combo.findText(model_text)
                    if index >= 0:
                        self.model_combo.setCurrentIndex(index)
            
            if "compute_type" in recommendations:
                compute_text = recommendations["compute_type"]
                index = self.compute_combo.findText(compute_text)
                if index >= 0:
                    self.compute_combo.setCurrentIndex(index)
            
            if "beam_size" in recommendations:
                self.beam_size.setValue(recommendations["beam_size"])
            
            if "vad_method" in recommendations:
                vad_text = recommendations["vad_method"]
                index = self.vad_method.findText(vad_text)
                if index >= 0:
                    self.vad_method.setCurrentIndex(index)
            
            self.blockSignals(False)
            self.save_settings_to_file()
            logging.info(f"Applied hardware recommendations: {recommendations}")
            
        except Exception as e:
            self.blockSignals(False)
            logging.error(f"Error applying hardware recommendations: {e}")

    def create_menu_bar(self):
        """ Create menu bar with hardware optimization option """
        try:
            menubar = self.menuBar()
            menubar.clear()
            menubar.setVisible(False)

            if getattr(self, "top_menu_toolbar", None):
                self.removeToolBar(self.top_menu_toolbar)

            toolbar = QToolBar("Top Menus", self)
            toolbar.setObjectName("top_menu_toolbar")
            toolbar.setMovable(False)
            toolbar.setFloatable(False)
            toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
            self.top_menu_toolbar = toolbar

            hardware_menu = QMenu(toolbar)
            optimize_action = hardware_menu.addAction("Optimize Hardware Settings")
            optimize_action.triggered.connect(self.force_hardware_optimization)
            view_hw_action = hardware_menu.addAction("View Hardware Info")
            view_hw_action.triggered.connect(self.show_hardware_info)
            hardware_menu.addSeparator()
            diagnose_action = hardware_menu.addAction("Diagnose GPU Detection")
            diagnose_action.triggered.connect(self.diagnose_gpu_detection)

            software_menu = QMenu(toolbar)
            view_software_action = software_menu.addAction("View Software Information")
            view_software_action.triggered.connect(self.show_software_information)

            help_menu = QMenu(toolbar)
            wiki_action = help_menu.addAction("Wiki")
            wiki_action.triggered.connect(self.open_wiki_page)
            help_menu.addSeparator()
            debug_logs_action = help_menu.addAction("Debug Settings")
            debug_logs_action.triggered.connect(self.show_debug_log_dialog)
            help_menu.addSeparator()
            check_updates_action = help_menu.addAction("Check for Updates")
            check_updates_action.triggered.connect(self.check_app_update)

            self._top_menu_buttons = []
            for title, menu in (
                ("Hardware Settings", hardware_menu),
                ("Software Information", software_menu),
                ("Help", help_menu),
            ):
                button = QToolButton(toolbar)
                button.setText(title)
                button.setMenu(menu)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                toolbar.addWidget(button)
                self._top_menu_buttons.append(button)

            self._top_menus = (hardware_menu, software_menu, help_menu)
            self._apply_menu_bar_font_scaling()

        except Exception as e:
            logging.error(f"Error creating menu bar: {e}")

    def _apply_menu_bar_font_scaling(self):
        # Match top-menu text to app font scaling across 100%/125%/150% text sizes.
        menu_font = QFont(QApplication.font())
        size_rule = "10pt"
        if menu_font.pointSizeF() > 0:
            if menu_font.pointSizeF() < 10.0:
                menu_font.setPointSizeF(menu_font.pointSizeF() + 0.5)
            size_rule = f"{menu_font.pointSizeF():.1f}pt"
        elif menu_font.pixelSize() > 0:
            if menu_font.pixelSize() < 14:
                menu_font.setPixelSize(menu_font.pixelSize() + 1)
            size_rule = f"{menu_font.pixelSize()}px"

        toolbar = getattr(self, "top_menu_toolbar", None)
        if toolbar:
            toolbar.setStyleSheet(
                f"QToolBar#top_menu_toolbar {{ border: 0px; spacing: 8px; padding: 4px 8px; }}"
                f" QToolButton {{ font-size: {size_rule}; font-weight: 400; padding: 4px 8px; border: none; }}"
                " QToolButton::menu-indicator { image: none; width: 0px; }"
            )
            for button in getattr(self, "_top_menu_buttons", []):
                button.setFont(menu_font)

        for menu in getattr(self, "_top_menus", ()):
            menu.setFont(menu_font)
            for action in menu.actions():
                action.setFont(menu_font)

    def _log_debug_parameters(self, context):
        if not self.settings.get("debug_model_download_logging", False):
            return
        logger = get_model_download_logger()
        if getattr(logger, "disabled", False):
            return
        params = {
            "context": context,
            "model": self._get_selected_model_name(),
            "task": self.task_combo.currentText(),
            "language": self.get_language_code(),
            "device": self.device_combo.currentText(),
            "compute_type": self._get_effective_compute_type(),
            "output_formats": getattr(self, "last_output_formats", []),
            "temperature": self.temperature.value(),
            "beam_size": self.beam_size.value(),
            "best_of": self.best_of.value(),
            "patience": self.patience.value(),
            "vad_enabled": self.vad_filter.isChecked(),
            "vad_method": self.vad_method.currentText() if self.vad_filter.isChecked() else None,
            "vad_device": self.vad_device.currentText() if self.vad_filter.isChecked() else None,
            "vad_threshold": self.vad_threshold.value() if self.vad_filter.isChecked() else None,
            "vad_min_speech": self.vad_min_speech.value() if self.vad_filter.isChecked() else None,
            "diarize_enabled": (
                self.diarize_enable.isChecked()
                if getattr(self, "diarize_enable", None)
                else False
            ),
            "diarize_backend": (
                self.diarize_backend.currentText()
                if getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()
                else None
            ),
            "diarize_device": (
                self.diarize_device.currentText()
                if getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()
                else None
            ),
            "diarize_num_speakers": (
                self.diarize_num_speakers.value()
                if getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()
                else None
            ),
            "diarize_min_speakers": (
                self.diarize_min_speakers.value()
                if getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()
                else None
            ),
            "diarize_max_speakers": (
                self.diarize_max_speakers.value()
                if getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()
                else None
            ),
            "diarize_only": (
                self.diarize_only_checkbox.isChecked()
                if getattr(self, "diarize_only_checkbox", None)
                else False
            ),
            "diarize_return_embeddings": (
                self.diarize_return_embeddings_checkbox.isChecked()
                if getattr(self, "diarize_return_embeddings_checkbox", None)
                else False
            ),
            "word_timestamps": (
                self.word_timestamps_checkbox.isChecked()
                if getattr(self, "word_timestamps_checkbox", None)
                else False
            ),
            "highlight_words": (
                self.highlight_words_checkbox.isChecked()
                if getattr(self, "highlight_words_checkbox", None)
                else False
            ),
            "convert_to_mp3": self.ff_mp3.isChecked(),
            "audio_preprocess_enabled": self.audio_preprocess_enable.isChecked(),
            "audio_gain_db": self.audio_gain.value(),
            "audio_normalize_enabled": self.audio_normalize.isChecked(),
            "audio_lufs_target": self.audio_lufs_target.value(),
            "audio_true_peak_enabled": self.audio_true_peak_enable.isChecked(),
            "audio_true_peak_db": self.audio_true_peak.value(),
            "audio_lra": self.audio_lra.value(),
        }
        logger.info("[run-params] %s", json.dumps(params, separators=(",", ":")))

    def _log_debug_event(self, event, **fields):
        if not self.settings.get("debug_model_download_logging", False):
            return
        logger = get_model_download_logger()
        if getattr(logger, "disabled", False):
            return
        payload = {"event": event}
        for key, value in fields.items():
            if value is not None:
                payload[key] = value
        try:
            logger.info("[event] %s", json.dumps(payload, separators=(",", ":")))
        except Exception:
            logger.info("[event] %s", payload)

    def _detect_model_arch_from_dir(self, model_path):
        return detect_model_arch_from_dir(model_path)

    def _should_auto_convert_transformers(self):
        checkbox = getattr(self, "auto_convert_transformers_checkbox", None)
        if checkbox is not None:
            return bool(checkbox.isChecked())
        checkbox_settings = self.settings.get("checkboxes", {})
        return bool(checkbox_settings.get("auto_convert_transformers_checkbox", False))

    def _maybe_convert_transformers_model(self, model_dir, parent=None):
        if not model_dir:
            return False
        model_bin = os.path.join(model_dir, "model.bin")
        if os.path.isfile(model_bin):
            return True
        weight_files = scan_transformers_weights(model_dir)
        if not weight_files:
            return False
        if not confirm_transformers_conversion(
            parent or self,
            auto_convert=self._should_auto_convert_transformers(),
            use_bundle=getattr(sys, "frozen", False),
        ):
            return False
        use_bundle = getattr(sys, "frozen", False)
        success, message = run_transformers_conversion_dialog(
            parent or self,
            model_dir,
            use_bundle=use_bundle,
        )
        if not success:
            QMessageBox.warning(
                parent or self,
                "Conversion Failed",
                message or "Model conversion failed.",
            )
            return False
        return os.path.isfile(model_bin)

    def _get_current_model_arch_info(self):
        info = {
            "model": None,
            "arch": None,
            "model_dir": None,
            "path": None,
            "note": None,
        }
        model_name = None
        try:
            model_name = self._get_selected_model_name()
        except Exception:
            model_name = None
        info["model"] = model_name
        try:
            _, local_model_dir, _ = self._get_model_root_dirs(model_name)
        except Exception:
            local_model_dir = None
        if not local_model_dir:
            info["note"] = "model_dir_missing"
            return info
        expected_dir = None
        if model_name:
            expected_dir = os.path.join(local_model_dir, self._get_model_folder_name(model_name))
        if expected_dir:
            detected = self._detect_model_arch_from_dir(expected_dir)
            if detected:
                info.update(detected)
                return info
        matches = []
        if model_name:
            try:
                for entry in sorted(os.listdir(local_model_dir)):
                    entry_path = os.path.join(local_model_dir, entry)
                    if not os.path.isdir(entry_path):
                        continue
                    if model_name.lower() not in entry.lower():
                        continue
                    detected = self._detect_model_arch_from_dir(entry_path)
                    if detected:
                        detected["name"] = entry
                        matches.append(detected)
            except Exception:
                matches = []
        if len(matches) == 1:
            info.update(matches[0])
            info["note"] = "matched_by_name"
            return info
        if matches:
            info["note"] = "ambiguous_matches:" + ",".join(entry.get("name", "") for entry in matches)
            return info
        info["model_dir"] = expected_dir
        info["note"] = "model_not_found"
        return info

    def _get_last_error_log_path(self):
        log_dir = os.path.join(get_app_directory(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, "last_error.txt")

    def _redact_path_text(self, text):
        return redact_path_text(text)

    def _looks_like_path_token(self, token):
        return looks_like_path_token(token)

    def _get_sanitized_command_display(self):
        return sanitize_command_display(self._last_command, self._last_display_command)

    def _basename_only(self, path):
        if not path:
            return ""
        try:
            return os.path.basename(path)
        except Exception:
            return path

    def _write_last_error_log(self, exit_code, exit_status, stderr_tail=None, error_message=None):
        try:
            log_path = self._get_last_error_log_path()
            status_name = getattr(exit_status, "name", str(exit_status))
            model_info = self._get_current_model_arch_info()
            input_file = getattr(self, "current_input_file", "")
            input_ext = os.path.splitext(input_file)[1].lower()
            lines = [
                f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Input File: {input_ext or 'unknown'}",
                f"Exit Code: {exit_code}",
                f"Exit Status: {status_name}",
            ]
            if model_info:
                lines.append(f"Model: {model_info.get('model') or ''}")
                lines.append(f"Model Type: {model_info.get('arch') or 'Unknown'}")
                if model_info.get("model_dir"):
                    lines.append(f"Model Dir: {self._basename_only(model_info.get('model_dir'))}")
                if model_info.get("note"):
                    lines.append(f"Model Note: {model_info.get('note')}")
            if error_message:
                lines.append(f"Process Error: {self._redact_path_text(error_message)}")
            if self._last_display_command:
                lines.append("Command:")
                lines.append(self._get_sanitized_command_display())
            if stderr_tail:
                lines.append("Stderr Tail:")
                if isinstance(stderr_tail, (list, tuple)):
                    lines.extend(self._redact_path_text(line) for line in stderr_tail)
                else:
                    lines.append(self._redact_path_text(str(stderr_tail)))
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            return log_path
        except Exception as exc:
            logging.warning("Failed to write last_error.txt: %s", exc)
            return None

    def force_hardware_optimization(self):
        """ Force hardware optimization dialog to show """
        try:
            self.show_hardware_optimization_dialog()
        except Exception as e:
            logging.error(f"Error in forced hardware optimization: {e}")

    def setup_realtime_saving(self):
        """Connect UI elements to save settings in real-time"""
        self.model_combo.currentTextChanged.connect(self.save_combo_setting)
        self.task_combo.currentTextChanged.connect(self.save_combo_setting)
        self.language_combo.currentTextChanged.connect(self.save_combo_setting)
        self.compute_combo.currentTextChanged.connect(self.save_combo_setting)
        self.device_combo.currentTextChanged.connect(self.save_combo_setting)
        self.vad_method.currentTextChanged.connect(self.save_combo_setting)
        self.vad_device.currentTextChanged.connect(self.save_combo_setting)
        self.diarize_backend.currentTextChanged.connect(self.save_combo_setting)
        self.diarize_device.currentTextChanged.connect(self.save_combo_setting)
        if getattr(self, "existing_output_combo", None):
            self.existing_output_combo.currentIndexChanged.connect(self.save_combo_setting)
        self.temperature.valueChanged.connect(self.save_spinbox_setting)
        self.beam_size.valueChanged.connect(self.save_spinbox_setting)
        self.best_of.valueChanged.connect(self.save_spinbox_setting)
        self.patience.valueChanged.connect(self.save_spinbox_setting)
        self.vad_threshold.valueChanged.connect(self.save_spinbox_setting)
        self.vad_min_speech.valueChanged.connect(self.save_spinbox_setting)
        self.diarize_num_speakers.valueChanged.connect(self.save_spinbox_setting)
        self.diarize_min_speakers.valueChanged.connect(self.save_spinbox_setting)
        self.diarize_max_speakers.valueChanged.connect(self.save_spinbox_setting)
        self.output_dir.textChanged.connect(self.save_text_setting)
        if getattr(self, "output_dir_source_checkbox", None):
            self.output_dir_source_checkbox.toggled.connect(self.update_output_dir_mode)
        self.initial_prompt.textChanged.connect(self.save_text_setting)
        # Without this, Extra CLI Args only persisted on a clean window close, and
        # any other realtime save wrote a stale value over it. See issue #22.
        self.extra_cli_args.textChanged.connect(self.save_text_setting)
        self.tooltips_checkbox.toggled.connect(self.apply_tooltip_visibility)
        
        for checkbox in self.findChildren(QCheckBox):
            if checkbox.objectName():
                checkbox.toggled.connect(self.save_checkbox_setting)
        
        for fmt, checkbox in self.output_format_checkboxes.items():
            checkbox.toggled.connect(self.save_output_format_setting)
        
        self.main_splitter.splitterMoved.connect(self.save_splitter_setting)

    def save_combo_setting(self):
        """Save combo box settings immediately"""
        self.settings["model"] = self._get_selected_model_name()
        self.settings["task"] = self.task_combo.currentText()
        self.settings["language"] = self.get_language_code()
        self.settings["compute_type"] = self.compute_combo.currentText()
        self.settings["device"] = self.device_combo.currentText()
        self.settings["vad_method"] = self.vad_method.currentText()
        self.settings["vad_device"] = self.vad_device.currentText()
        self.settings["diarize_backend"] = self.diarize_backend.currentText()
        self.settings["diarize_device"] = self.diarize_device.currentText()
        self.settings["existing_output_mode"] = self._get_existing_output_mode()
        self.save_settings_to_file()

    def save_spinbox_setting(self):
        """Save spinbox settings immediately"""
        self.settings["temperature"] = self.temperature.value()
        self.settings["beam_size"] = self.beam_size.value()
        self.settings["best_of"] = self.best_of.value()
        self.settings["patience"] = self.patience.value()
        self.settings["vad_threshold"] = self.vad_threshold.value()
        self.settings["vad_min_speech"] = self.vad_min_speech.value()
        self.settings["diarize_num_speakers"] = self.diarize_num_speakers.value()
        self.settings["diarize_min_speakers"] = self.diarize_min_speakers.value()
        self.settings["diarize_max_speakers"] = self.diarize_max_speakers.value()
        self.save_settings_to_file()

    def save_text_setting(self):
        """Save text field settings immediately"""
        self.settings["output_dir"] = self.output_dir.text()
        self.settings["initial_prompt"] = self.initial_prompt.toPlainText()
        self.settings["extra_cli_args"] = self.extra_cli_args.toPlainText()
        self.save_settings_to_file()

    def save_converter_python_path(self):
        converter_path = ""
        if getattr(self, "converter_python_path_input", None):
            converter_path = self.converter_python_path_input.text().strip()
        self.settings["converter_python_path"] = converter_path
        if converter_path:
            os.environ["FWHISPER_CONVERTER_PYTHON"] = converter_path
        else:
            os.environ.pop("FWHISPER_CONVERTER_PYTHON", None)
        self.save_settings_to_file()

    def save_checkbox_setting(self):
        """Save checkbox settings immediately"""
        checkbox_settings = {cb.objectName(): cb.isChecked() for cb in self.findChildren(QCheckBox) if cb.objectName()}
        self.settings["checkboxes"] = checkbox_settings
        self.save_settings_to_file()

    def apply_tooltip_visibility(self, checked):
        try:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableToolTips, bool(checked))
        except Exception:
            pass

    def save_output_format_setting(self):
        """Save output format settings immediately"""
        output_formats = [fmt for fmt, cb in self.output_format_checkboxes.items() if cb.isChecked()]
        self.settings["output_formats"] = output_formats
        self.save_settings_to_file()

    def save_splitter_setting(self):
        """Save splitter position immediately"""
        self.settings["splitter_sizes"] = self.main_splitter.sizes()
        self.save_settings_to_file()

    def browse_files(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Audio/Video Files",
            "",
            "Audio/Video Files (*.mp3 *.wav *.m4a *.mp4 *.avi *.mov *.mkv *.flac *.aac *.ogg *.webm);;All Files (*.*)"
        )
        if file_paths:
            self.add_input_files(file_paths)

    def browse_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.add_input_files([dir_path])

    def add_input_files(self, paths):
        if not paths:
            return
        collected = []
        for path in paths:
            if not path:
                continue
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for filename in files:
                        if filename.lower().endswith(SUPPORTED_EXTENSIONS):
                            collected.append(os.path.join(root, filename))
            else:
                collected.append(path)

        if not collected:
            return

        existing = {self.file_list.item(i).text() for i in range(self.file_list.count())}
        for path in collected:
            normalized = os.path.abspath(path)
            if not os.path.isfile(normalized):
                continue
            if not normalized.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            if normalized in existing:
                continue
            self.file_list.addItem(normalized)
            existing.add(normalized)
        self.update_file_count_label()

    def browse_converter_python(self):
        start_dir = ""
        current = self.converter_python_path_input.text().strip()
        if current and os.path.isdir(current):
            start_dir = current
        elif current and os.path.isfile(current):
            start_dir = os.path.dirname(current)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Python Executable",
            start_dir,
            "Python Executable (python.exe);;All Files (*.*)"
        )
        if not path:
            return
        self.converter_python_path_input.setText(path)
        self.save_converter_python_path()

    def repair_converter_bundle(self):
        if getattr(self, "_converter_bundle_repair_in_progress", False):
            QMessageBox.information(self, "Repair Converter Bundle", "A repair is already in progress.")
            return
        if not getattr(sys, "frozen", False):
            reply = QMessageBox.question(
                self,
                "Repair Converter Bundle",
                "This bundle is only required for the Windows EXE build.\n\n"
                "Download a fresh converter bundle anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        reply = QMessageBox.question(
            self,
            "Repair Converter Bundle",
            "This will delete the cached converter bundle and re-download it (~250 MB).\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._converter_bundle_repair_in_progress = True
        dialog = ConverterProgressDialog(self)
        dialog.setWindowTitle("Repairing Converter Bundle")
        dialog.update_progress("Preparing converter bundle...")

        worker = ConverterBundleRepairWorker(parent=dialog)
        self._converter_bundle_worker = worker

        def on_progress(message, percent):
            cleaned = re.sub(r"\s*\(\d{1,3}%\)\s*$", "", (message or "")).strip()
            lowered = cleaned.lower()
            if "extracting converter bundle" in lowered or "preparing converter bundle" in lowered:
                dialog.update_progress(cleaned, percent=-1)
                return
            if percent is not None and percent >= 0:
                dialog.update_progress(cleaned, percent=percent)
            else:
                dialog.update_progress(cleaned, percent=-1)

        def on_finished(success, message):
            self._converter_bundle_repair_in_progress = False
            dialog.set_completion_state(success, message if message else None)

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        dialog.rejected.connect(worker.stop)
        worker.start()
        dialog.exec()
        if worker.isRunning():
            worker.stop()
            worker.wait(2000)

    def verify_converter_bundle(self):
        if getattr(self, "_converter_bundle_verify_in_progress", False):
            QMessageBox.information(self, "Verify Converter Bundle", "A verification is already in progress.")
            return

        dialog = ConverterProgressDialog(self)
        dialog.setWindowTitle("Verifying Converter Bundle")
        dialog.update_progress("Preparing verification...")

        self._converter_bundle_verify_in_progress = True
        worker = ConverterBundleVerifyWorker(parent=dialog)
        self._converter_bundle_verify_worker = worker

        def on_progress(message):
            dialog.update_progress(message)

        def on_finished(success, message):
            self._converter_bundle_verify_in_progress = False
            dialog.set_completion_state(success, message if message else None)

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        dialog.rejected.connect(worker.stop)
        worker.start()
        dialog.exec()
        if worker.isRunning():
            worker.stop()
            worker.wait(2000)

    def _extract_links(self, text):
        return extract_links_from_text(text)

    def add_input_links(self, links):
        if not links:
            return
        collected = []
        for entry in links:
            if not entry:
                continue
            if isinstance(entry, str):
                collected.extend(self._extract_links(entry))
            else:
                collected.append(str(entry))

        if not collected:
            return

        existing = {self.link_list.item(i).text() for i in range(self.link_list.count())}
        for link in collected:
            normalized = link.strip()
            if not normalized or normalized in existing:
                continue
            self.link_list.addItem(normalized)
            existing.add(normalized)
        self.update_link_count_label()

    def prompt_add_links(self):
        dialog = QInputDialog(self)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setOption(QInputDialog.InputDialogOption.UsePlainTextEditForTextInput, True)
        dialog.setWindowTitle("Add Links")
        dialog.setLabelText("Enter one or more YouTube URLs (one per line):")
        dialog.resize(520, 320)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_input_links([dialog.textValue()])

    def paste_links(self):
        text = QApplication.clipboard().text()
        if not text:
            QMessageBox.information(self, "Paste Links", "Clipboard is empty.")
            return
        self.add_input_links([text])

    def remove_selected_links(self):
        for item in self.link_list.selectedItems():
            row = self.link_list.row(item)
            self.link_list.takeItem(row)
        self.update_link_count_label()

    def clear_links(self):
        self.link_list.clear()
        self.update_link_count_label()

    def get_input_links(self):
        return [self.link_list.item(i).text() for i in range(self.link_list.count())]

    def update_link_count_label(self):
        count = self.link_list.count()
        label = "link" if count == 1 else "links"
        if hasattr(self, "link_count_label"):
            self.link_count_label.setText(f"{count} {label}")

    def _normalize_download_path(self, path):
        if not path:
            return ""
        return os.path.normcase(os.path.normpath(path))

    def _load_output_basename_map(self):
        raw_map = self.settings.get("output_basename_map")
        if not isinstance(raw_map, dict):
            self._output_basename_map = {}
            return
        cleaned = {}
        for key, value in raw_map.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            normalized_key = self._normalize_download_path(key)
            if not normalized_key:
                continue
            cleaned[normalized_key] = value
        self._output_basename_map = cleaned
        self._trim_output_basename_map()

    def _make_output_map_key(self, input_file_path, output_dir):
        normalized_input = self._normalize_download_path(input_file_path)
        normalized_output = self._normalize_download_path(output_dir)
        if not normalized_input or not normalized_output:
            return ""
        return f"{normalized_output}|{normalized_input}"

    def _record_output_basename(self, input_file_path, output_dir, output_basename):
        if not input_file_path or not output_dir or not output_basename:
            return
        key = self._make_output_map_key(input_file_path, output_dir)
        if not key:
            return
        if key in self._output_basename_map:
            self._output_basename_map.pop(key, None)
        self._output_basename_map[key] = output_basename
        self._trim_output_basename_map()
        self.settings["output_basename_map"] = self._output_basename_map

    def _trim_output_basename_map(self):
        limit = getattr(self, "_output_basename_map_limit", 200)
        if limit <= 0:
            return
        while len(self._output_basename_map) > limit:
            oldest_key = next(iter(self._output_basename_map), None)
            if oldest_key is None:
                break
            self._output_basename_map.pop(oldest_key, None)

    def _get_hardware_info_cached(self):
        if self._hardware_info_cache is None:
            try:
                self._hardware_info_cache = detect_hardware_capabilities()
            except Exception as exc:
                logging.warning("Could not detect hardware capabilities: %s", exc)
                self._hardware_info_cache = {}
        return self._hardware_info_cache

    def _vad_method_is_pyannote(self):
        if not self.vad_filter.isChecked():
            return False
        return "pyannote" in (self.vad_method.currentText() or "").lower()

    def _vad_device_forced_by_user(self):
        if not self._vad_method_is_pyannote():
            return False
        extra_args = self.extra_cli_args.toPlainText().strip()
        if "--vad_device" in extra_args:
            return True
        selection = (self.vad_device.currentText() or "").strip().lower()
        return selection in {"cpu", "cuda"}

    @staticmethod
    def _detect_cuda_oom(text):
        return detect_cuda_oom(text)

    @staticmethod
    def _detect_cuda_kernel_incompatible(text):
        return detect_cuda_kernel_incompatible(text)

    @staticmethod
    def _detect_cublas_not_supported(text):
        return detect_cublas_not_supported(text)

    def _should_retry_with_vad_cpu(self):
        if not self._last_run_cuda_oom:
            return False
        if self.stop_requested:
            return False
        if not self._vad_method_is_pyannote():
            return False
        if self._vad_cpu_fallback_active:
            return False
        if self._vad_device_forced_by_user():
            return False
        input_file = getattr(self, "current_input_file", None)
        if not input_file:
            return False
        key = self._normalize_download_path(input_file)
        return key not in self._vad_oom_retry_files

    def _should_retry_with_vad_cpu_for_kernel(self):
        if not self._last_run_cuda_kernel_incompatible:
            return False
        if self.stop_requested:
            return False
        if not self._vad_method_is_pyannote():
            return False
        if self._vad_cpu_fallback_active:
            return False
        if self._vad_device_forced_by_user():
            return False
        input_file = getattr(self, "current_input_file", None)
        if not input_file:
            return False
        key = self._normalize_download_path(input_file)
        return key not in self._vad_oom_retry_files

    def _start_vad_cpu_retry(self, reason="cuda_oom"):
        input_file = getattr(self, "current_input_file", None)
        if not input_file:
            return False
        key = self._normalize_download_path(input_file)
        self._vad_oom_retry_files.add(key)
        self._vad_cpu_fallback_active = True
        self._last_run_cuda_oom = False
        self._last_run_cuda_kernel_incompatible = False
        retry_input = input_file
        if self._current_processed_audio and os.path.exists(self._current_processed_audio):
            retry_input = self._current_processed_audio
        command = self.build_command(retry_input)
        if not command:
            self._vad_cpu_fallback_active = False
            return False
        if reason == "cuda_oom":
            logging.info("CUDA OOM detected: retrying with VAD on CPU for %s", input_file)
            stderr_tail = self._last_cuda_oom_snippet
        elif reason == "cuda_kernel_incompatible":
            logging.info("CUDA kernel incompatible: retrying with VAD on CPU for %s", input_file)
            stderr_tail = self._last_cuda_kernel_snippet
        else:
            logging.info("Retrying with VAD on CPU for %s (reason=%s)", input_file, reason)
            stderr_tail = None
        self._log_debug_event(
            "vad_cpu_retry",
            reason=reason,
            input_file=input_file,
            stderr_tail=stderr_tail,
        )
        self.output_buffer = ""
        self.last_line_was_overwrite = False
        self.transcription_completed_successfully = False
        self._recent_stderr_lines.clear()
        self.process = None
        if reason == "cuda_oom":
            self._append_text_to_console("\nCUDA OOM detected. Retrying with VAD on CPU...\n")
        elif reason == "cuda_kernel_incompatible":
            self._append_text_to_console("\nCUDA kernel incompatible. Retrying with VAD on CPU...\n")
        else:
            self._append_text_to_console("\nRetrying with VAD on CPU...\n")
        return self.start_transcription_process(command, clear_output=False)

    def _should_retry_cublas_compute(self):
        if not self._last_run_cublas_not_supported:
            return False
        if self.stop_requested:
            return False
        if self._cublas_compute_retry_attempted:
            return False
        current = self._get_effective_compute_type()
        return get_compute_fallback(current) is not None

    def _start_cublas_compute_retry(self):
        current = self._get_effective_compute_type()
        fallback = get_compute_fallback(current)
        if not fallback:
            return False
        self._cublas_compute_retry_attempted = True
        self._last_run_cublas_not_supported = False
        self._compute_type_override = fallback
        logging.info(
            "cuBLAS not-supported detected: retrying with compute_type=%s (was %s)",
            fallback, current,
        )
        self._log_debug_event(
            "cublas_compute_retry",
            original_compute=current,
            fallback_compute=fallback,
            input_file=getattr(self, "current_input_file", None),
            stderr_tail=self._last_cublas_not_supported_snippet,
        )
        self.output_buffer = ""
        self.last_line_was_overwrite = False
        self.transcription_completed_successfully = False
        self._recent_stderr_lines.clear()
        self.process = None
        self._append_text_to_console(
            f"\ncuBLAS reports compute type '{current}' is not supported on this GPU. "
            f"Retrying with '{fallback}'...\n"
        )
        input_file = getattr(self, "current_input_file", None)
        if self._current_processed_audio and os.path.exists(self._current_processed_audio):
            input_file = self._current_processed_audio
        command = self.build_command(input_file)
        self._compute_type_override = None
        if not command:
            return False
        return self.start_transcription_process(command, clear_output=False)

    def _resolve_vad_device(self):
        if not self._vad_method_is_pyannote():
            return None
        extra_args = self.extra_cli_args.toPlainText().strip()
        if "--vad_device" in extra_args:
            return None
        if self._vad_cpu_fallback_active:
            return "cpu"
        selection = (self.vad_device.currentText() or "").strip().lower()
        if selection and selection != "auto":
            return selection
        hardware_info = self._get_hardware_info_cached()
        if hardware_info.get("has_cuda") and hardware_info.get("gpu_memory_gb", 0) <= 8:
            logging.info("Using CPU for pyannote VAD due to limited VRAM.")
            return "cpu"
        return None

    def _maybe_set_pyannote_env(self):
        if not self._vad_method_is_pyannote():
            return None
        env = QProcessEnvironment.systemEnvironment()
        current = env.value("PYTORCH_CUDA_ALLOC_CONF", "")
        token = "expandable_segments:True"
        if token in current:
            return env
        updated = f"{current},{token}" if current else token
        env.insert("PYTORCH_CUDA_ALLOC_CONF", updated)
        return env

    def on_download_file_ready(self, file_path):
        if self.download_all_checkbox.isChecked():
            return
        if not file_path:
            return
        normalized = self._normalize_download_path(file_path)
        if normalized in self._download_seen_paths:
            logging.info("Skipping duplicate download output: %s", file_path)
            return
        self._download_seen_paths.add(normalized)
        self.last_downloaded_file = file_path
        self.pending_files.append(file_path)
        if not self.batch_total_known:
            self.batch_total += 1
        self._append_text_to_console(f"Download finished, output file: {file_path}\n" + "="*50 + "\n")
        self.serial_download_waiting = True
        if not self.process and not self.stop_requested:
            self.start_next_file()

    def on_download_total_found(self, total):
        if total <= 0:
            return
        self.batch_total = total
        self.batch_total_known = True

    def remove_selected_files(self):
        for item in self.file_list.selectedItems():
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
        self.update_file_count_label()

    def clear_files(self):
        self.file_list.clear()
        self.update_file_count_label()

    def get_input_files(self):
        return [self.file_list.item(i).text() for i in range(self.file_list.count())]

    def update_file_count_label(self):
        count = self.file_list.count()
        label = "file" if count == 1 else "files"
        if hasattr(self, "file_count_label"):
            self.file_count_label.setText(f"{count} {label}")

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.output_dir.setText(dir_path)

    def get_output_dir(self, input_file=None):
        dir_path = self.output_dir.text().strip()
        if not dir_path:
            dir_path = os.path.join(get_app_directory(), "output")
        else:
            if dir_path.lower() == "source":
                candidate = input_file or getattr(self, "current_input_file", None)
                if candidate:
                    dir_path = os.path.dirname(candidate)
                else:
                    dir_path = os.path.join(get_app_directory(), "output")
            elif dir_path == ".":
                dir_path = os.getcwd()
        try:
            os.makedirs(dir_path, exist_ok=True)
            return dir_path
        except Exception as exc:
            logging.error(f"Failed to create output directory '{dir_path}': {exc}")
            QMessageBox.warning(
                self,
                "Output Directory Error",
                f"Could not create the output directory:\n{dir_path}\n\n"
                "Please choose a different location."
            )
            return None

    def get_language_code(self):
        data = self.language_combo.currentData()
        if data:
            return str(data)
        text = self.language_combo.currentText().strip()
        if text.endswith(")") and "(" in text:
            code = text[text.rfind("(") + 1:-1].strip()
            if code:
                return code
        return text

    def _is_windows_path(self, path):
        return is_windows_path(path)

    def _windows_to_posix_path(self, path):
        return windows_to_posix_path(path)

    def _get_models_registry(self):
        registry = self.settings.get("models_registry")
        if isinstance(registry, list):
            return registry
        return []

    def _ensure_models_registry(self):
        registry = self.settings.get("models_registry")
        changed = False
        if not isinstance(registry, list):
            registry = []
            changed = True
        existing = {}
        for entry in registry:
            name = entry.get("name")
            if not name:
                continue
            existing[name] = entry
            if not entry.get("display_name"):
                entry["display_name"] = name
                changed = True
            if "enabled" not in entry:
                entry["enabled"] = True
                changed = True
            if "root" not in entry:
                entry["root"] = "default"
                changed = True
            if "source" not in entry:
                entry["source"] = "unknown"
                changed = True
        for name in BUILTIN_MODELS:
            if name in existing:
                entry = existing[name]
                if entry.get("source") != "builtin":
                    entry["source"] = "builtin"
                    changed = True
                if entry.get("root") != "default":
                    entry["root"] = "default"
                    changed = True
                if "enabled" not in entry:
                    entry["enabled"] = True
                    changed = True
                if not entry.get("display_name"):
                    entry["display_name"] = name
                    changed = True
                continue
            registry.append({
                "name": name,
                "display_name": name,
                "source": "builtin",
                "enabled": True,
                "root": "default",
            })
            changed = True
        if changed:
            self.settings["models_registry"] = registry
            self._models_registry_dirty = True

    def _sanitize_model_name(self, name):
        return sanitize_model_name(name)

    def _get_enabled_model_entries(self):
        return [entry for entry in self._get_models_registry() if entry.get("enabled")]

    def _find_model_entry(self, model_name):
        if not model_name:
            return None
        for entry in self._get_models_registry():
            if entry.get("name") == model_name:
                return entry
        return None

    def _get_selected_model_name(self):
        data = self.model_combo.currentData()
        if data:
            return str(data)
        return self.model_combo.currentText().strip()

    def _set_model_selection_by_name(self, model_name):
        if not model_name:
            return False
        index = self.model_combo.findData(model_name)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
            return True
        return False

    def _refresh_model_combo(self, preferred_name=None):
        enabled_entries = self._get_enabled_model_entries()
        current_name = preferred_name or self.settings.get("model") or self._get_selected_model_name()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for entry in enabled_entries:
            display_name = entry.get("display_name") or entry.get("name") or ""
            self.model_combo.addItem(display_name, entry.get("name"))
        if enabled_entries:
            if not self._set_model_selection_by_name(current_name):
                self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)

    def _join_cli_path(self, base_path, child):
        if not base_path:
            return None
        if self._is_windows_path(base_path):
            return ntpath.join(base_path, child)
        return os.path.join(base_path, child)

    def _get_model_root_dirs(self, model_name=None):
        cli_model_dir, local_model_dir = self.get_model_dirs()
        entry = self._find_model_entry(model_name or self._get_selected_model_name())
        if entry and entry.get("root") == "custom":
            cli_model_dir = self._join_cli_path(cli_model_dir, "custom")
            local_model_dir = os.path.join(local_model_dir, "custom") if local_model_dir else None
        return cli_model_dir, local_model_dir, entry

    def _get_effective_compute_type(self):
        return self._compute_type_override or self.compute_combo.currentText()

    def _should_retry_custom_model_on_crash(self, exit_code, exit_status):
        if exit_status != QProcess.ExitStatus.CrashExit:
            return False
        if exit_code != -1073741819:
            return False
        if self._custom_model_retry_attempted:
            return False
        model_name = self._get_selected_model_name()
        entry = self._find_model_entry(model_name)
        if not entry or entry.get("source") not in ("hf", "local"):
            return False
        current_compute = self._get_effective_compute_type()
        if current_compute in ("auto", "float32"):
            return False
        return True

    def _start_custom_model_retry(self):
        self._custom_model_retry_attempted = True
        self._compute_type_override = "float32"
        self._append_text_to_console(
            "Custom model crashed; retrying once with compute_type=float32.\n"
        )
        command = self.build_command(self.current_input_file)
        self._compute_type_override = None
        if not command:
            return False
        return self.start_transcription_process(command, clear_output=False)

    def _get_model_folder_name(self, model_name):
        return f"{MODEL_DIR_PREFIX}{model_name}"

    def _parse_hf_repo_id(self, value):
        return parse_hf_repo_id(value)

    def _sync_models_from_disk(self):
        registry = self._get_models_registry()
        if not registry:
            return
        cli_model_dir, local_model_dir = self.get_model_dirs()
        if not local_model_dir or not os.path.isdir(local_model_dir):
            return
        roots = [("default", local_model_dir)]
        custom_root = os.path.join(local_model_dir, "custom")
        if os.path.isdir(custom_root):
            roots.append(("custom", custom_root))
        existing = {(entry.get("name"), entry.get("root")) for entry in registry if entry.get("name")}
        changed = False
        for root_type, root_path in roots:
            try:
                for entry_name in sorted(os.listdir(root_path)):
                    if not entry_name.startswith(MODEL_DIR_PREFIX):
                        continue
                    entry_path = os.path.join(root_path, entry_name)
                    if not os.path.isdir(entry_path):
                        continue
                    model_bin = os.path.join(entry_path, "model.bin")
                    if not os.path.isfile(model_bin):
                        continue
                    model_name = entry_name[len(MODEL_DIR_PREFIX):]
                    key = (model_name, root_type)
                    if key in existing:
                        continue
                    registry.append({
                        "name": model_name,
                        "display_name": model_name,
                        "source": "local",
                        "enabled": True,
                        "root": root_type,
                    })
                    existing.add(key)
                    changed = True
            except Exception:
                continue
        if changed:
            self.settings["models_registry"] = registry
            self._models_registry_dirty = True

    def get_model_dirs(self):
        model_override = self.settings.get("model_dir_override")
        if model_override:
            if os.path.isdir(model_override):
                direct_model_bin = os.path.join(model_override, "model.bin")
                if os.path.isfile(direct_model_bin):
                    parent_dir = os.path.dirname(model_override)
                    return parent_dir, parent_dir
            return model_override, model_override
        if not self.executable_path:
            return None, None
        if self._is_windows_path(self.executable_path):
            exe_dir = ntpath.dirname(self.executable_path)
            cli_model_dir = ntpath.join(exe_dir, "_models")
            if sys.platform == "win32":
                local_model_dir = cli_model_dir
            else:
                local_model_dir = self._windows_to_posix_path(cli_model_dir)
        else:
            exe_dir = os.path.dirname(self.executable_path)
            cli_model_dir = os.path.join(exe_dir, "_models")
            local_model_dir = cli_model_dir
        return cli_model_dir, local_model_dir

    def ensure_model_available(self):
        model_name = self._get_selected_model_name()
        cli_model_dir, local_model_dir, entry = self._get_model_root_dirs(model_name)
        if not local_model_dir:
            return True
        if getattr(self, "_model_download_cancelled", False):
            logging.info("ensure_model_available: skip dialog (recent cancel)")
            return False
        model_folder = self._get_model_folder_name(model_name)
        target_dir = os.path.join(local_model_dir, model_folder)
        model_bin = os.path.join(target_dir, "model.bin")
        if os.path.exists(model_bin) and os.path.getsize(model_bin) > 0:
            logging.info("ensure_model_available: model present %s", model_bin)
            return True
        if entry and entry.get("source") == "local":
            QMessageBox.warning(
                self,
                "Model Missing",
                "This model was imported from a local folder but files are missing.\n"
                "Please re-import the model from the Models manager."
            )
            return False
        if not hasattr(self, "_model_dialog_count"):
            self._model_dialog_count = 0
        self._model_dialog_count += 1
        logging.info(
            "ensure_model_available: open dialog #%s model=%s target=%s",
            self._model_dialog_count,
            model_name,
            target_dir,
        )
        repo_id = entry.get("repo_id") if entry else None
        if entry and entry.get("source") == "hf" and repo_id:
            dialog = ModelDownloadDialog(
                model_name,
                target_dir,
                parent=self,
                repo_id=repo_id,
                download_all_files=True,
                auto_convert_transformers=self._should_auto_convert_transformers(),
            )
        else:
            dialog = ModelDownloadDialog(
                model_name,
                target_dir,
                parent=self,
                auto_convert_transformers=self._should_auto_convert_transformers(),
            )
        result = dialog.exec()
        logging.info(
            "ensure_model_available: dialog #%s result=%s",
            self._model_dialog_count,
            result,
        )
        if result != QDialog.DialogCode.Accepted:
            self._model_download_cancelled = True
        return result == QDialog.DialogCode.Accepted

    def build_command(self, input_file):
        if not self.executable_path or not os.path.exists(self.executable_path):
            QMessageBox.critical(self, "Error", f"Core executable not found at: {self.executable_path}. Please restart the application to run the setup.")
            return None
        
        if not os.access(self.executable_path, os.X_OK):
            QMessageBox.critical(self, "Error", f"Executable at {self.executable_path} does not have execute permissions.")
            return None
        if not input_file or not os.path.exists(input_file):
            QMessageBox.warning(self, "Warning", f"Input file not found: {input_file}")
            return None
        output_dir = self.get_output_dir(input_file)
        if not output_dir:
            return None

        self._temp_srt_for_json = False
        self._temp_txt_for_json = False

        cmd = [self.executable_path, input_file]
        model_dir_cli, _, _ = self._get_model_root_dirs()
        vad_device = self._resolve_vad_device()
        compute_type = self._get_effective_compute_type()
        device_type = self.device_combo.currentText()
        diarize_enabled = bool(getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked())
        extra_args_text = self.extra_cli_args.toPlainText().strip()
        if not diarize_enabled and self._extra_args_has_flag("--diarize", extra_args_text):
            diarize_enabled = True
        if device_type == "cpu" and compute_type in ("float16", "int8_float16", "int8_bfloat16", "bfloat16"):
            compute_type = "float32"
            if not self._cpu_compute_override_applied:
                self._cpu_compute_override_applied = True
                self._append_text_to_console(
                    "CPU does not support float16/bfloat16 compute. Using float32 instead.\n"
                )
        options = {
            "-m": self._get_selected_model_name(), "--task": self.task_combo.currentText(),
            "-l": self.get_language_code() if self.get_language_code() != 'auto' else None,
            "--compute_type": compute_type, "--device": device_type,
            "--temperature": str(self.temperature.value()) if self.temperature.value() > 0 else None,
            "--beam_size": str(self.beam_size.value()) if self.beam_size.value() != 5 else None,
            "--best_of": str(self.best_of.value()) if self.best_of.value() != 5 else None,
            "--patience": str(self.patience.value()) if self.patience.value() != 1.0 else None,
            "--initial_prompt": self.initial_prompt.toPlainText() if self.initial_prompt.toPlainText() else None,
            "--model_dir": model_dir_cli,
            "--output_dir": output_dir,
            "--vad_method": self.vad_method.currentText() if self.vad_filter.isChecked() else None,
            "--vad_device": vad_device,
            "--vad_threshold": str(self.vad_threshold.value()) if self.vad_filter.isChecked() else None,
            "--vad_min_speech_duration_ms": str(self.vad_min_speech.value()) if self.vad_filter.isChecked() else None,
        }
        if diarize_enabled:
            diarize_backend = self.diarize_backend.currentText()
            diarize_device = (self.diarize_device.currentText() or "").strip().lower()
            if diarize_device == "auto":
                diarize_device = None
            fixed_speakers = int(self.diarize_num_speakers.value())
            min_speakers = int(self.diarize_min_speakers.value())
            max_speakers = int(self.diarize_max_speakers.value())
            options["--diarize"] = diarize_backend
            if diarize_device:
                options["--diarize_device"] = diarize_device
            if fixed_speakers > 0:
                options["--num_speakers"] = str(fixed_speakers)
            else:
                if min_speakers > 0:
                    options["--min_speakers"] = str(min_speakers)
                if max_speakers > 0:
                    options["--max_speakers"] = str(max_speakers)
        for option, value in options.items():
            if value is not None:
                cmd.extend([option, value])

        checkboxes = {
            "--ff_mp3": self.ff_mp3,
        }
        for option, checkbox in checkboxes.items():
            if checkbox.isChecked():
                cmd.append(option)
        if diarize_enabled and getattr(self, "diarize_only_checkbox", None) and self.diarize_only_checkbox.isChecked():
            cmd.append("--diarize_only")
        if diarize_enabled and getattr(self, "diarize_return_embeddings_checkbox", None) and self.diarize_return_embeddings_checkbox.isChecked():
            cmd.append("--return_embeddings")

        selected_formats = []
        display_formats = []
        self.txt_with_timestamps_requested = False
        self.sentences_only_requested = False
        self._auto_json_for_lrc = False
        
        for fmt, cb in self.output_format_checkboxes.items():
            if fmt != 'all' and cb.isChecked():
                if fmt == 'txt (with timestamps)':
                    selected_formats.append('txt')
                    self.txt_with_timestamps_requested = True
                    display_formats.append(fmt)
                elif fmt == 'txt (sentences only)':
                    selected_formats.append('txt')
                    self.sentences_only_requested = True
                    display_formats.append(fmt)
                else:
                    selected_formats.append(fmt)
                    display_formats.append(fmt)
        
        if not selected_formats and not self.output_format_checkboxes['all'].isChecked():
            selected_formats = ['srt']
            display_formats = ['srt']
        elif self.output_format_checkboxes['all'].isChecked():
            selected_formats = ['all']
            display_formats = ['all']

        if diarize_enabled and "all" not in selected_formats:
            wants_json = "json" in selected_formats
            has_speaker_source = bool(set(selected_formats) & {"srt", "txt"})
            if wants_json and not has_speaker_source:
                selected_formats.append("srt")
                self._temp_srt_for_json = True

        if (
            getattr(self, "word_timestamps_checkbox", None)
            and self.word_timestamps_checkbox.isChecked()
            and ("lrc" in selected_formats or "all" in selected_formats)
            and "json" not in selected_formats
            and "all" not in selected_formats
        ):
            selected_formats.append("json")
            self._auto_json_for_lrc = True

        if selected_formats:
            cmd.extend(["--output_format"] + selected_formats)
        self.last_output_formats = list(display_formats)

        if getattr(self, "word_timestamps_checkbox", None) and self.word_timestamps_checkbox.isChecked():
            supported_formats = {"json", "vtt", "srt", "lrc", "all"}
            if not set(selected_formats or []) & supported_formats:
                QMessageBox.warning(
                    self,
                    "Word Timestamps",
                    "Word-level timestamps require a subtitle/JSON output format (e.g., srt, vtt, json).\n"
                    "Please select a supported output format."
                )
                return None
            subtitle_formats = {"vtt", "srt", "all"}
            if set(selected_formats or []) & subtitle_formats:
                if getattr(self, "highlight_words_checkbox", None) and not self.highlight_words_checkbox.isChecked():
                    self.highlight_words_checkbox.setChecked(True)
                    if not getattr(self, "_auto_highlight_notice_shown", False):
                        QMessageBox.information(
                            self,
                            "Word Timestamps",
                            "Highlight words was enabled automatically to render word-level timing in SRT/VTT."
                        )
                        self._auto_highlight_notice_shown = True

        if getattr(self, "highlight_words_checkbox", None) and self.highlight_words_checkbox.isChecked():
            if not (getattr(self, "word_timestamps_checkbox", None) and self.word_timestamps_checkbox.isChecked()):
                QMessageBox.warning(
                    self,
                    "Highlight Words",
                    "Highlight words requires Word timestamps to be enabled."
                )
                return None
            highlight_formats = {"srt", "vtt", "all"}
            if not set(selected_formats or []) & highlight_formats:
                QMessageBox.warning(
                    self,
                    "Highlight Words",
                    "Highlight words only works with SRT or VTT output.\n"
                    "Please select SRT or VTT."
                )
                return None

        if getattr(self, "word_timestamps_checkbox", None) and self.word_timestamps_checkbox.isChecked():
            cmd.extend(["--word_timestamps", "True"])
        if getattr(self, "highlight_words_checkbox", None) and self.highlight_words_checkbox.isChecked():
            cmd.extend(["--highlight_words", "True"])
        if extra_args_text:
            try:
                extra_tokens = shlex.split(extra_args_text, posix=os.name != "nt")
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    "Extra CLI Args",
                    f"Could not parse extra arguments:\n{exc}"
                )
                return None
            cmd.extend(extra_tokens)
            self._warn_about_inert_extra_args(extra_args_text)
        return cmd

    # Flags the exe only honours when its input is a wildcard or a directory.
    # This GUI runs the exe once per file with an explicit path, so they are
    # silently ignored -- which has confused several people (issues #20, #22).
    INERT_EXTRA_ARGS = {
        "--skip": "use the 'Existing Outputs' setting in Global Settings instead",
        "--batch_recursive": "add the folder with 'Add Folder' instead",
        "--check_files": None,
    }

    def _warn_about_inert_extra_args(self, extra_args_text):
        """Tell the user once per run about extra args that cannot take effect."""
        if getattr(self, "_inert_args_warned", False):
            return
        for flag, hint in self.INERT_EXTRA_ARGS.items():
            if not self._extra_args_has_flag(flag, extra_args_text):
                continue
            self._inert_args_warned = True
            message = (
                f"Note: {flag} has no effect here. It only works when "
                "Faster Whisper XXL is given a wildcard or a folder, and this app "
                "runs it once per file."
            )
            if hint:
                message += f" To do this, {hint}."
            self._append_text_to_console(message + "\n")

    def start_processing(self):
        self.stop_requested = False
        self.output_buffer = ""
        self.last_line_was_overwrite = False
        self.transcription_completed_successfully = False
        self._review_link_ready = False
        self._update_diarize_review_link()
        self._last_run_cuda_oom = False
        self._last_cuda_oom_snippet = None
        self._last_run_cuda_kernel_incompatible = False
        self._last_cuda_kernel_snippet = None
        self._last_run_cublas_not_supported = False
        self._last_cublas_not_supported_snippet = None
        self._cublas_compute_retry_attempted = False
        self._vad_cpu_fallback_active = False
        self._vad_oom_retry_files = set()
        self._custom_model_retry_attempted = False
        self._compute_type_override = None
        self._cpu_compute_override_applied = False
        self._model_download_cancelled = False
        self._skipped_existing_count = 0
        self._inert_args_warned = False
        if not self.get_output_dir():
            return

        active_tab = self.tabs.currentWidget()
        if active_tab == self.file_tab:
            input_files = self.get_input_files()
            if not input_files:
                QMessageBox.warning(self, "Warning", "Please add one or more input files in the 'File' tab.")
                return
            self.pending_files = list(input_files)
            self.batch_total = len(self.pending_files)
            self.batch_index = 0
            self.batch_total_known = True
            self.output_text.clear()
            self.run_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.start_next_file()
        elif active_tab == self.youtube_tab:
            self.download_and_transcribe()

    def start_next_file(self):
        if self.stop_requested or not self.pending_files:
            return
        started = False
        while self.pending_files and not self.stop_requested:
            self.batch_index += 1
            next_file = self.pending_files.pop(0)
            total_display = self.batch_total if self.batch_total_known else "?"
            should_skip, note = self._check_existing_outputs(next_file)
            if should_skip:
                self._skipped_existing_count += 1
                logging.info("start_next_file: skipping %s (outputs exist)", next_file)
                # Keep this to a single short line: resuming a large batch can
                # skip hundreds of files, and full paths would swamp the console.
                self._append_text_to_console(
                    f"\nSKIPPED FILE {self.batch_index}/{total_display} • "
                    f"{os.path.basename(next_file)} (outputs already exist)\n"
                )
                continue
            self._append_text_to_console(
                f"\nPROCESSING FILE {self.batch_index}/{total_display} • {next_file}\n\n"
            )
            if self.run_transcription(next_file):
                started = True
                break
        if not started and not self.stop_requested:
            # Nothing ran, so on_finished will not fire to hand control back.
            # In serial mode it is what releases the next download, so do that
            # here instead or the queue stalls. Gate on serial_download_waiting
            # rather than isRunning(): the downloader emits `finished` from
            # inside run(), so in download-all mode the thread can still report
            # itself running and we would wrongly leave Run disabled.
            if self.serial_download_waiting and self.downloader and self.downloader.isRunning():
                self.serial_download_waiting = False
                self.downloader.allow_next_download()
                self.run_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
                return
            self._report_skipped_summary()
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.stop_requested = False

    def run_transcription(self, input_file, clear_output=False):
        if not input_file:
            QMessageBox.warning(self, "Warning", "Please select an input file in the 'File' tab.")
            return False

        self.current_input_file = input_file
        self._current_original_audio = input_file
        self._current_output_basename = None
        self._single_output_line_emitted = False
        logging.info("run_transcription: input=%s", input_file)

        # Check before touching the model so a fully-skipped batch never waits
        # on a model download it will not use.
        should_skip, note = self._check_existing_outputs(input_file)
        if note:
            self._append_text_to_console(f"{note}\n")
        if should_skip:
            logging.info("run_transcription: skipping %s (outputs exist)", input_file)
            self._skipped_existing_count += 1
            return False

        if not self.ensure_model_available():
            logging.info("run_transcription: model download cancelled or failed")
            return False

        self.cleanup_processed_audio()
        filters = self.get_preprocess_filters()
        force_suffix = bool(filters)
        if getattr(self, "output_name_match_checkbox", None) and self.output_name_match_checkbox.isChecked():
            force_suffix = False
        self._force_output_suffix = force_suffix
        output_dir = self.get_output_dir(input_file)
        if not output_dir:
            return False
        self._current_output_basename = self._compute_output_basename(
            input_file,
            output_dir,
            force_suffix=force_suffix
        )
        self._register_output_basename(input_file, output_dir)
        self._record_output_basename(input_file, output_dir, self._current_output_basename)
        if self.audio_preprocess_enable.isChecked() and filters:
            return self.start_preprocess_and_transcribe(input_file, filters, clear_output)
        command = self.build_command(input_file)
        if not command:
            return False
        return self.start_transcription_process(command, clear_output)

    def start_preprocess_and_transcribe(self, input_file, filters, clear_output):
        ffmpeg_path = resolve_ffmpeg_location()
        if not ffmpeg_path:
            QMessageBox.warning(self, "Audio Pre-Processing", "ffmpeg was not found. Please install or bundle it.")
            return False
        output_dir = self.get_output_dir()
        if not output_dir:
            return False
        if self.preprocess_worker and self.preprocess_worker.isRunning():
            QMessageBox.warning(self, "Audio Pre-Processing", "Audio pre-processing is already running.")
            return False
        self.preprocess_dialog = AudioPreprocessDialog(self)
        self.preprocess_worker = AudioPreprocessWorker(
            ffmpeg_path,
            input_file,
            output_dir,
            filters,
            self,
        )
        self.preprocess_worker.finished.connect(
            lambda result: self.on_preprocess_finished(result, clear_output)
        )
        self.preprocess_dialog.rejected.connect(self.cancel_preprocess)
        self.preprocess_dialog.show()
        self.preprocess_worker.start()
        return True

    def cancel_preprocess(self):
        if self.preprocess_worker and self.preprocess_worker.isRunning():
            self.preprocess_worker.stop()

    def on_preprocess_finished(self, result, clear_output):
        if self.preprocess_dialog:
            self.preprocess_dialog.accept()
            self.preprocess_dialog = None
        worker = self.preprocess_worker
        self.preprocess_worker = None
        if not result or not result.get("ok"):
            error = result.get("error") if result else "Audio preprocessing failed"
            if error == "canceled":
                self._append_text_to_console("\nAudio preprocessing canceled.\n")
            else:
                self._append_text_to_console(f"\nAudio preprocessing failed: {error}\n")
            if self.pending_files and not self.stop_requested:
                self.start_next_file()
                return
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        processed_file = result.get("path")
        if not processed_file:
            self._append_text_to_console("\nAudio preprocessing failed.\n")
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        if processed_file != self._current_original_audio:
            self._current_processed_audio = processed_file
        command = self.build_command(processed_file)
        if not command:
            if self.pending_files and not self.stop_requested:
                self.start_next_file()
                return
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        self.start_transcription_process(command, clear_output)

    def start_transcription_process(self, command, clear_output):
        self._log_debug_parameters("transcribe")
        self._last_run_cuda_oom = False
        self._last_cuda_oom_snippet = None
        self._last_run_cuda_kernel_incompatible = False
        self._last_cuda_kernel_snippet = None
        self._last_run_cublas_not_supported = False
        self._last_cublas_not_supported_snippet = None

        if clear_output:
            self.output_text.clear()
        
        quoted_command_parts = []
        for arg in command:
            if ' ' in arg or '"' in arg or "'" in arg:
                processed_arg = arg.replace('"', '\\"')
                quoted_command_parts.append(f'"{processed_arg}"')
            else:
                quoted_command_parts.append(arg)
        display_command = ' '.join(quoted_command_parts)
        self._last_command = list(command)
        self._last_display_command = display_command
        
        logging.info(f"Starting QProcess with command: {command}")
        if self.full_console_checkbox.isChecked():
            self._append_text_to_console(f"Running command:\n{display_command}\n" + "="*50 + "\n")
        else:
            status_bits = ["Status: Running"]
            if getattr(self, "current_input_file", None):
                status_bits.append(self._basename_only(self.current_input_file))
            if self._extra_args_has_flag("--diarize") or (getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()):
                status_bits.append("diarization on")
            self._append_text_to_console("\n" + " • ".join(status_bits) + "\n\n\n")

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.process = QProcess(self)
        self._recent_stderr_lines.clear()
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.on_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        env = self._maybe_set_pyannote_env()
        if env is not None:
            self.process.setProcessEnvironment(env)

        self.process.start(command[0], command[1:])
        return True

    def stop_processing(self):
        self.stop_requested = True
        self.pending_files = []
        self.batch_total = 0
        self.batch_index = 0
        if self.preprocess_worker and self.preprocess_worker.isRunning():
            self.preprocess_worker.stop()
            if self.preprocess_dialog:
                self.preprocess_dialog.reject()
        if self.downloader and self.downloader.isRunning():
            self._append_text_to_console("\nRequesting download cancellation...\n")
            self.downloader.stop()
            if not self.process or self.process.state() != QProcess.ProcessState.Running:
                self.run_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                self.downloader = None
                self.stop_requested = False
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self._append_text_to_console("\nTerminating process...\n")
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self._append_text_to_console("Process did not terminate gracefully, killing it.\n\n")
                self.process.kill()


    def _filter_verbose_output(self, data):
        return filter_verbose_output(data)

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
        data = strip_terminal_escapes(data)
        self.check_for_transcription_success(data)
        if self.full_console_checkbox.isChecked():
            self._append_text_to_console(data)
        else:
            filtered = self._filter_verbose_output(data)
            if filtered:
                self._append_text_to_console(filtered + "\n")

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode('utf-8', errors='ignore')
        self.check_for_transcription_success(data)
        if data and self._detect_cuda_oom(data):
            if not self._last_run_cuda_oom:
                lines = [line.strip() for line in data.replace("\r", "\n").split("\n") if line.strip()]
                self._last_cuda_oom_snippet = lines[-3:] if lines else None
                self._log_debug_event(
                    "cuda_oom_detected",
                    input_file=getattr(self, "current_input_file", None),
                    stderr_tail=self._last_cuda_oom_snippet,
                )
            self._last_run_cuda_oom = True
        if data and self._detect_cuda_kernel_incompatible(data):
            if not self._last_run_cuda_kernel_incompatible:
                lines = [line.strip() for line in data.replace("\r", "\n").split("\n") if line.strip()]
                self._last_cuda_kernel_snippet = lines[-3:] if lines else None
                self._log_debug_event(
                    "cuda_kernel_incompatible_detected",
                    input_file=getattr(self, "current_input_file", None),
                    stderr_tail=self._last_cuda_kernel_snippet,
                )
            self._last_run_cuda_kernel_incompatible = True
        if data and self._detect_cublas_not_supported(data):
            if not self._last_run_cublas_not_supported:
                lines = [line.strip() for line in data.replace("\r", "\n").split("\n") if line.strip()]
                self._last_cublas_not_supported_snippet = lines[-3:] if lines else None
                self._log_debug_event(
                    "cublas_not_supported_detected",
                    input_file=getattr(self, "current_input_file", None),
                    stderr_tail=self._last_cublas_not_supported_snippet,
                )
            self._last_run_cublas_not_supported = True
        if data:
            for line in data.replace("\r", "\n").split("\n"):
                line = line.strip()
                if line:
                    self._recent_stderr_lines.append(line)
        if self.full_console_checkbox.isChecked():
            self._append_text_to_console(data)
        else:
            filtered = self._filter_verbose_output(data)
            if filtered:
                self._append_text_to_console(filtered + "\n")

    def _append_text_to_console(self, text_chunk, is_html=False):
        vbar = self.output_text.verticalScrollBar()
        was_at_bottom = True
        if vbar is not None:
            # Only auto-follow new output when the user is already at the bottom.
            was_at_bottom = vbar.value() >= (vbar.maximum() - 2)

        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if is_html:
            cursor.insertHtml(text_chunk)
            self.last_line_was_overwrite = False 
            if was_at_bottom:
                self.output_text.setTextCursor(cursor)
                self.output_text.ensureCursorVisible()
                if vbar is not None:
                    vbar.setValue(vbar.maximum())
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
                cursor.insertText('\n')
                self.last_line_was_overwrite = False
            else:
                self.last_line_was_overwrite = True

        if was_at_bottom:
            self.output_text.setTextCursor(cursor)
            self.output_text.ensureCursorVisible()
            if vbar is not None:
                vbar.setValue(vbar.maximum())


    def create_sentences_only_file(self, txt_with_timestamps, sentences_only_path):
        """Helper method to create sentences-only file from timestamped txt"""
        success, warning = create_sentences_only(txt_with_timestamps, sentences_only_path)
        if warning:
            self._append_text_to_console(f"\nWarning: {warning}\n")
        return success

    def _parse_txt_timestamp(self, value):
        return parse_txt_timestamp(value)

    def _parse_srt_timestamp(self, value):
        return parse_srt_timestamp(value)

    def _read_speaker_segments_from_srt(self, srt_path):
        return read_speaker_segments_from_srt(srt_path)

    def _read_speaker_segments_from_txt(self, txt_path):
        return read_speaker_segments_from_txt(txt_path)

    def _match_speaker_for_segment(self, start, end, speaker_segments):
        return match_speaker_for_segment(start, end, speaker_segments)

    def _extra_args_has_flag(self, flag, text=None):
        if text is None:
            text = self.extra_cli_args.toPlainText().strip()
        return extra_args_has_flag(flag, text)

    def _get_last_review_json_path(self):
        output_dir = self._last_review_output_dir or self.get_output_dir()
        base = self._last_review_output_base
        if output_dir and base:
            candidate = os.path.join(output_dir, f"{base}.json")
            if os.path.exists(candidate):
                return candidate
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select JSON Output",
            output_dir or "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        return path or None

    def _replace_speaker_labels_in_text(self, path, mapping):
        return replace_speaker_labels_in_text(path, mapping)

    def _apply_speaker_names_to_json(self, json_path, mapping):
        return apply_speaker_names_to_json(json_path, mapping)

    def _inject_speakers_into_json_path(self, json_path):
        return inject_speakers_into_json(json_path)

    def _show_diarization_prompt(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Diarization Complete")
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        message_row = QHBoxLayout()
        icon_label = QLabel()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        icon_label.setPixmap(icon.pixmap(32, 32))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        message_row.addWidget(icon_label)

        message_label = QLabel("Diarization finished. Review and rename speakers now?")
        message_label.setWordWrap(True)
        message_row.addWidget(message_label, 1)
        layout.addLayout(message_row)

        button_box = QDialogButtonBox()
        review_button = button_box.addButton("Review Now", QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton("Later", QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(button_box)

        def on_review():
            dialog.accept()
            self.show_diarization_review_dialog()

        review_button.clicked.connect(on_review)
        button_box.rejected.connect(dialog.reject)

        dialog.exec()

    def show_diarization_review_dialog(self):
        json_path = self._get_last_review_json_path()
        if not json_path:
            return
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            QMessageBox.warning(self, "Review Output", f"Failed to read JSON:\n{exc}")
            return
        if isinstance(data, dict):
            segments = data.get("segments") or []
        elif isinstance(data, list):
            segments = data
        else:
            segments = []
        if not segments:
            QMessageBox.information(self, "Review Output", "No segments found in JSON output.")
            return

        if not any(isinstance(segment, dict) and segment.get("speaker") for segment in segments):
            if self._inject_speakers_into_json_path(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    if isinstance(data, dict):
                        segments = data.get("segments") or []
                    elif isinstance(data, list):
                        segments = data
                except Exception:
                    pass

        speakers = []
        speaker_names = {}
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            speaker_id = segment.get("speaker")
            if not speaker_id:
                continue
            if speaker_id not in speaker_names:
                speaker_names[speaker_id] = segment.get("speaker_name") or ""
            if speaker_id not in speakers:
                speakers.append(speaker_id)
        speakers.sort()

        dialog = QDialog(self)
        dialog.setWindowTitle("Review Diarization Output")
        dialog.setModal(True)
        dialog.setMinimumSize(960, 640)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        title = QLabel(f"Output: {os.path.basename(json_path)}")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        speaker_group = QGroupBox("Speaker Names")
        speaker_layout = QFormLayout(speaker_group)
        speaker_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        speaker_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        speaker_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        speaker_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        speaker_layout.setHorizontalSpacing(10)
        speaker_layout.setVerticalSpacing(4)
        speaker_layout.setContentsMargins(8, 8, 8, 8)

        mapping_inputs = {}
        if speakers:
            for speaker_id in speakers:
                line_edit = QLineEdit()
                line_edit.setPlaceholderText("Optional name")
                line_edit.setText(speaker_names.get(speaker_id, ""))
                speaker_layout.addRow(f"{speaker_id}:", line_edit)
                mapping_inputs[speaker_id] = line_edit
        else:
            empty_label = QLabel("No speaker labels found in this JSON. Run diarization or select an output with speaker tags.")
            empty_label.setWordWrap(True)
            empty_label.setStyleSheet("color: #a0a0a0;")
            speaker_layout.addRow(empty_label)

        layout.addWidget(speaker_group)

        table = QTableWidget(dialog)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Start (s)", "End (s)", "Speaker", "Text"])
        table.setRowCount(len(segments))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        speaker_rows = {sp: [] for sp in speakers}
        for row, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            start = segment.get("start")
            end = segment.get("end")
            speaker_id = segment.get("speaker") or ""
            speaker_name = segment.get("speaker_name") or ""
            speaker_label = speaker_name or speaker_id
            text = (segment.get("text") or "").strip()
            start_item = QTableWidgetItem(f"{float(start):.2f}" if isinstance(start, (int, float)) else "")
            end_item = QTableWidgetItem(f"{float(end):.2f}" if isinstance(end, (int, float)) else "")
            speaker_item = QTableWidgetItem(speaker_label)
            text_item = QTableWidgetItem(text)
            table.setItem(row, 0, start_item)
            table.setItem(row, 1, end_item)
            table.setItem(row, 2, speaker_item)
            table.setItem(row, 3, text_item)
            if speaker_id in speaker_rows:
                speaker_rows[speaker_id].append(row)

        layout.addWidget(table)

        update_text_outputs_checkbox = QCheckBox("Update speaker labels in SRT/TXT/LRC/VTT outputs")
        update_text_outputs_checkbox.setChecked(True)
        layout.addWidget(update_text_outputs_checkbox)

        button_row = QHBoxLayout()
        save_button = QPushButton("Apply Names")
        close_button = QPushButton("Close")
        save_button.setMinimumWidth(130)
        close_button.setMinimumWidth(110)
        button_row.addWidget(save_button)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        if not speakers:
            save_button.setEnabled(False)
            update_text_outputs_checkbox.setEnabled(False)

        def refresh_table_for_speaker(speaker_id, name):
            rows = speaker_rows.get(speaker_id) or []
            label = name.strip() or speaker_id
            for row in rows:
                item = table.item(row, 2)
                if item:
                    item.setText(label)

        for speaker_id, line_edit in mapping_inputs.items():
            line_edit.textChanged.connect(lambda text, sid=speaker_id: refresh_table_for_speaker(sid, text))

        def on_apply():
            mapping = {
                speaker_id: line_edit.text().strip()
                for speaker_id, line_edit in mapping_inputs.items()
                if line_edit.text().strip()
            }
            if not mapping:
                QMessageBox.information(dialog, "Review Output", "No speaker names were entered.")
                return
            json_updated = self._apply_speaker_names_to_json(json_path, mapping)
            text_updated = False
            if update_text_outputs_checkbox.isChecked():
                base = os.path.splitext(json_path)[0]
                for ext in ("srt", "txt", "lrc", "vtt"):
                    text_updated = self._replace_speaker_labels_in_text(f"{base}.{ext}", mapping) or text_updated
            if json_updated or text_updated:
                QMessageBox.information(dialog, "Review Output", "Speaker names applied.")
            else:
                QMessageBox.information(dialog, "Review Output", "No changes were made.")

        save_button.clicked.connect(on_apply)
        close_button.clicked.connect(dialog.accept)

        dialog.exec()

    def _maybe_inject_speakers_into_json(self, input_file_path):
        if not input_file_path:
            return False
        diarize_enabled = bool(getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked())
        if not diarize_enabled and not self._extra_args_has_flag("--diarize"):
            return False
        formats = getattr(self, "last_output_formats", [])
        if "json" not in formats and "all" not in formats:
            return False
        output_dir = self.get_output_dir()
        if not output_dir:
            return False
        filename_only = self._current_output_basename or os.path.splitext(os.path.basename(input_file_path))[0]
        json_path = os.path.join(output_dir, filename_only + ".json")

        srt_path = os.path.join(output_dir, filename_only + ".srt")
        txt_path = os.path.join(output_dir, filename_only + ".txt")
        temp_srt = bool(getattr(self, "_temp_srt_for_json", False))
        temp_txt = bool(getattr(self, "_temp_txt_for_json", False))
        updated = False

        try:
            if not os.path.exists(json_path):
                return False
            speaker_segments = self._read_speaker_segments_from_srt(srt_path)
            if not speaker_segments:
                speaker_segments = self._read_speaker_segments_from_txt(txt_path)
            if not speaker_segments:
                return False

            with open(json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                segments = data.get("segments") or []
            elif isinstance(data, list):
                segments = data
            else:
                return False
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                if segment.get("speaker"):
                    continue
                start = segment.get("start")
                end = segment.get("end")
                speaker = self._match_speaker_for_segment(start, end, speaker_segments)
                if speaker:
                    segment["speaker"] = speaker
                    updated = True
            if not updated:
                return False
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=4, ensure_ascii=False)
            if self.full_console_checkbox.isChecked():
                self._append_text_to_console("\nAdded speaker labels to JSON output.\n")
        except Exception as exc:
            if self.full_console_checkbox.isChecked():
                self._append_text_to_console(f"\nFailed to add speaker labels to JSON: {exc}\n")
            return False
        finally:
            if temp_srt and os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except Exception:
                    pass
            if temp_txt and os.path.exists(txt_path):
                try:
                    os.remove(txt_path)
                except Exception:
                    pass
        return updated

    def _format_lrc_timestamp(self, seconds):
        return format_lrc_timestamp(seconds)

    def create_enhanced_lrc_from_json(self, input_file_path):
        """Generate enhanced LRC with word timestamps from JSON output."""
        try:
            output_dir = self.get_output_dir()
            filename_only = self._current_output_basename or os.path.splitext(os.path.basename(input_file_path))[0]
            json_path = os.path.join(output_dir, filename_only + '.json')
            lrc_path = os.path.join(output_dir, filename_only + '.lrc')

            if not os.path.exists(json_path):
                self._append_text_to_console(f"\nWarning: JSON file not found at {json_path}\n")
                return False

            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            segments = []
            if isinstance(data, dict):
                segments = data.get("segments") or []
            elif isinstance(data, list):
                segments = data

            if not segments:
                self._append_text_to_console("\nWarning: No segments found in JSON file\n")
                return False

            lines = build_lrc_lines(segments)

            if not lines:
                self._append_text_to_console("\nWarning: No valid LRC lines created from JSON\n")
                return False

            with open(lrc_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines) + "\n")

            if self._auto_json_for_lrc and os.path.exists(json_path):
                os.remove(json_path)

            return True
        except Exception as e:
            self._append_text_to_console(f"Error creating enhanced LRC file: {str(e)}\n")
            return False

    def handle_txt_format_selection(self, input_file_path):
        """Handle txt format files based on user selection"""
        try:
            output_dir = self.get_output_dir()
            filename_only = self._current_output_basename or os.path.splitext(os.path.basename(input_file_path))[0]
            original_base = os.path.splitext(os.path.basename(input_file_path))[0]
            txt_with_timestamps = os.path.join(output_dir, filename_only + '.txt')
            sentences_only_path = os.path.join(output_dir, filename_only + '_sentences.txt')
            
            wants_timestamps = getattr(self, 'txt_with_timestamps_requested', False)
            wants_sentences = getattr(self, 'sentences_only_requested', False)
            
            if wants_sentences:
                source_txt = txt_with_timestamps
                if not os.path.exists(source_txt) and filename_only != original_base:
                    source_txt = os.path.join(output_dir, original_base + '.txt')
                success = self.create_sentences_only_file(source_txt, sentences_only_path)
                if success:
                    formats = getattr(self, "last_output_formats", [])
                    if len(formats) <= 1:
                        self._append_text_to_console(f"\nOutput saved to: {sentences_only_path}\n")
                        self._single_output_line_emitted = True
            
            if wants_sentences and not wants_timestamps:
                if os.path.exists(txt_with_timestamps):
                    os.remove(txt_with_timestamps)
                elif filename_only != original_base:
                    original_txt = os.path.join(output_dir, original_base + '.txt')
                    if os.path.exists(original_txt):
                        os.remove(original_txt)
            
        except Exception as e:
            self._append_text_to_console(f"Error handling txt format selection: {str(e)}\n")

    def on_finished(self, exit_code, exit_status):
        logging.info(f"QProcess finished. Exit Code: {exit_code}, Exit Status: {exit_status}")
        
        if self.output_buffer:
            self._append_text_to_console(self.output_buffer + '\n')
            self.output_buffer = ""
        
        if self.last_line_was_overwrite:
            self.output_text.append("")
        
        if self.full_console_checkbox.isChecked():
            self._append_text_to_console("="*50 + "\n")
        if self.stop_requested:
            if self.full_console_checkbox.isChecked():
                self._append_text_to_console("Process stopped by user.\n")
            else:
                self._append_text_to_console("\nStatus: Stopped\n")
        elif exit_code == 0 or self.transcription_completed_successfully:
            if self.full_console_checkbox.isChecked():
                self._append_text_to_console("Process completed successfully.\n")
            else:
                self._append_text_to_console("\nStatus: Completed\n")

            self.rename_outputs_for_current_run()
            self._last_review_output_dir = self.get_output_dir()
            if self._current_output_basename:
                self._last_review_output_base = self._current_output_basename
            elif getattr(self, "current_input_file", None):
                self._last_review_output_base = os.path.splitext(os.path.basename(self.current_input_file))[0]
            self._review_link_ready = True
            self._update_diarize_review_link()
            if not self.full_console_checkbox.isChecked():
                if self._extra_args_has_flag("--diarize") or (getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()):
                    self._append_text_to_console(
                        "Review Output: VAD tab → Diarization → Review Diarization Output\n"
                    )
            if (
                (self._extra_args_has_flag("--diarize") or (getattr(self, "diarize_enable", None) and self.diarize_enable.isChecked()))
                and not self.pending_files
                and not (self.downloader and self.downloader.isRunning())
            ):
                if not getattr(self, "diarize_review_prompt_checkbox", None) or self.diarize_review_prompt_checkbox.isChecked():
                    self._show_diarization_prompt()

            if ((hasattr(self, 'sentences_only_requested') and self.sentences_only_requested) or
                (hasattr(self, 'txt_with_timestamps_requested') and self.txt_with_timestamps_requested)) and \
               hasattr(self, 'current_input_file') and self.current_input_file:
                self.handle_txt_format_selection(self.current_input_file)
            if (
                getattr(self, "word_timestamps_checkbox", None)
                and self.word_timestamps_checkbox.isChecked()
                and hasattr(self, 'current_input_file')
                and self.current_input_file
            ):
                formats = getattr(self, "last_output_formats", [])
                if "lrc" in formats or "all" in formats:
                    success = self.create_enhanced_lrc_from_json(self.current_input_file)
                    if success and not self.full_console_checkbox.isChecked():
                        self._append_text_to_console(
                            f"\nWord timestamps LRC saved to: {self.get_output_dir()}\n"
                        )
            if hasattr(self, 'current_input_file') and self.current_input_file:
                injected = self._maybe_inject_speakers_into_json(self.current_input_file)
                if injected and not self.full_console_checkbox.isChecked():
                    self._append_text_to_console("\nSpeaker labels added to JSON.\n")
            if not self.full_console_checkbox.isChecked():
                output_dir = self.get_output_dir()
                formats = getattr(self, "last_output_formats", [])
                if formats:
                    formats_label = ", ".join(fmt.replace("(", "[").replace(")", "]") for fmt in formats)
                    has_non_txt = any(not fmt.startswith("txt") for fmt in formats)
                    if has_non_txt or not getattr(self, "_single_output_line_emitted", False):
                        if len(formats) > 1:
                            self._append_text_to_console(f"\nOutputs saved to: {output_dir} ({formats_label})\n")
        else:
            remaining = ""
            if self.process:
                try:
                    remaining = self.process.readAllStandardError().data().decode('utf-8', errors='ignore')
                except Exception:
                    remaining = ""
            if remaining:
                if self._detect_cublas_not_supported(remaining):
                    self._last_run_cublas_not_supported = True
                    lines = [l.strip() for l in remaining.replace("\r", "\n").split("\n") if l.strip()]
                    self._last_cublas_not_supported_snippet = lines[-3:] if lines else None
                for line in remaining.replace("\r", "\n").split("\n"):
                    line = line.strip()
                    if line:
                        self._recent_stderr_lines.append(line)
            if self._should_retry_cublas_compute():
                if self._start_cublas_compute_retry():
                    return
            if self._should_retry_with_vad_cpu():
                if self._start_vad_cpu_retry():
                    return
            if self._should_retry_with_vad_cpu_for_kernel():
                if self._start_vad_cpu_retry(reason="cuda_kernel_incompatible"):
                    return
            if self._should_retry_custom_model_on_crash(exit_code, exit_status):
                if self._start_custom_model_retry():
                    return
            status_str = "Crashed" if exit_status == QProcess.ExitStatus.CrashExit else "Failed"
            self._append_text_to_console(f"Process {status_str} with exit code {exit_code}.\n")
            stderr_tail = list(self._recent_stderr_lines)[-8:] if self._recent_stderr_lines else []
            if stderr_tail:
                self._append_text_to_console("Last error lines:\n" + "\n".join(stderr_tail) + "\n")
            model_info = self._get_current_model_arch_info()
            model_entry = self._find_model_entry(model_info.get("model") if model_info else None)
            if model_entry and model_entry.get("verify_status") == "failed":
                self._append_text_to_console(
                    "Note: This model is marked as incompatible by Verify Models.\n"
                )
            self._log_debug_event(
                "process_failed",
                input_file=getattr(self, "current_input_file", None),
                exit_code=exit_code,
                exit_status=getattr(exit_status, "name", str(exit_status)),
                stderr_tail=stderr_tail,
                model=model_info.get("model") if model_info else None,
                model_arch=model_info.get("arch") if model_info else None,
                model_note=model_info.get("note") if model_info else None,
            )
            log_path = self._write_last_error_log(exit_code, exit_status, stderr_tail=stderr_tail)
            if log_path:
                self._append_text_to_console(f"Details saved to: {log_path}\n")

        self.cleanup_processed_audio()

        if self.pending_files and not self.stop_requested:
            self.process = None
            self.transcription_completed_successfully = False
            self.output_buffer = ""
            self.last_line_was_overwrite = False
            self.start_next_file()
            return

        if self.downloader and self.downloader.isRunning():
            if self.serial_download_waiting and not self.stop_requested:
                self.serial_download_waiting = False
                self.downloader.allow_next_download()
            self.process = None
            self.run_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            return

        self._report_skipped_summary()
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.process = None
        self.downloader = None
        self.stop_requested = False
        self._force_output_suffix = False

    def on_process_error(self, error):
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
        self._append_text_to_console(f"{ '='*50 }\nPROCESS ERROR:\n{error_message}\n{ '='*50 }\n")

    def download_and_transcribe(self):
        urls = self.get_input_links()
        if not urls:
            QMessageBox.warning(self, "Warning", "Please add one or more YouTube links.")
            return
        output_path = self.get_output_dir()
        if not output_path:
            return
        if self.settings.get("debug_model_download_logging", False):
            logger = get_model_download_logger()
            if not getattr(logger, "disabled", False):
                logger.info(
                    "[run-params] %s",
                    json.dumps(
                        {
                            "context": "youtube_download",
                            "audio_only": self.audio_only_checkbox.isChecked(),
                        },
                        separators=(",", ":"),
                    ),
                )
        self._model_download_cancelled = False
        self.downloads_completed = False
        self.serial_download_waiting = False
        self.pending_files = []
        self.batch_total = 0
        self.batch_index = 0
        self.batch_total_known = False
        self._download_seen_paths = set()
        
        audio_only = self.audio_only_checkbox.isChecked()
        stream_mode = not self.download_all_checkbox.isChecked()
        
        serial_mode = stream_mode
        ytdlp_exe = None
        source = self.settings.get("yt_dlp_source", "bundled")
        if source == "path":
            ytdlp_exe = self.settings.get("yt_dlp_exe_override") or shutil.which("yt-dlp")
            if not ytdlp_exe:
                QMessageBox.warning(self, "yt-dlp", "yt-dlp was not found in PATH. Please set a custom path.")
                self.run_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                return
        cookies_from_browser = self.settings.get("yt_dlp_cookies_from_browser") or None
        cookies_file = self.settings.get("yt_dlp_cookies_file") or None
        self.downloader = YouTubeDownloader(
            urls,
            output_path,
            audio_only,
            stream_mode=stream_mode,
            serial_mode=serial_mode,
            ytdlp_exe=ytdlp_exe,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file
        )
        self.downloader.finished.connect(self.on_download_finished)
        self.downloader.error.connect(self.on_download_error)
        self.downloader.progress.connect(self.handle_download_progress)
        self.downloader.file_ready.connect(self.on_download_file_ready)
        self.downloader.total_found.connect(self.on_download_total_found)
        
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.output_text.clear()
        if stream_mode:
            self._append_text_to_console("Streaming downloads: transcribing as items finish.\n\n")
        self._append_text_to_console(
            "Starting download from:\n" + "\n".join(urls) + "\n" + "="*50 + "\n"
        )
        self.downloader.start()

    def handle_download_progress(self, text):
        text = strip_terminal_escapes(text)
        if text.strip() == "Downloading with yt-dlp.exe...":
            self._append_text_to_console(text + "\n\n")
        elif text.startswith("Downloading"):
            self._append_text_to_console(text + "\r")
        elif "Downloaded:" in text:
            self._append_text_to_console(text + "\n\n")
        else:
            self._append_text_to_console(text + "\n")

    def on_download_finished(self, file_paths):
        self.downloads_completed = True
        if self.stop_requested:
            self.on_finished(0, QProcess.ExitStatus.NormalExit) 
            return

        if not file_paths:
            self._append_text_to_console("Download finished with no files.\n" + "="*50 + "\n")
            if not self.process and not self.pending_files:
                self.run_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                self.downloader = None
            return
        self.last_downloaded_file = file_paths[-1]

        if self.download_all_checkbox.isChecked():
            self._append_text_to_console(
                "Download finished, output files: "
                + ", ".join(file_paths)
                + "\n"
                + "="*50
                + "\n"
            )
            self.pending_files = []
            for path in file_paths:
                normalized = self._normalize_download_path(path)
                if normalized in self._download_seen_paths:
                    logging.info("Skipping duplicate download output: %s", path)
                    continue
                self._download_seen_paths.add(normalized)
                self.pending_files.append(path)
            self.batch_total = len(self.pending_files)
            self.batch_index = 0
            self.batch_total_known = True
            self.start_next_file()
        else:
            if not self.process and not self.pending_files:
                self._report_skipped_summary()
                self.run_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)

    def on_download_error(self, error_message):
        if "cancelled by user" in error_message and self.stop_requested:
            self._append_text_to_console("\nDownload cancelled by user.\n")
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.downloader = None
            self.stop_requested = False
            return

        self._append_text_to_console(f"YouTube Download Error:\n{error_message}\n")
        error_lower = (error_message or "").lower()

        if self._is_cookie_auth_error(error_lower):
            self.downloader = None
            self._handle_cookie_auth_error()
            return

        if (
            "http error 403" in error_lower
            or "403: forbidden" in error_lower
            or "403 forbidden" in error_lower
        ):
            self._append_text_to_console(
                "\nTip: This usually means yt-dlp is outdated or YouTube changed access rules.\n"
                f"Try Settings -> yt-dlp and switch Source to {executable_word()}, then use the latest yt-dlp {executable_word(False)}.\n"
                "If you use Python module mode, restart the app and accept the yt-dlp update prompt.\n"
            )
            if not self._shown_ytdlp_403_hint:
                self._shown_ytdlp_403_hint = True
                QMessageBox.information(
                    self,
                    "yt-dlp 403 Forbidden",
                    (
                        "YouTube returned HTTP 403 (Forbidden).\n\n"
                        "Most common fix: use the latest yt-dlp.exe.\n"
                        f"Go to Settings -> yt-dlp, set Source to {executable_word()}, and point it to a new yt-dlp {executable_word(False)}.\n\n"
                        "If you prefer Python module mode, restart and accept the yt-dlp update prompt."
                    ),
                )
        self.downloads_completed = True
        if not self.process and not self.pending_files:
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        self.downloader = None

    def _is_cookie_auth_error(self, error_lower):
        return (
            "sign in to confirm" in error_lower
            or "cookies-from-browser" in error_lower
            or "use --cookies" in error_lower
        )

    def _detect_installed_browsers(self):
        browser_executables = {
            "brave": ["brave", "brave-browser"],
            "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
            "chromium": ["chromium", "chromium-browser"],
            "edge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
            "firefox": ["firefox", "librewolf", "waterfox", "floorp"],
            "opera": ["opera"],
            "safari": ["safari"],
            "vivaldi": ["vivaldi", "vivaldi-stable"],
            "whale": ["whale", "naver-whale"],
        }
        installed = []
        for browser, exe_names in browser_executables.items():
            for exe in exe_names:
                if shutil.which(exe):
                    installed.append(browser)
                    break
        return installed

    def _handle_cookie_auth_error(self):
        already_set = self.settings.get("yt_dlp_cookies_from_browser") or ""
        already_set_file = self.settings.get("yt_dlp_cookies_file") or ""
        if already_set:
            self._append_text_to_console(
                f"\nCookie authentication failed even with browser '{already_set}' configured.\n"
                "Try a different browser or ensure you are logged into YouTube in that browser.\n"
            )
        elif already_set_file:
            self._append_text_to_console(
                f"\nCookie authentication failed with cookies file: {already_set_file}\n"
                "The file may be expired or invalid.\n"
            )

        installed_browsers = self._detect_installed_browsers()

        dialog = QDialog(self)
        dialog.setWindowTitle("YouTube Authentication Required")
        dialog.setModal(True)
        dialog.setMinimumSize(440, 320)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        info = QLabel(
            "YouTube is requiring authentication to download this video.\n\n"
            "You can either let yt-dlp read cookies from an installed browser,\n"
            "or provide a cookies.txt file (Netscape format)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        combo = QComboBox()
        combo.addItem("-- Select a browser --", "")
        if installed_browsers:
            for b in installed_browsers:
                combo.addItem(b.capitalize(), b)
        else:
            combo.addItem("(no browsers detected)", "")
        if already_set and already_set in installed_browsers:
            combo.setCurrentIndex(installed_browsers.index(already_set) + 1)
        layout.addWidget(combo)

        file_row = QHBoxLayout()
        file_label = QLabel("Or use cookies file:")
        cookie_file_path = QLineEdit()
        cookie_file_path.setPlaceholderText("Path to cookies.txt")
        if already_set_file:
            cookie_file_path.setText(already_set_file)
        browse_btn = QPushButton("Browse...")
        file_row.addWidget(file_label)
        file_row.addWidget(cookie_file_path, 1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        def on_browse():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Select cookies.txt", "", "Text files (*.txt);;All files (*)"
            )
            if path:
                cookie_file_path.setText(path)
                combo.setCurrentIndex(0)
        browse_btn.clicked.connect(on_browse)

        button_row = QHBoxLayout()
        retry_btn = QPushButton("Save && Retry")
        retry_btn.setEnabled(bool(already_set) or bool(already_set_file))
        cancel_btn = QPushButton("Cancel")
        button_row.addStretch()
        button_row.addWidget(retry_btn)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        def update_retry_state():
            has_browser = bool(combo.currentData())
            has_file = bool(cookie_file_path.text().strip())
            retry_btn.setEnabled(has_browser or has_file)

        combo.currentIndexChanged.connect(lambda _: update_retry_state())
        cookie_file_path.textChanged.connect(lambda _: update_retry_state())

        chosen = {"browser": None, "file": None}

        def on_retry():
            file_path = cookie_file_path.text().strip()
            if file_path:
                chosen["file"] = file_path
                chosen["browser"] = None
            else:
                chosen["browser"] = combo.currentData()
                chosen["file"] = None
            dialog.accept()

        retry_btn.clicked.connect(on_retry)
        cancel_btn.clicked.connect(dialog.reject)

        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted and (chosen["browser"] or chosen["file"]):
            if chosen["file"]:
                self.settings["yt_dlp_cookies_file"] = chosen["file"]
                self.settings["yt_dlp_cookies_from_browser"] = ""
                self.save_settings_to_file()
                self._append_text_to_console(
                    f"\nCookies file set: {chosen['file']}. Retrying download...\n"
                    + "=" * 50 + "\n"
                )
            else:
                self.settings["yt_dlp_cookies_from_browser"] = chosen["browser"]
                self.settings["yt_dlp_cookies_file"] = ""
                self.save_settings_to_file()
                self._append_text_to_console(
                    f"\nCookies set to use browser: {chosen['browser']}. Retrying download...\n"
                    + "=" * 50 + "\n"
                )
            self.download_and_transcribe()
        else:
            self._append_text_to_console("\nCookie setup cancelled. Download aborted.\n")
            self.downloads_completed = True
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

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
            temp_file = self.settings_file + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(self.settings, f, indent=4)
            
            if os.path.exists(temp_file):
                if os.path.exists(self.settings_file):
                    os.remove(self.settings_file)
                os.rename(temp_file, self.settings_file)
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")
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
        self.settings["model"] = self._get_selected_model_name()
        self.settings["task"] = self.task_combo.currentText()
        self.settings["language"] = self.get_language_code()
        self.settings["compute_type"] = self.compute_combo.currentText()
        self.settings["device"] = self.device_combo.currentText()
        self.settings["temperature"] = self.temperature.value()
        self.settings["beam_size"] = self.beam_size.value()
        self.settings["best_of"] = self.best_of.value()
        self.settings["patience"] = self.patience.value()
        self.settings["initial_prompt"] = self.initial_prompt.toPlainText()
        self.settings["extra_cli_args"] = self.extra_cli_args.toPlainText()
        if getattr(self, "converter_python_path_input", None):
            self.settings["converter_python_path"] = self.converter_python_path_input.text().strip()
        self.settings["audio_preprocess_enabled"] = self.audio_preprocess_enable.isChecked()
        self.settings["audio_gain_db"] = self.audio_gain.value()
        self.settings["audio_normalize_enabled"] = self.audio_normalize.isChecked()
        self.settings["audio_lufs_target"] = self.audio_lufs_target.value()
        self.settings["audio_true_peak_enabled"] = self.audio_true_peak_enable.isChecked()
        self.settings["audio_true_peak_db"] = self.audio_true_peak.value()
        self.settings["audio_lra"] = self.audio_lra.value()
        self.settings["keep_preprocessed_audio"] = self.keep_preprocessed_audio.isChecked()
        self.settings["fw_executable_override"] = self.fw_exe_path.text().strip()
        self.settings["model_dir_override"] = self.model_dir_path.text().strip()
        self.settings["yt_dlp_source"] = self.ytdlp_source_combo.currentData()
        self.settings["yt_dlp_exe_override"] = self.ytdlp_exe_path.text().strip()
        self.settings["ffmpeg_override"] = self.ffmpeg_path_input.text().strip()
        
        self.settings["vad_method"] = self.vad_method.currentText()
        self.settings["vad_device"] = self.vad_device.currentText()
        self.settings["vad_threshold"] = self.vad_threshold.value()
        self.settings["vad_min_speech"] = self.vad_min_speech.value()
        self.settings["diarize_backend"] = self.diarize_backend.currentText()
        self.settings["diarize_device"] = self.diarize_device.currentText()
        self.settings["diarize_num_speakers"] = self.diarize_num_speakers.value()
        self.settings["diarize_min_speakers"] = self.diarize_min_speakers.value()
        self.settings["diarize_max_speakers"] = self.diarize_max_speakers.value()
        
        checkbox_settings = dict(self.settings.get("checkboxes", {}))
        checkbox_settings.update(
            {cb.objectName(): cb.isChecked() for cb in self.findChildren(QCheckBox) if cb.objectName()}
        )
        self.settings["checkboxes"] = checkbox_settings

        output_formats = [fmt for fmt, cb in self.output_format_checkboxes.items() if cb.isChecked()]
        self.settings["output_formats"] = output_formats
        
        self.save_settings_to_file()

    def load_settings(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.settings = json.load(f)
            elif os.path.exists(self.old_roaming_settings_file):
                logging.info("Migrating settings from roaming folder back to portable location")
                with open(self.old_roaming_settings_file, "r") as f:
                    self.settings = json.load(f)
                self.save_settings_to_file()
                logging.info("Settings migrated back to portable location")
            elif os.path.exists("settings.json"):
                logging.info("Found original settings.json, keeping in portable location")
                with open("settings.json", "r") as f:
                    self.settings = json.load(f)
                if not os.path.samefile("settings.json", self.settings_file):
                    self.save_settings_to_file()
            else:
                self.settings = {}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.warning(f"Could not load settings file: {e}. Using defaults.")
            self.settings = {}

        if "theme" not in self.settings:
            system_theme = self.get_system_theme()
            theme = system_theme
        else:
            theme = self.settings.get("theme", "dark")
        
        self.theme_combo.blockSignals(True)
        theme_display_map = {
            "light": "Light",
            "dark": "Dark", 
            "amoled": "AMOLED"
        }
        display_name = theme_display_map.get(theme.lower(), "Dark")
        self.theme_combo.setCurrentText(display_name)
        self.theme_combo.blockSignals(False)
        self.apply_theme(theme)

        if not getattr(sys, "frozen", False):
            if geometry_hex := self.settings.get("geometry"):
                try:
                    if isinstance(geometry_hex, str) and all(c in '0123456789abcdefABCDEF' for c in geometry_hex):
                        self.restoreGeometry(QByteArray.fromHex(bytes(geometry_hex, 'utf-8')))
                    else:
                        logging.warning("Invalid geometry data in settings")
                except Exception as e:
                    logging.warning(f"Failed to restore window geometry: {e}")
            if splitter_sizes := self.settings.get("splitter_sizes"):
                self.main_splitter.setSizes(splitter_sizes)
                self._apply_tab_minimums()
                self._enforce_splitter_sizes_for_frozen()

        self.output_dir.setText(self.settings.get("output_dir", ""))
        source_mode = self.settings.get("checkboxes", {}).get("output_dir_source_checkbox", False)
        if self.output_dir.text().strip().lower() == "source":
            source_mode = True
        if getattr(self, "output_dir_source_checkbox", None):
            self.output_dir_source_checkbox.setChecked(source_mode)
            self.update_output_dir_mode(source_mode, save_text=False)
        self._ensure_models_registry()
        self._sync_models_from_disk()
        self._refresh_model_combo(preferred_name=self.settings.get("model", "large-v3"))
        self.task_combo.setCurrentText(self.settings.get("task", "transcribe"))
        language_code = self.settings.get("language", "auto")
        language_index = self.language_combo.findData(language_code)
        if language_index >= 0:
            self.language_combo.setCurrentIndex(language_index)
        else:
            self.language_combo.setCurrentText(language_code)
        self.compute_combo.setCurrentText(self.settings.get("compute_type", "float16"))
        self.device_combo.setCurrentText(self.settings.get("device", "cuda"))
        self.temperature.setValue(self.settings.get("temperature", 0.0))
        self.beam_size.setValue(self.settings.get("beam_size", 5))
        self.best_of.setValue(self.settings.get("best_of", 5))
        self.patience.setValue(self.settings.get("patience", 1.0))
        self.initial_prompt.setPlainText(self.settings.get("initial_prompt", ""))
        self.extra_cli_args.setPlainText(self.settings.get("extra_cli_args", ""))
        converter_path = self.settings.get("converter_python_path", "")
        if getattr(self, "converter_python_path_input", None):
            self.converter_python_path_input.setText(converter_path)
        if converter_path:
            os.environ["FWHISPER_CONVERTER_PYTHON"] = converter_path
        else:
            os.environ.pop("FWHISPER_CONVERTER_PYTHON", None)
        self.audio_gain.setValue(self.settings.get("audio_gain_db", 0.0))
        self.audio_lufs_target.setValue(self.settings.get("audio_lufs_target", -16.0))
        self.audio_true_peak.setValue(self.settings.get("audio_true_peak_db", -1.5))
        self.audio_lra.setValue(self.settings.get("audio_lra", 11.0))
        self.audio_preprocess_enable.setChecked(self.settings.get("audio_preprocess_enabled", False))
        self.audio_normalize.setChecked(self.settings.get("audio_normalize_enabled", False))
        self.audio_true_peak_enable.setChecked(self.settings.get("audio_true_peak_enabled", True))
        self.keep_preprocessed_audio.setChecked(self.settings.get("keep_preprocessed_audio", False))
        self.update_audio_preprocess_controls(self.audio_preprocess_enable.isChecked())
        self.update_audio_normalize_controls(self.audio_normalize.isChecked())
        
        self.vad_method.setCurrentText(self.settings.get("vad_method", "silero_v4_fw"))
        self.vad_device.setCurrentText(self.settings.get("vad_device", "Auto"))
        self.vad_threshold.setValue(self.settings.get("vad_threshold", 0.5))
        self.vad_min_speech.setValue(self.settings.get("vad_min_speech", 250))
        self.diarize_backend.setCurrentText(self.settings.get("diarize_backend", "pyannote_v3.1"))
        self.diarize_device.setCurrentText(self.settings.get("diarize_device", "Auto"))
        self.diarize_num_speakers.setValue(self.settings.get("diarize_num_speakers", 0))
        self.diarize_min_speakers.setValue(self.settings.get("diarize_min_speakers", 0))
        self.diarize_max_speakers.setValue(self.settings.get("diarize_max_speakers", 0))
        if getattr(self, "existing_output_combo", None):
            existing_mode = self.settings.get("existing_output_mode", "suffix")
            existing_index = self.existing_output_combo.findData(existing_mode)
            self.existing_output_combo.setCurrentIndex(existing_index if existing_index >= 0 else 0)

        all_checkboxes = {cb.objectName(): cb for cb in self.findChildren(QCheckBox) if cb.objectName()}
        checkbox_settings = self.settings.get("checkboxes", {})
        for name, checked in checkbox_settings.items():
            if name in all_checkboxes:
                all_checkboxes[name].setChecked(checked)
        self.apply_tooltip_visibility(self.tooltips_checkbox.isChecked())
        self.update_diarization_controls(self.diarize_enable.isChecked())
        
        output_formats = self.settings.get("output_formats", ["srt"])
        for fmt, cb in self.output_format_checkboxes.items():
            cb.setChecked(fmt in output_formats)

        self.fw_exe_path.setText(self.settings.get("fw_executable_override", ""))
        self.model_dir_path.setText(self.settings.get("model_dir_override", ""))
        self.ytdlp_exe_path.setText(self.settings.get("yt_dlp_exe_override", ""))
        self.ffmpeg_path_input.setText(self.settings.get("ffmpeg_override", ""))
        source = self.settings.get("yt_dlp_source", "bundled")
        if source == "system":
            source = "bundled"
        index = self.ytdlp_source_combo.findData(source)
        if index >= 0:
            self.ytdlp_source_combo.setCurrentIndex(index)
        self.apply_config_settings()
        if self._models_registry_dirty:
            self.save_settings_to_file()
            self._models_registry_dirty = False

    def load_settings_file_only(self):
        """Load settings from disk before UI init (no widget access)."""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.settings = json.load(f)
            elif os.path.exists(self.old_roaming_settings_file):
                with open(self.old_roaming_settings_file, "r") as f:
                    self.settings = json.load(f)
            elif os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    self.settings = json.load(f)
            else:
                self.settings = {}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.warning(f"Could not load settings file: {e}. Using defaults.")
            self.settings = {}
