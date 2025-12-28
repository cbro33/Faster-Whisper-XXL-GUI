import sys
import os
import json
import logging
import shutil
import requests
import webbrowser
import platform
import re
import ntpath
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QHBoxLayout, QVBoxLayout, QLabel, 
    QComboBox, QTabWidget, QGroupBox, QFormLayout, QPushButton, 
    QSizePolicy, QCheckBox, QTextEdit, QDoubleSpinBox, QSpinBox, 
    QScrollArea, QFileDialog, QCompleter, QListWidget, QAbstractItemView,
    QSpacerItem, QMessageBox, QApplication, QGridLayout, QLineEdit, QDialog
)
from PyQt6.QtCore import Qt, QTimer, QProcess, QByteArray, QUrl
from PyQt6.QtGui import QIcon, QPalette, QColor, QTextCursor, QFont, QDesktopServices

from config import APP_VERSION, SUPPORTED_EXTENSIONS
from utils import (
    get_app_directory, get_settings_directory, get_portable_settings_directory,
    resource_path, format_path_for_display, detect_faster_whisper_binary_version
)
from python_utils import (
    enumerate_python_runtimes, get_execution_environment, get_executable_fallback_path,
    get_python_probe_log
)
from ytdlp_utils import (
    get_system_yt_dlp_info, evaluate_yt_dlp_version_status, can_update_yt_dlp,
    get_python_update_plan, should_check_ytdlp_update, record_ytdlp_update_success,
    record_ytdlp_update_failure, clear_ytdlp_update_failure, is_within_update_cooldown,
    get_ytdlp_installation_info, refresh_yt_dlp_module_after_update, remove_external_yt_dlp
)
from workers import YouTubeDownloader, YtDlpUpdateWorker
from gui_components import (
    DownloadManager, HardwareOptimizationDialog, FileDropGroupBox, FileDropListWidget,
    FileDropLineEdit, FileDropWidget, UpdateProgressDialog, show_setup_critical, 
    show_setup_question, show_setup_warning, show_setup_information,
    show_yt_dlp_unavailable, ModelDownloadDialog, set_model_download_logging_enabled,
    get_model_download_log_path, get_model_download_logger
)
from gpu_utils import detect_hardware_capabilities

class WhisperGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.process = None
        self.downloader = None
        self.stop_requested = False
        self.output_format_checkboxes = {}
        self.pending_files = []
        self.batch_total = 0
        self.batch_index = 0
        
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
        self._single_output_line_emitted = False
        self._auto_highlight_notice_shown = False
        
        if not self.check_and_setup_dependencies():
            QTimer.singleShot(0, self.close)
            return

        self.init_ui()
        self.load_settings()
        set_model_download_logging_enabled(self.settings.get("debug_model_download_logging", False))
        self.setup_realtime_saving()
        
        # Check for hardware optimization on first run
        QTimer.singleShot(500, self.check_hardware_optimization)
        
        # Check yt-dlp version after UI is ready (only if not already checked this session)
        if not self.yt_dlp_update_checked:
            QTimer.singleShot(1000, self.check_yt_dlp_version)
        QTimer.singleShot(1200, self.check_app_update)

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
            detailed_error = f"Failed to set up dependencies: {error_message}"
            if getattr(self.download_manager, "keep_archive_on_error", False):
                archive_path = getattr(self.download_manager, "archive_path", None)
                if archive_path and os.path.exists(archive_path):
                    detailed_error += f"\n\nThe downloaded archive was kept at:\n{os.path.abspath(archive_path)}"
            show_setup_critical(self, "Setup Failed", detailed_error)
            return False

    def init_ui(self):
        self.setWindowTitle(f"Faster Whisper XXL GUI v{APP_VERSION}")
        self.setGeometry(100, 100, 1300, 1000)
        self.setMinimumSize(1100, 800)

        # Create menu bar
        self.create_menu_bar()

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
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tabs.setMaximumHeight(280)
        left_layout.addWidget(self.tabs)

        self.file_tab = FileDropWidget(add_files_callback=self.add_input_files)
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
        global_settings_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(global_settings_group)

        button_layout = self.create_button_layout()
        left_layout.addLayout(button_layout)

        left_layout.setStretch(1, 0)
        left_layout.setStretch(2, 1)
        left_layout.setStretch(3, 0)

        right_panel = self.create_output_console()

        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setSizes([450, 750])

    def setup_file_tab(self, tab):
        layout = QFormLayout(tab)
        input_group = FileDropGroupBox("Input", add_files_callback=self.add_input_files)
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(8, 6, 8, 6)
        input_layout.setSpacing(4)

        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_label = QLabel("Drag & drop files or folders here.")
        hint_label.setStyleSheet("color: #a0a0a0; font-size: 12px;")
        self.file_count_label = QLabel("0 files")
        self.file_count_label.setStyleSheet("color: #a0a0a0; font-size: 12px; font-weight: bold;")
        hint_row.addWidget(hint_label)
        hint_row.addStretch()
        hint_row.addWidget(self.file_count_label)
        input_layout.addLayout(hint_row)

        self.file_list = FileDropListWidget(add_files_callback=self.add_input_files)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.file_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.file_list.setAcceptDrops(True)
        self.file_list.viewport().setAcceptDrops(True)
        self.file_list.setMinimumHeight(80)
        self.file_list.setMaximumHeight(90)
        self.file_list.setFrameShape(QListWidget.Shape.NoFrame)
        input_layout.addWidget(self.file_list)

        file_button_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("Add Files")
        self.add_files_btn.clicked.connect(self.browse_files)
        self.add_folder_btn = QPushButton("Add Folder")
        self.add_folder_btn.clicked.connect(self.browse_folder)
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self.remove_selected_files)
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self.clear_files)
        file_button_layout.addWidget(self.add_files_btn)
        file_button_layout.addWidget(self.add_folder_btn)
        file_button_layout.addWidget(self.remove_btn)
        file_button_layout.addWidget(self.clear_btn)
        input_layout.addLayout(file_button_layout)
        input_group.setMaximumHeight(190)

        layout.addRow(input_group)

    def setup_youtube_tab(self, tab):
        layout = QFormLayout(tab)
        self.youtube_url = FileDropLineEdit()
        self.youtube_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.youtube_url.setToolTip("Paste a YouTube URL to download and transcribe.")
        layout.addRow("YouTube URL:", self.youtube_url)
        self.audio_only_checkbox = QCheckBox("Audio Only (Download faster)")
        self.audio_only_checkbox.setChecked(True)
        self.audio_only_checkbox.setObjectName("audio_only_checkbox")
        self.audio_only_checkbox.setToolTip("Downloads audio only for faster processing.")
        layout.addRow(self.audio_only_checkbox)

    def setup_global_settings(self, layout):
        layout.setVerticalSpacing(4)
        layout.setContentsMargins(8, 4, 8, 4)
        output_dir_container = QWidget()
        output_dir_layout = QHBoxLayout(output_dir_container)
        output_dir_layout.setContentsMargins(0, 0, 0, 0)
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Leave empty for default 'output' folder")
        self.output_dir.setToolTip(
            "Where to save output files. Default is the app folder, or the media folder when using recursive batch. "
            "Use '.' for the current folder or 'source' for the media folder."
        )
        output_dir_label = QLabel("Output Dir:")
        output_dir_label.setToolTip(
            "Where to save output files. Default is the app folder, or the media folder when using recursive batch. "
            "Use '.' for the current folder or 'source' for the media folder."
        )
        self.browse_out_btn = QPushButton("Browse")
        self.browse_out_btn.setToolTip("Choose an output folder.")
        self.browse_out_btn.clicked.connect(self.browse_output_dir)
        output_dir_layout.addWidget(self.output_dir)
        output_dir_layout.addWidget(self.browse_out_btn)
        layout.addRow(output_dir_label, output_dir_container)
        self.model_combo = QComboBox()
        self.model_combo.addItems(['tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en', 'medium', 'medium.en', 'large-v1', 'large-v2', 'large-v3', 'large-v3-turbo', 'distil-large-v2', 'distil-large-v3', 'distil-medium.en', 'distil-small.en'])
        self.model_combo.setCurrentText('large-v3')
        self.model_combo.setToolTip("Choose a model. Larger models are more accurate but use more resources.")
        model_label = QLabel("Model:")
        model_label.setToolTip("Choose a model. Larger models are more accurate but use more resources.")
        layout.addRow(model_label, self.model_combo)
        self.task_combo = QComboBox()
        self.task_combo.addItems(['transcribe', 'translate'])
        self.task_combo.setToolTip("Transcribe keeps the original language; translate outputs English.")
        task_label = QLabel("Task:")
        task_label.setToolTip("Transcribe keeps the original language; translate outputs English.")
        layout.addRow(task_label, self.task_combo)
        self.language_combo = QComboBox()
        languages = [
            ("auto", "Auto"),
            ("af", "Afrikaans"), ("am", "Amharic"), ("ar", "Arabic"), ("as", "Assamese"),
            ("az", "Azerbaijani"), ("ba", "Bashkir"), ("be", "Belarusian"), ("bg", "Bulgarian"),
            ("bn", "Bengali"), ("bo", "Tibetan"), ("br", "Breton"), ("bs", "Bosnian"),
            ("ca", "Catalan"), ("cs", "Czech"), ("cy", "Welsh"), ("da", "Danish"),
            ("de", "German"), ("el", "Greek"), ("en", "English"), ("es", "Spanish"),
            ("et", "Estonian"), ("eu", "Basque"), ("fa", "Persian"), ("fi", "Finnish"),
            ("fo", "Faroese"), ("fr", "French"), ("gl", "Galician"), ("gu", "Gujarati"),
            ("ha", "Hausa"), ("haw", "Hawaiian"), ("he", "Hebrew"), ("hi", "Hindi"),
            ("hr", "Croatian"), ("ht", "Haitian Creole"), ("hu", "Hungarian"), ("hy", "Armenian"),
            ("id", "Indonesian"), ("is", "Icelandic"), ("it", "Italian"), ("ja", "Japanese"),
            ("jw", "Javanese"), ("ka", "Georgian"), ("kk", "Kazakh"), ("km", "Khmer"),
            ("kn", "Kannada"), ("ko", "Korean"), ("la", "Latin"), ("lb", "Luxembourgish"),
            ("ln", "Lingala"), ("lo", "Lao"), ("lt", "Lithuanian"), ("lv", "Latvian"),
            ("mg", "Malagasy"), ("mi", "Maori"), ("mk", "Macedonian"), ("ml", "Malayalam"),
            ("mn", "Mongolian"), ("mr", "Marathi"), ("ms", "Malay"), ("mt", "Maltese"),
            ("my", "Burmese"), ("ne", "Nepali"), ("nl", "Dutch"), ("nn", "Nynorsk"),
            ("no", "Norwegian"), ("oc", "Occitan"), ("pa", "Punjabi"), ("pl", "Polish"),
            ("ps", "Pashto"), ("pt", "Portuguese"), ("ro", "Romanian"), ("ru", "Russian"),
            ("sa", "Sanskrit"), ("sd", "Sindhi"), ("si", "Sinhala"), ("sk", "Slovak"),
            ("sl", "Slovenian"), ("sn", "Shona"), ("so", "Somali"), ("sq", "Albanian"),
            ("sr", "Serbian"), ("su", "Sundanese"), ("sv", "Swedish"), ("sw", "Swahili"),
            ("ta", "Tamil"), ("te", "Telugu"), ("tg", "Tajik"), ("th", "Thai"),
            ("tk", "Turkmen"), ("tl", "Tagalog"), ("tr", "Turkish"), ("tt", "Tatar"),
            ("uk", "Ukrainian"), ("ur", "Urdu"), ("uz", "Uzbek"), ("vi", "Vietnamese"),
            ("yi", "Yiddish"), ("yo", "Yoruba"), ("yue", "Cantonese"), ("zh", "Chinese"),
        ]
        for code, name in languages:
            display = f"{name} ({code})"
            self.language_combo.addItem(display, code)
        self.language_combo.setEditable(True)
        self.language_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.language_combo.setToolTip("Auto-detect or pick a language code.")
        if line_edit := self.language_combo.lineEdit():
            line_edit.setTextMargins(0, 0, 0, 0)
            line_edit.setContentsMargins(0, 0, 0, 0)
        self.language_combo.setStyleSheet(
            "QComboBox { padding-left: 0px; }"
            "QComboBox QLineEdit { padding-left: 0px; margin-left: 0px; }"
        )
        if completer := self.language_combo.completer():
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        language_label = QLabel("Language:")
        language_label.setToolTip("Auto-detect or pick a language code.")
        layout.addRow(language_label, self.language_combo)
        self.compute_combo = QComboBox()
        self.compute_combo.addItems(['default', 'auto', 'int8', 'int8_float16', 'int8_float32', 'int8_bfloat16', 'int16', 'float16', 'float32', 'bfloat16'])
        self.compute_combo.setToolTip(
            "Speed vs quality setting. Lower precision is faster and uses less GPU memory."
        )
        compute_label = QLabel("Compute Type:")
        compute_label.setToolTip(
            "Speed vs quality setting. Lower precision is faster and uses less GPU memory."
        )
        layout.addRow(compute_label, self.compute_combo)
        self.device_combo = QComboBox()
        self.device_combo.addItems(['cuda', 'cpu'])
        self.device_combo.setToolTip(
            "Use GPU (cuda) if available, otherwise CPU."
        )
        device_label = QLabel("Device:")
        device_label.setToolTip(
            "Use GPU (cuda) if available, otherwise CPU."
        )
        layout.addRow(device_label, self.device_combo)
        self.full_console_checkbox = QCheckBox("Show extra console details")
        self.full_console_checkbox.setObjectName("full_console_checkbox")
        self.full_console_checkbox.setToolTip("Show extra status lines (verbose).")
        self.full_console_checkbox.setChecked(False)
        console_label = QLabel("Console Output:")
        console_label.setToolTip("Show extra status lines (verbose).")
        layout.addRow(console_label, self.full_console_checkbox)
        output_format_group = QWidget()
        container_layout = QHBoxLayout(output_format_group)
        container_layout.setContentsMargins(0, 4, 0, 0)
        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(40)
        grid_layout.setVerticalSpacing(2)
        formats = ['json', 'vtt', 'srt', 'lrc', 'txt (with timestamps)', 'txt (sentences only)', 'tsv', 'all']
        num_rows = 4
        for i, fmt in enumerate(formats):
            checkbox = QCheckBox(fmt)
            checkbox.setObjectName(f"format_checkbox_{fmt}")
            checkbox.setToolTip("Select one or more output formats.")
            checkbox.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
            checkbox.setMinimumHeight(20)
            self.output_format_checkboxes[fmt] = checkbox
            row = i % num_rows
            col = i // num_rows
            grid_layout.addWidget(checkbox, row, col, Qt.AlignmentFlag.AlignLeft)
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
        self.tooltips_checkbox = QCheckBox("Show tooltips")
        self.tooltips_checkbox.setObjectName("show_tooltips_checkbox")
        self.tooltips_checkbox.setToolTip("Toggle help tooltips throughout the app.")
        self.tooltips_checkbox.setChecked(True)
        scroll_layout.addRow(self.tooltips_checkbox)
        self.word_timestamps_checkbox = QCheckBox("Word timestamps")
        self.word_timestamps_checkbox.setObjectName("word_timestamps_checkbox")
        self.word_timestamps_checkbox.setToolTip(
            "Include word-level timestamps when supported by the output format. "
            "For SRT/VTT, Highlight words will be enabled automatically."
        )
        scroll_layout.addRow(self.word_timestamps_checkbox)
        self.highlight_words_checkbox = QCheckBox("Highlight words")
        self.highlight_words_checkbox.setObjectName("highlight_words_checkbox")
        self.highlight_words_checkbox.setToolTip(
            "Highlight words as they are spoken (SRT/VTT only). "
            "Required to render word-level timestamps in SRT/VTT."
        )
        scroll_layout.addRow(self.highlight_words_checkbox)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 1.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setDecimals(1)
        self.temperature.setToolTip("Higher values increase randomness; lower values are more consistent.")
        scroll_layout.addRow("Temperature:", self.temperature)
        self.beam_size = QSpinBox()
        self.beam_size.setRange(1, 100)
        self.beam_size.setToolTip("Number of beams used in decoding (temperature 0).")
        scroll_layout.addRow("Beam Size:", self.beam_size)
        self.best_of = QSpinBox()
        self.best_of.setRange(1, 100)
        self.best_of.setToolTip("Number of candidates to consider when temperature > 0.")
        scroll_layout.addRow("Best Of:", self.best_of)
        self.patience = QDoubleSpinBox()
        self.patience.setRange(0.0, 10.0)
        self.patience.setSingleStep(0.1)
        self.patience.setDecimals(1)
        self.patience.setToolTip("Beam search patience; higher may improve accuracy.")
        scroll_layout.addRow("Patience:", self.patience)
        self.initial_prompt = QTextEdit()
        self.initial_prompt.setMaximumHeight(80)
        self.initial_prompt.setToolTip("Optional text to prime the transcription.")
        scroll_layout.addRow("Initial Prompt:", self.initial_prompt)
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        layout = QVBoxLayout(tab)
        layout.addWidget(scroll)

    def setup_vad_tab(self, tab):
        layout = QFormLayout(tab)
        self.vad_filter = QCheckBox("Enable VAD Filter")
        self.vad_filter.setObjectName("vad_filter_checkbox")
        self.vad_filter.setToolTip(
            "Enable voice activity detection to filter non-speech."
        )
        layout.addRow(self.vad_filter)
        self.vad_method = QComboBox()
        self.vad_method.addItems(['silero_v4_fw', 'silero_v5_fw', 'silero_v3', 'silero_v4', 'silero_v5', 'pyannote_v3', 'pyannote_onnx_v3', 'auditok', 'webrtc'])
        self.vad_method.setToolTip("Choose the VAD backend.")
        layout.addRow("VAD Method:", self.vad_method)
        self.vad_threshold = QDoubleSpinBox()
        self.vad_threshold.setRange(0.0, 1.0)
        self.vad_threshold.setSingleStep(0.01)
        self.vad_threshold.setDecimals(2)
        self.vad_threshold.setToolTip(
            "Higher values are stricter about what counts as speech."
        )
        self.vad_threshold.setStyleSheet(
            "QDoubleSpinBox { padding-left: 0px; }"
            "QDoubleSpinBox QLineEdit { padding-left: 0px; margin-left: 0px; }"
        )
        layout.addRow("VAD Threshold:", self.vad_threshold)
        self.vad_min_speech = QSpinBox()
        self.vad_min_speech.setRange(0, 10000)
        self.vad_min_speech.setSuffix(" ms")
        self.vad_min_speech.setToolTip(
            "Minimum duration to treat a segment as speech."
        )
        self.vad_min_speech.setStyleSheet(
            "QSpinBox { padding-left: 0px; }"
            "QSpinBox QLineEdit { padding-left: 0px; margin-left: 0px; }"
        )
        layout.addRow("Min Speech Duration:", self.vad_min_speech)

    def setup_audio_tab(self, tab):
        layout = QFormLayout(tab)
        self.ff_mp3 = QCheckBox("Convert to MP3")
        self.ff_mp3.setObjectName("ff_mp3_checkbox")
        self.ff_mp3.setToolTip("Convert input audio to MP3 before processing.")
        layout.addRow(self.ff_mp3)

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
        if self.yt_dlp_update_in_progress:
            logging.info("yt-dlp update in progress, skipping version check")
            return
        
        if not should_check_ytdlp_update():
            return
        
        self.yt_dlp_update_checked = True
        
        try:
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
            plan_status = plan.get("status")
            
            try:
                response = requests.get("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest", timeout=5)
                if response.status_code == 200:
                    latest_data = response.json()
                    latest_version = latest_data["tag_name"]
                    
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
                                self, "yt-dlp Update Available",
                                update_msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.Yes
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
                else:
                    logging.warning("Could not check for yt-dlp updates")
            except requests.RequestException as e:
                logging.warning(f"Failed to check yt-dlp version: {e}")
                
        except Exception as e:
            logging.error(f"Error checking yt-dlp version: {e}")

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
        if not version_str:
            return ""
        return version_str.strip().lstrip("vV")

    def _version_tuple(self, version_str):
        try:
            return tuple(int(p) for p in re.findall(r"\d+", version_str))
        except Exception:
            return ()

    def check_app_update(self):
        """Check GitHub releases for a newer GUI version and prompt once."""
        try:
            ignore_version = self.settings.get("ignore_update_version")
            current_version = self._normalize_version(APP_VERSION)

            if ignore_version and self._version_tuple(ignore_version) >= self._version_tuple(current_version):
                return

            response = requests.get(
                "https://api.github.com/repos/cbro33/Faster-Whisper-XXL-GUI/releases/latest",
                timeout=5
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
            hardware_menu = menubar.addMenu('Hardware Settings')
            optimize_action = hardware_menu.addAction('Optimize Hardware Settings')
            optimize_action.triggered.connect(self.force_hardware_optimization)
            view_hw_action = hardware_menu.addAction('View Hardware Info')
            view_hw_action.triggered.connect(self.show_hardware_info)
            hardware_menu.addSeparator()
            diagnose_action = hardware_menu.addAction('Diagnose GPU Detection')
            diagnose_action.triggered.connect(self.diagnose_gpu_detection)

            software_menu = menubar.addMenu('Software Information')
            view_software_action = software_menu.addAction('View Software Information')
            view_software_action.triggered.connect(self.show_software_information)

            help_menu = menubar.addMenu('Help')
            wiki_action = help_menu.addAction('Wiki')
            wiki_action.triggered.connect(self.open_wiki_page)
            help_menu.addSeparator()
            debug_logs_action = help_menu.addAction('Debug Settings')
            debug_logs_action.triggered.connect(self.show_debug_log_dialog)
            help_menu.addSeparator()
            check_updates_action = help_menu.addAction('Check for Updates')
            check_updates_action.triggered.connect(self.check_app_update)

        except Exception as e:
            logging.error(f"Error creating menu bar: {e}")

    def _log_debug_parameters(self, context):
        if not self.settings.get("debug_model_download_logging", False):
            return
        logger = get_model_download_logger()
        if getattr(logger, "disabled", False):
            return
        params = {
            "context": context,
            "model": self.model_combo.currentText(),
            "task": self.task_combo.currentText(),
            "language": self.get_language_code(),
            "device": self.device_combo.currentText(),
            "compute_type": self.compute_combo.currentText(),
            "output_formats": getattr(self, "last_output_formats", []),
            "temperature": self.temperature.value(),
            "beam_size": self.beam_size.value(),
            "best_of": self.best_of.value(),
            "patience": self.patience.value(),
            "vad_enabled": self.vad_filter.isChecked(),
            "vad_method": self.vad_method.currentText() if self.vad_filter.isChecked() else None,
            "vad_threshold": self.vad_threshold.value() if self.vad_filter.isChecked() else None,
            "vad_min_speech": self.vad_min_speech.value() if self.vad_filter.isChecked() else None,
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
        }
        logger.info("[run-params] %s", json.dumps(params, separators=(",", ":")))

    def force_hardware_optimization(self):
        """ Force hardware optimization dialog to show """
        try:
            self.show_hardware_optimization_dialog()
        except Exception as e:
            logging.error(f"Error in forced hardware optimization: {e}")

    def open_wiki_page(self):
        """Open the project wiki in the default browser."""
        try:
            import webbrowser
            webbrowser.open("https://github.com/cbro33/Faster-Whisper-XXL-GUI/wiki")
        except Exception as e:
            logging.error(f"Error opening wiki page: {e}")

    def show_debug_log_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Debug Logs")
        dialog.setModal(True)
        dialog.setMinimumSize(560, 300)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 14, 22, 22)
        layout.setSpacing(14)

        title_label = QLabel("Debug Logging")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title_label)

        info_label = QLabel(
            "<div>Enable debugging and write to <b>debug_log.txt</b>.</div>"
            "<div style='margin-top:10px;'>The log includes:</div>"
            "<ul style='margin-top:8px; margin-bottom:0;'>"
            "<li>Model download lifecycle (start, cancel, finish)</li>"
            "<li>Model size detection source (HEAD/API/header)</li>"
            "<li>Run parameters (model, task, device, compute type)</li>"
            "<li>Output formats and advanced toggles (VAD, word timestamps, MP3)</li>"
            "</ul>"
        )
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setStyleSheet("font-size: 14px; line-height: 1.25;")
        layout.addWidget(info_label)
        layout.addSpacing(10)

        enabled_checkbox = QCheckBox("Enable debug logging")
        enabled_checkbox.setChecked(self.settings.get("debug_model_download_logging", False))
        enabled_checkbox.setStyleSheet("margin-top: 6px; margin-bottom: 8px; font-size: 14px;")
        layout.addWidget(enabled_checkbox)

        button_row = QHBoxLayout()
        open_button = QPushButton("Open Log")
        clear_button = QPushButton("Clear Log")
        close_button = QPushButton("Close")
        for button in (open_button, clear_button, close_button):
            button.setMinimumWidth(110)
            button.setMinimumHeight(34)
        button_row.addWidget(open_button)
        button_row.addWidget(clear_button)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        def on_toggle():
            enabled = enabled_checkbox.isChecked()
            self.settings["debug_model_download_logging"] = enabled
            self.save_settings_to_file()
            set_model_download_logging_enabled(enabled)

        def on_open():
            log_path = get_model_download_log_path()
            if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
                QMessageBox.information(self, "Debug Log", "No debug log found yet.")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(log_path))

        def on_clear():
            log_path = get_model_download_log_path()
            try:
                if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
                    QMessageBox.information(self, "Debug Log", "No debug log to clear.")
                    return
                with open(log_path, "w", encoding="utf-8"):
                    pass
                QMessageBox.information(self, "Debug Log", "Debug log cleared.")
            except Exception as exc:
                QMessageBox.warning(self, "Debug Log", f"Unable to clear log file:\n{exc}")

        enabled_checkbox.stateChanged.connect(on_toggle)
        open_button.clicked.connect(on_open)
        clear_button.clicked.connect(on_clear)
        close_button.clicked.connect(dialog.accept)

        dialog.exec()

    def show_hardware_info(self):
        """ Show current hardware information """
        try:
            hardware_info = detect_hardware_capabilities()
            info_text = "Current Hardware Information:\n\n"
            
            if hardware_info["has_cuda"]:
                info_text += f"GPU: {hardware_info['gpu_name']} ({hardware_info['gpu_memory_gb']:.1f}GB VRAM)\n"
                info_text += f"CUDA Version: {hardware_info['cuda_version']}\n"
            else:
                info_text += "GPU: No CUDA-compatible GPU detected\n"
            
            info_text += f"System RAM: {hardware_info['ram_gb']:.1f}GB\n"
            info_text += f"CPU Cores: {hardware_info['cpu_cores']}\n"
            info_text += f"Platform: {hardware_info['os_platform']}\n\n"
            
            if self.settings.get("hardware_optimized", False):
                info_text += "Hardware Optimization: Applied\n"
                applied = self.settings.get("optimization_applied", {})
                if applied:
                    info_text += f"Current Settings:\n"
                    info_text += f"• Device: {applied.get('device', 'N/A')}\n"
                    info_text += f"• Model: {applied.get('model', 'N/A')}\n"
                    info_text += f"• Compute Type: {applied.get('compute_type', 'N/A')}\n"
            else:
                info_text += "Hardware Optimization: Not applied\n"
            
            show_setup_information(self, "Hardware Information", info_text)
            
        except Exception as e:
            logging.error(f"Error showing hardware info: {e}")
            show_setup_critical(self, "Error", f"Could not retrieve hardware information: {e}")

    def show_software_information(self):
        """Display detected versions, interpreters, and module paths"""
        try:
            info_lines = []
            execution_env = get_execution_environment()
            mode = "Standalone Executable" if getattr(sys, 'frozen', False) else "Source (Python)"
            info_lines.append(f"Application Mode: {mode}")
            info_lines.append(f"Execution Environment: {execution_env}")
            info_lines.append(f"App Version: {APP_VERSION}")
            info_lines.append(f"App Directory: {format_path_for_display(get_app_directory())}")
            info_lines.append("")

            info_lines.append("Current Python Process:")
            info_lines.append(f"• Version: {platform.python_version()} ({platform.python_implementation()})")
            info_lines.append(f"• Executable: {format_path_for_display(get_executable_fallback_path())}")

            runtimes = enumerate_python_runtimes()
            info_lines.append("")
            info_lines.append("Detected Python Interpreters:")
            if runtimes:
                for runtime in runtimes:
                    pip_state = "pip available" if runtime.get("has_pip") else "pip missing"
                    commands = runtime.get("commands") or []
                    if commands:
                        cmd_summary = ", ".join(commands[:2])
                        if len(commands) > 2:
                            cmd_summary += " …"
                        cmd_summary = f" via {cmd_summary}"
                    else:
                        cmd_summary = ""
                    info_lines.append(
                        f"• Python {runtime.get('version', 'unknown')} ({pip_state}{cmd_summary})"
                    )
                    display_path = format_path_for_display(runtime.get("executable"))
                    if display_path:
                        info_lines.append(f"  Executable: {display_path}")
            else:
                info_lines.append("• None detected")

            plan = get_python_update_plan()
            info_lines.append("")
            info_lines.append("yt-dlp Update Planner:")
            info_lines.append(f"• Status: {plan.get('status')}")
            if plan.get("status_detail"):
                info_lines.append(f"• Detail: {plan['status_detail']}")
            python_info = plan.get("python_info")
            if python_info:
                info_lines.append(
                    f"• Selected Interpreter: {python_info.get('display_name')} (Python {python_info.get('version')})"
                )
            if plan.get("target_directory"):
                info_lines.append(f"• Target Directory: {format_path_for_display(plan['target_directory'])}")

            probe_log = get_python_probe_log()
            if plan.get("status") != "ready" and failures:
                info_lines.append("")
                info_lines.append("Python Probe Diagnostics (failed commands):")
                for entry in failures[:5]:
                    detail = entry.get("detail")
                    if detail and len(detail) > 200:
                        detail = detail[:200] + "…"
                    cmd_label = " ".join(entry.get("command", [])) or "(unknown)"
                    info_lines.append(
                        f"• {cmd_label}: {entry.get('status')}" + (f" ({detail})" if detail else "")
                    )

            ytdlp_info = get_ytdlp_installation_info()
            info_lines.append("")
            info_lines.append("yt-dlp Module:")
            info_lines.append(f"• Version: {ytdlp_info.get('version') or 'Not found'}")
            info_lines.append(f"• Location: {format_path_for_display(ytdlp_info.get('path')) or 'Unknown'}")
            info_lines.append(f"• Source: {ytdlp_info.get('installation_type')}")

            version_status = evaluate_yt_dlp_version_status(ytdlp_info.get('version'))
            latest = version_status.get("latest_version")
            if latest:
                info_lines.append(f"• Latest Release: {latest}")
            status_label = version_status.get("status")
            if status_label == "up_to_date":
                info_lines.append("• Upstream Status: Up to date")
            elif status_label == "behind" and version_status.get("releases_behind"):
                releases_behind = version_status["releases_behind"]
                plural = "s" if releases_behind != 1 else ""
                info_lines.append(
                    f"• Upstream Status: {releases_behind} release{plural} behind"
                )
            elif status_label == "unknown":
                info_lines.append("• Upstream Status: Unable to determine (version not in release list)")

            info_lines.append("")
            info_lines.append("Faster Whisper XXL GUI:")
            info_lines.append(f"• GUI Version: {APP_VERSION}")
            if self.executable_path and os.path.exists(self.executable_path):
                fw_version = detect_faster_whisper_binary_version(self.executable_path)
                info_lines.append(f"• Backend Binary: {format_path_for_display(self.executable_path)}")
                info_lines.append(f"• Backend Binary Version: {fw_version or 'Not reported'}")
            else:
                info_lines.append("• Backend Binary: Not initialized yet")

            info_text = "\n".join(info_lines)
            show_setup_information(self, "Software Information", info_text)
        except Exception as e:
            logging.error(f"Error showing software info: {e}")
            show_setup_warning(self, "Software Information", f"Could not gather software info:\n{e}")

    def diagnose_gpu_detection(self):
        """ Run comprehensive GPU detection diagnostics """
        try:
            hardware_info = detect_hardware_capabilities()
            diag_text = "GPU Detection Diagnostics\n\n"
            
            if hardware_info["has_cuda"]:
                diag_text += f"✅ SUCCESS: GPU detected via {hardware_info.get('detection_method', 'unknown')}\n\n"
                diag_text += f"GPU Name: {hardware_info['gpu_name']}\n"
                diag_text += f"VRAM: {hardware_info['gpu_memory_gb']:.1f}GB\n"
                diag_text += f"CUDA Version: {hardware_info['cuda_version']}\n\n"
                diag_text += "Your GPU is properly detected and CUDA acceleration is available."
            else:
                diag_text += "❌ FAILED: No CUDA-compatible GPU detected\n\n"
                if hardware_info.get("detection_details"):
                    diag_text += "Detection attempts:\n"
                    for detail in hardware_info["detection_details"]:
                        diag_text += f"• {detail}\n"
                    diag_text += "\n"
                
                diag_text += "Troubleshooting steps:\n"
                diag_text += "1. Install PyTorch with CUDA support:\n"
                diag_text += "   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121\n\n"
                diag_text += "2. Update NVIDIA drivers from nvidia.com\n\n"
                diag_text += "3. Verify NVIDIA GPU is detected by Windows:\n"
                diag_text += "   - Open Device Manager\n"
                diag_text += "   - Check 'Display adapters' section\n"
                diag_text += "   - Look for NVIDIA GPU (no yellow warning icons)\n\n"
                diag_text += "4. Test nvidia-smi command:\n"
                diag_text += "   - Open Command Prompt\n"
                diag_text += "   - Run: nvidia-smi\n"
                diag_text += "   - Should show GPU information\n\n"
                diag_text += "5. Restart computer after driver installation\n\n"
                diag_text += "CPU mode will be used until GPU is properly detected."
            
            show_setup_information(self, "GPU Detection Diagnostics", diag_text)
            
        except Exception as e:
            logging.error(f"Error in GPU diagnostics: {e}")
            show_setup_critical(self, "Diagnostic Error", f"Could not run GPU diagnostics: {e}")

    def setup_realtime_saving(self):
        """Connect UI elements to save settings in real-time"""
        self.model_combo.currentTextChanged.connect(self.save_combo_setting)
        self.task_combo.currentTextChanged.connect(self.save_combo_setting)
        self.language_combo.currentTextChanged.connect(self.save_combo_setting)
        self.compute_combo.currentTextChanged.connect(self.save_combo_setting)
        self.device_combo.currentTextChanged.connect(self.save_combo_setting)
        self.vad_method.currentTextChanged.connect(self.save_combo_setting)
        self.temperature.valueChanged.connect(self.save_spinbox_setting)
        self.beam_size.valueChanged.connect(self.save_spinbox_setting)
        self.best_of.valueChanged.connect(self.save_spinbox_setting)
        self.patience.valueChanged.connect(self.save_spinbox_setting)
        self.vad_threshold.valueChanged.connect(self.save_spinbox_setting)
        self.vad_min_speech.valueChanged.connect(self.save_spinbox_setting)
        self.output_dir.textChanged.connect(self.save_text_setting)
        self.initial_prompt.textChanged.connect(self.save_text_setting)
        self.tooltips_checkbox.toggled.connect(self.apply_tooltip_visibility)
        
        for checkbox in self.findChildren(QCheckBox):
            if checkbox.objectName():
                checkbox.toggled.connect(self.save_checkbox_setting)
        
        for fmt, checkbox in self.output_format_checkboxes.items():
            checkbox.toggled.connect(self.save_output_format_setting)
        
        self.main_splitter.splitterMoved.connect(self.save_splitter_setting)

    def save_combo_setting(self):
        """Save combo box settings immediately"""
        self.settings["model"] = self.model_combo.currentText()
        self.settings["task"] = self.task_combo.currentText()
        self.settings["language"] = self.get_language_code()
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

    def get_output_dir(self):
        dir_path = self.output_dir.text()
        if not dir_path:
            dir_path = os.path.join(get_app_directory(), "output")
        os.makedirs(dir_path, exist_ok=True)
        return dir_path

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
        return bool(re.match(r"^[A-Za-z]:\\\\", path))

    def _windows_to_posix_path(self, path):
        drive = path[0].lower()
        rest = path[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"

    def get_model_dirs(self):
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
        model_name = self.model_combo.currentText()
        cli_model_dir, local_model_dir = self.get_model_dirs()
        if not local_model_dir:
            return True
        if getattr(self, "_model_download_cancelled", False):
            logging.info("ensure_model_available: skip dialog (recent cancel)")
            return False
        model_folder = f"faster-whisper-{model_name}"
        target_dir = os.path.join(local_model_dir, model_folder)
        model_bin = os.path.join(target_dir, "model.bin")
        if os.path.exists(model_bin) and os.path.getsize(model_bin) > 0:
            logging.info("ensure_model_available: model present %s", model_bin)
            return True
        if not hasattr(self, "_model_dialog_count"):
            self._model_dialog_count = 0
        self._model_dialog_count += 1
        logging.info(
            "ensure_model_available: open dialog #%s model=%s target=%s",
            self._model_dialog_count,
            model_name,
            target_dir,
        )
        dialog = ModelDownloadDialog(model_name, target_dir, parent=self)
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
        
        cmd = [self.executable_path, input_file]
        model_dir_cli, _ = self.get_model_dirs()
        options = {
            "-m": self.model_combo.currentText(), "--task": self.task_combo.currentText(),
            "-l": self.get_language_code() if self.get_language_code() != 'auto' else None,
            "--compute_type": self.compute_combo.currentText(), "--device": self.device_combo.currentText(),
            "--temperature": str(self.temperature.value()) if self.temperature.value() > 0 else None,
            "--beam_size": str(self.beam_size.value()) if self.beam_size.value() != 5 else None,
            "--best_of": str(self.best_of.value()) if self.best_of.value() != 5 else None,
            "--patience": str(self.patience.value()) if self.patience.value() != 1.0 else None,
            "--initial_prompt": self.initial_prompt.toPlainText() if self.initial_prompt.toPlainText() else None,
            "--model_dir": model_dir_cli,
            "--output_dir": self.get_output_dir(),
            "--vad_method": self.vad_method.currentText() if self.vad_filter.isChecked() else None,
            "--vad_threshold": str(self.vad_threshold.value()) if self.vad_filter.isChecked() else None,
            "--vad_min_speech_duration_ms": str(self.vad_min_speech.value()) if self.vad_filter.isChecked() else None,
        }
        for option, value in options.items():
            if value is not None:
                cmd.extend([option, value])

        checkboxes = {
            "--ff_mp3": self.ff_mp3,
        }
        for option, checkbox in checkboxes.items():
            if checkbox.isChecked():
                cmd.append(option)

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
        return cmd

    def start_processing(self):
        self.stop_requested = False
        self.output_buffer = ""
        self.last_line_was_overwrite = False
        self.transcription_completed_successfully = False
        self._model_download_cancelled = False

        active_tab = self.tabs.currentWidget()
        if active_tab == self.file_tab:
            input_files = self.get_input_files()
            if not input_files:
                QMessageBox.warning(self, "Warning", "Please add one or more input files in the 'File' tab.")
                return
            self.pending_files = list(input_files)
            self.batch_total = len(self.pending_files)
            self.batch_index = 0
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
            self._append_text_to_console(
                f"\nPROCESSING FILE {self.batch_index}/{self.batch_total} • {next_file}\n\n"
            )
            if self.run_transcription(next_file):
                started = True
                break
        if not started and not self.stop_requested:
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.stop_requested = False

    def run_transcription(self, input_file, clear_output=False):
        if not input_file:
            QMessageBox.warning(self, "Warning", "Please select an input file in the 'File' tab.")
            return False

        self.current_input_file = input_file
        self._single_output_line_emitted = False
        logging.info("run_transcription: input=%s", input_file)

        if not self.ensure_model_available():
            logging.info("run_transcription: model download cancelled or failed")
            return False

        command = self.build_command(input_file)
        if not command:
            return False
        self._log_debug_parameters("transcribe")

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
        
        logging.info(f"Starting QProcess with command: {command}")
        if self.full_console_checkbox.isChecked():
            self._append_text_to_console(f"Running command:\n{display_command}\n" + "="*50 + "\n")

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.on_finished)
        self.process.errorOccurred.connect(self.on_process_error)

        self.process.start(command[0], command[1:])
        return True

    def stop_processing(self):
        self.stop_requested = True
        self.pending_files = []
        self.batch_total = 0
        self.batch_index = 0
        if self.downloader and self.downloader.isRunning():
            self._append_text_to_console("\nRequesting download cancellation...\n")
            self.downloader.stop()
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self._append_text_to_console("\nTerminating process...\n")
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self._append_text_to_console("Process did not terminate gracefully, killing it.\n")
                self.process.kill()


    def _filter_verbose_output(self, data):
        lines = data.split('\n')
        filtered_lines = []

        for line in lines:
            if re.match(r'^\[\d+:\d+\.\d+ --> \d+:\d+\.\d+\]', line):
                filtered_lines.append(line)
            elif 'Subtitles are written to' in line and self.full_console_checkbox.isChecked():
                filtered_lines.append(line)

        return '\n'.join(filtered_lines) if filtered_lines else ''

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
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
        if self.full_console_checkbox.isChecked():
            self._append_text_to_console(data)
        else:
            filtered = self._filter_verbose_output(data)
            if filtered:
                self._append_text_to_console(filtered + "\n")

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
                cursor.insertText('\n')
                self.last_line_was_overwrite = False
            else:
                self.last_line_was_overwrite = True
        
        self.output_text.ensureCursorVisible()


    def create_sentences_only_file(self, txt_with_timestamps, sentences_only_path):
        """Helper method to create sentences-only file from timestamped txt"""
        try:
            if not os.path.exists(txt_with_timestamps):
                self._append_text_to_console(f"Warning: Timestamped txt file not found at {txt_with_timestamps}\n")
                return False
            
            with open(txt_with_timestamps, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if not content.strip():
                self._append_text_to_console(f"Warning: Timestamped txt file is empty\n")
                return False
            
            pattern = r'\[[\d:.\->\s]+\]\s*(.*)'
            matches = re.findall(pattern, content)
            
            if not matches:
                self._append_text_to_console(f"Warning: No timestamped content found in txt file\n")
                return False
            
            sentences_text = ' '.join(match.strip() for match in matches if match.strip())
            
            if not sentences_text:
                self._append_text_to_console(f"Warning: No valid sentences extracted from txt file\n")
                return False
            
            with open(sentences_only_path, 'w', encoding='utf-8') as f:
                f.write(sentences_text)
            
            return True
            
        except Exception as e:
            self._append_text_to_console(f"Error creating sentences-only txt file: {str(e)}\n")
            return False

    def _format_lrc_timestamp(self, seconds):
        try:
            total_centis = int(round(max(0.0, float(seconds)) * 100))
        except (TypeError, ValueError):
            total_centis = 0
        minutes = total_centis // 6000
        secs = (total_centis // 100) % 60
        centis = total_centis % 100
        return f"{minutes:02d}:{secs:02d}.{centis:02d}"

    def create_enhanced_lrc_from_json(self, input_file_path):
        """Generate enhanced LRC with word timestamps from JSON output."""
        try:
            output_dir = self.get_output_dir()
            filename_only = os.path.splitext(os.path.basename(input_file_path))[0]
            json_path = os.path.join(output_dir, filename_only + '.json')
            lrc_path = os.path.join(output_dir, filename_only + '.lrc')

            if not os.path.exists(json_path):
                self._append_text_to_console(f"Warning: JSON file not found at {json_path}\n")
                return False

            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            segments = []
            if isinstance(data, dict):
                segments = data.get("segments") or []
            elif isinstance(data, list):
                segments = data

            if not segments:
                self._append_text_to_console("Warning: No segments found in JSON file\n")
                return False

            lines = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                start = segment.get("start", 0)
                words = segment.get("words") or []
                if words:
                    word_chunks = []
                    for word_entry in words:
                        if not isinstance(word_entry, dict):
                            continue
                        word_text = str(word_entry.get("word", "")).strip()
                        if not word_text:
                            continue
                        word_start = word_entry.get("start", start)
                        word_ts = self._format_lrc_timestamp(word_start)
                        word_chunks.append(f"<{word_ts}>{word_text}")
                    if not word_chunks:
                        text = str(segment.get("text", "")).strip()
                        if not text:
                            continue
                        lines.append(f"[{self._format_lrc_timestamp(start)}]{text}")
                    else:
                        line = f"[{self._format_lrc_timestamp(start)}]" + " ".join(word_chunks)
                        lines.append(line)
                else:
                    text = str(segment.get("text", "")).strip()
                    if not text:
                        continue
                    lines.append(f"[{self._format_lrc_timestamp(start)}]{text}")

            if not lines:
                self._append_text_to_console("Warning: No valid LRC lines created from JSON\n")
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
            filename_only = os.path.splitext(os.path.basename(input_file_path))[0]
            txt_with_timestamps = os.path.join(output_dir, filename_only + '.txt')
            sentences_only_path = os.path.join(output_dir, filename_only + '_sentences.txt')
            
            wants_timestamps = getattr(self, 'txt_with_timestamps_requested', False)
            wants_sentences = getattr(self, 'sentences_only_requested', False)
            
            if wants_sentences:
                success = self.create_sentences_only_file(txt_with_timestamps, sentences_only_path)
                if success:
                    formats = getattr(self, "last_output_formats", [])
                    if len(formats) <= 1:
                        self._append_text_to_console(f"\nOutput saved to: {sentences_only_path}\n")
                        self._single_output_line_emitted = True
            
            if wants_sentences and not wants_timestamps:
                if os.path.exists(txt_with_timestamps):
                    os.remove(txt_with_timestamps)
            
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
        elif exit_code == 0 or self.transcription_completed_successfully:
            if self.full_console_checkbox.isChecked():
                self._append_text_to_console("Process completed successfully.\n")
            
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
            status_str = "Crashed" if exit_status == QProcess.ExitStatus.CrashExit else "Failed"
            self._append_text_to_console(f"Process {status_str} with exit code {exit_code}.\n")

        if self.pending_files and not self.stop_requested:
            self.process = None
            self.transcription_completed_successfully = False
            self.output_buffer = ""
            self.last_line_was_overwrite = False
            self.start_next_file()
            return

        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.process = None
        self.downloader = None
        self.stop_requested = False
        
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
        url = self.youtube_url.text()
        if not url:
            QMessageBox.warning(self, "Warning", "Please enter a YouTube URL!")
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
        if "Downloading:" in text:
            self._append_text_to_console(text + "\r")
        else:
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
        self.settings["model"] = self.model_combo.currentText()
        self.settings["task"] = self.task_combo.currentText()
        self.settings["language"] = self.get_language_code()
        self.settings["compute_type"] = self.compute_combo.currentText()
        self.settings["device"] = self.device_combo.currentText()
        self.settings["temperature"] = self.temperature.value()
        self.settings["beam_size"] = self.beam_size.value()
        self.settings["best_of"] = self.best_of.value()
        self.settings["patience"] = self.patience.value()
        self.settings["initial_prompt"] = self.initial_prompt.toPlainText()
        
        self.settings["vad_method"] = self.vad_method.currentText()
        self.settings["vad_threshold"] = self.vad_threshold.value()
        self.settings["vad_min_speech"] = self.vad_min_speech.value()
        
        checkbox_settings = {cb.objectName(): cb.isChecked() for cb in self.findChildren(QCheckBox) if cb.objectName()}
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

        self.output_dir.setText(self.settings.get("output_dir", ""))
        self.model_combo.setCurrentText(self.settings.get("model", "large-v3"))
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
        
        self.vad_method.setCurrentText(self.settings.get("vad_method", "silero_v4_fw"))
        self.vad_threshold.setValue(self.settings.get("vad_threshold", 0.5))
        self.vad_min_speech.setValue(self.settings.get("vad_min_speech", 250))

        all_checkboxes = {cb.objectName(): cb for cb in self.findChildren(QCheckBox) if cb.objectName()}
        checkbox_settings = self.settings.get("checkboxes", {})
        for name, checked in checkbox_settings.items():
            if name in all_checkboxes:
                all_checkboxes[name].setChecked(checked)
        self.apply_tooltip_visibility(self.tooltips_checkbox.isChecked())
        
        output_formats = self.settings.get("output_formats", ["srt"])
        for fmt, cb in self.output_format_checkboxes.items():
            cb.setChecked(fmt in output_formats)
