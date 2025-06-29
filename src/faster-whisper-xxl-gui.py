import sys
import os
import json
import platform
import shutil
import requests
import py7zr
from pathlib import Path
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
import yt_dlp

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
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
            self.progress.emit(f"Downloading: {d['_percent_str']} of {d.get('_total_bytes_str', 'N/A')} at {d.get('_speed_str', 'N/A')}")
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
                }
            else:
                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': output_template,
                    'noplaylist': True,
                    'progress_hooks': [self.progress_hook],
                }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(self.url, download=True)
                final_filename = ydl.prepare_filename(info_dict)
                if self.audio_only:
                    base, _ = os.path.splitext(final_filename)
                    final_filename = base + '.mp3'
                self.finished.emit(final_filename)
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self.stop_requested = True

class WhisperGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.process = None
        self.downloader = None
        self.stop_requested = False
        self.output_format_checkboxes = {}
        self.settings_file = "settings.json"
        self.settings = {}
        
        self.executable_path = None
        self.executable_name = None
        
        # This new method is called on startup to ensure dependencies are met.
        if not self.check_and_setup_dependencies():
            # If setup fails or is cancelled, we close the app.
            # Using QTimer to allow the constructor to finish before closing.
            QTimer.singleShot(0, self.close)
            return

        self.init_ui()
        self.load_settings()

    def check_and_setup_dependencies(self):
        """
        Checks for faster-whisper-xxl and ffmpeg, and downloads them if missing.
        Returns True on success, False on failure/cancellation.
        """
        if sys.platform == "win32":
            self.executable_name = "faster-whisper-xxl.exe"
            url = "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z"
            files_to_extract = [self.executable_name, "ffmpeg.exe"]
        elif sys.platform == "linux" or sys.platform == "darwin":
            self.executable_name = "faster-whisper-xxl"
            url = "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_linux.7z"
            files_to_extract = [self.executable_name, "ffmpeg"]
        else:
            QMessageBox.critical(self, "Unsupported OS", f"Your operating system '{sys.platform}' is not supported for automatic download.")
            return False

        # 1. Check if executable exists in the current directory
        if os.path.exists(self.executable_name):
            self.executable_path = os.path.abspath(self.executable_name)
            print(f"Found local executable: {self.executable_path}")
            return True

        # 2. Check if executable exists in system PATH
        path_in_system = shutil.which(self.executable_name)
        if path_in_system:
            self.executable_path = path_in_system
            print(f"Found executable in PATH: {self.executable_path}")
            return True

        # 3. If not found, prompt the user to download
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setText("Required files not found.")
        msg_box.setInformativeText(f"The core executable '{self.executable_name}' was not found.\n\n"
                                   "Would you like to download and extract it automatically? (Approx. 500-600 MB)\n\n"
                                   "This is a one-time setup.")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Yes)
        
        if msg_box.exec() == QMessageBox.StandardButton.No:
            return False

        # 4. Perform download and extraction
        archive_name = "whisper_essentials.7z"
        try:
            # --- Download with progress ---
            response = requests.get(url, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            progress = QProgressDialog("Downloading required files...", "Cancel", 0, total_size, self)
            progress.setWindowTitle("Setup")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.show()

            downloaded_size = 0
            with open(archive_name, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if progress.wasCanceled():
                        raise Exception("Download cancelled by user.")
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    progress.setValue(downloaded_size)
                    QApplication.processEvents()

            progress.setLabelText("Extracting files...")
            QApplication.processEvents()
            
            # --- Extract ---
            with py7zr.SevenZipFile(archive_name, mode='r') as z:
                z.extract(targets=files_to_extract, path=".")
            
            progress.setValue(total_size) # Mark as complete
            
            # --- Cleanup and Finalize ---
            os.remove(archive_name)

            if not os.path.exists(self.executable_name):
                 raise FileNotFoundError(f"Extraction failed. '{self.executable_name}' not found after extraction.")

            self.executable_path = os.path.abspath(self.executable_name)

            # Set execute permissions on Linux/macOS
            if sys.platform != "win32":
                os.chmod(self.executable_path, 0o755)

            QMessageBox.information(self, "Setup Complete", "Required files have been successfully downloaded and set up.")
            return True

        except Exception as e:
            QMessageBox.critical(self, "Setup Failed", f"An error occurred during setup:\n\n{e}\n\nThe application will now close.")
            if os.path.exists(archive_name):
                os.remove(archive_name) # Clean up partial download
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

        # --- Left Panel (Controls) ---
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

        # --- Right Panel (Output) ---
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
        self.audio_only_checkbox = QCheckBox("Audio-only")
        self.audio_only_checkbox.setToolTip("If checked, only downloads the audio. Uncheck to download the full video.")
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
        formats = ['json', 'vtt', 'srt', 'lrc', 'txt', 'text', 'tsv', 'all']
        num_rows = 4
        for i, fmt in enumerate(formats):
            checkbox = QCheckBox(fmt)
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
        scroll_layout.addRow("Temperature:", self.temperature)
        self.beam_size = QSpinBox()
        self.beam_size.setRange(1, 10)
        scroll_layout.addRow("Beam Size:", self.beam_size)
        self.best_of = QSpinBox()
        self.best_of.setRange(1, 10)
        scroll_layout.addRow("Best Of:", self.best_of)
        self.patience = QDoubleSpinBox()
        self.patience.setRange(0.0, 10.0)
        self.patience.setSingleStep(0.1)
        scroll_layout.addRow("Patience:", self.patience)
        self.initial_prompt = QTextEdit()
        self.initial_prompt.setMaximumHeight(80)
        scroll_layout.addRow("Initial Prompt:", self.initial_prompt)
        self.word_timestamps = QCheckBox("Word Timestamps")
        scroll_layout.addRow(self.word_timestamps)
        self.without_timestamps = QCheckBox("Without Timestamps")
        scroll_layout.addRow(self.without_timestamps)
        self.verbose = QCheckBox("Verbose")
        scroll_layout.addRow(self.verbose)
        self.print_progress = QCheckBox("Print Progress")
        scroll_layout.addRow(self.print_progress)
        self.highlight_words = QCheckBox("Highlight Words")
        scroll_layout.addRow(self.highlight_words)
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        layout = QVBoxLayout(tab)
        layout.addWidget(scroll)

    def setup_vad_tab(self, tab):
        layout = QFormLayout(tab)
        self.vad_filter = QCheckBox("Enable VAD Filter")
        layout.addRow(self.vad_filter)
        self.vad_method = QComboBox()
        self.vad_method.addItems(['silero_v4_fw', 'silero_v5_fw', 'silero_v3', 'silero_v4', 'silero_v5', 'pyannote_v3', 'pyannote_onnx_v3', 'auditok', 'webrtc'])
        layout.addRow("VAD Method:", self.vad_method)
        self.vad_threshold = QDoubleSpinBox()
        self.vad_threshold.setRange(0.0, 1.0)
        self.vad_threshold.setSingleStep(0.01)
        layout.addRow("VAD Threshold:", self.vad_threshold)
        self.vad_min_speech = QSpinBox()
        self.vad_min_speech.setRange(0, 10000)
        self.vad_min_speech.setSuffix(" ms")
        layout.addRow("Min Speech Duration:", self.vad_min_speech)

    def setup_audio_tab(self, tab):
        layout = QFormLayout(tab)
        self.ff_mp3 = QCheckBox("Convert to MP3")
        layout.addRow(self.ff_mp3)
        self.ff_loudnorm = QCheckBox("Loudness Normalization")
        layout.addRow(self.ff_loudnorm)
        self.ff_speechnorm = QCheckBox("Speech Normalization")
        layout.addRow(self.ff_speechnorm)
        self.ff_tempo = QDoubleSpinBox()
        self.ff_tempo.setRange(0.5, 2.0)
        self.ff_tempo.setSingleStep(0.1)
        self.ff_tempo.setEnabled(False)
        self.tempo_checkbox = QCheckBox("Adjust Tempo")
        self.tempo_checkbox.toggled.connect(self.ff_tempo.setEnabled)
        tempo_layout = QHBoxLayout()
        tempo_layout.addWidget(self.tempo_checkbox)
        tempo_layout.addWidget(self.ff_tempo)
        layout.addRow("Tempo:", tempo_layout)

    def apply_theme(self, theme_name):
        theme = theme_name.lower()
        self.settings["theme"] = theme
        qss_file = resource_path(f"{theme}_theme.qss")
        try:
            with open(qss_file, "r") as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError:
            print(f"Theme file '{qss_file}' not found. Using default theme.")

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
            dir_path = os.path.join(os.getcwd(), "output")
        os.makedirs(dir_path, exist_ok=True)
        return dir_path

    def build_command(self, input_file):
        if not input_file or not os.path.exists(input_file):
            QMessageBox.warning(self, "Warning", f"Input file not found: {input_file}")
            return None
        
        # Use the executable path determined at startup
        cmd = [self.executable_path, input_file]
        
        options = {
            "-m": self.model_combo.currentText(),
            "--task": self.task_combo.currentText(),
            "-l": self.language_combo.currentText() if self.language_combo.currentText() != 'auto' else None,
            "--compute_type": self.compute_combo.currentText(),
            "--device": self.device_combo.currentText(),
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
            "--vad_filter": self.vad_filter, "--ff_mp3": self.ff_mp3, "--ff_loudnorm": self.ff_loudnorm,
            "--ff_speechnorm": self.ff_speechnorm,
        }
        for option, checkbox in checkboxes.items():
            if checkbox.isChecked():
                cmd.append(option)
        selected_formats = [fmt for fmt, cb in self.output_format_checkboxes.items() if fmt != 'all' and cb.isChecked()]
        if self.output_format_checkboxes['all'].isChecked():
            selected_formats = ['all']
        if selected_formats:
            cmd.extend(["--output_format"] + selected_formats)
        return cmd

    def start_processing(self):
        self.stop_requested = False
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
                processed_arg = arg.replace('"', '\"').replace("'", "\'")
                quoted_command_parts.append(f'"{processed_arg}"')
            else:
                quoted_command_parts.append(arg)
        display_command = ' '.join(quoted_command_parts)
        
        self.output_text.append(f"<b>Running command:</b> {display_command}\n")
        self.output_text.append("-" * 80 + "\n")
        
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.on_finished)
        self.process.start(command[0], command[1:])

    def stop_processing(self):
        self.stop_requested = True
        if self.downloader and self.downloader.isRunning():
            self.downloader.stop()
            self.append_error("\nDownload cancelled by user.")
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.process.terminate()

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode(errors='ignore')
        self.append_output(data)

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode(errors='ignore')
        self.append_error(data)

    def append_output(self, text):
        if not text: return
        self.output_text.moveCursor(QTextCursor.MoveOperation.End)
        self.output_text.insertPlainText(text)
        self.output_text.verticalScrollBar().setValue(self.output_text.verticalScrollBar().maximum())

    def append_error(self, text):
        if not text: return
        self.output_text.moveCursor(QTextCursor.MoveOperation.End)
        self.output_text.insertHtml(f"<span style='color: #ff6b6b;'>{text.replace('\n', '<br>')}</span>")
        self.output_text.verticalScrollBar().setValue(self.output_text.verticalScrollBar().maximum())

    def on_finished(self, exit_code, exit_status):
        if self.stop_requested:
            self.append_error("\nProcess stopped by user.")
        else:
            self.output_text.append("\n<b>Process completed.</b>")
        
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.process = None
        self.downloader = None
        self.stop_requested = False

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
        self.downloader.progress.connect(self.append_output)
        
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.output_text.clear()
        self.output_text.append(f"<b>Starting download from:</b> {url}")
        self.downloader.start()

    def on_download_finished(self, file_path):
        self.output_text.append(f"<b>Download finished:</b> {file_path}")
        self.run_transcription(input_file=file_path)

    def on_download_error(self, error_message):
        if "Download cancelled by user" not in error_message:
            self.append_error(f"YouTube Download Error: {error_message}")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.downloader = None

    def closeEvent(self, event):
        # Prevent saving settings if UI was never initialized (e.g., setup was cancelled)
        if hasattr(self, 'main_splitter'):
            self.save_settings()
        super().closeEvent(event)

    def save_settings(self):
        self.settings["geometry"] = self.saveGeometry().data().hex()
        self.settings["splitter_sizes"] = self.main_splitter.sizes()
        self.settings["output_dir"] = self.output_dir.text()
        self.settings["audio_only"] = self.audio_only_checkbox.isChecked()
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
        checkbox_settings = {
            "word_timestamps": self.word_timestamps.isChecked(), "without_timestamps": self.without_timestamps.isChecked(),
            "verbose": self.verbose.isChecked(), "print_progress": self.print_progress.isChecked(),
            "highlight_words": self.highlight_words.isChecked(), "vad_filter": self.vad_filter.isChecked(),
            "ff_mp3": self.ff_mp3.isChecked(), "ff_loudnorm": self.ff_loudnorm.isChecked(),
            "ff_speechnorm": self.ff_speechnorm.isChecked(), "tempo_checkbox": self.tempo_checkbox.isChecked(),
        }
        self.settings["checkboxes"] = checkbox_settings
        self.settings["output_formats"] = [fmt for fmt, cb in self.output_format_checkboxes.items() if cb.isChecked()]
        with open(self.settings_file, "w") as f:
            json.dump(self.settings, f, indent=4)

    def load_settings(self):
        try:
            with open(self.settings_file, "r") as f:
                self.settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.settings = {}

        theme = self.settings.get("theme", "dark")
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(theme.upper() if theme == "amoled" else theme.capitalize())
        self.theme_combo.blockSignals(False)
        self.apply_theme(theme)

        geometry = self.settings.get("geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromHex(bytes(geometry, 'utf-8')))
        
        splitter_sizes = self.settings.get("splitter_sizes")
        if splitter_sizes:
            self.main_splitter.setSizes(splitter_sizes)

        self.output_dir.setText(self.settings.get("output_dir", ""))
        self.audio_only_checkbox.setChecked(self.settings.get("audio_only", True))
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
        
        checkbox_settings = self.settings.get("checkboxes", {})
        self.word_timestamps.setChecked(checkbox_settings.get("word_timestamps", False))
        self.without_timestamps.setChecked(checkbox_settings.get("without_timestamps", False))
        self.verbose.setChecked(checkbox_settings.get("verbose", False))
        self.print_progress.setChecked(checkbox_settings.get("print_progress", False))
        self.highlight_words.setChecked(checkbox_settings.get("highlight_words", False))
        self.vad_filter.setChecked(checkbox_settings.get("vad_filter", False))
        self.ff_mp3.setChecked(checkbox_settings.get("ff_mp3", False))
        self.ff_loudnorm.setChecked(checkbox_settings.get("ff_loudnorm", False))
        self.ff_speechnorm.setChecked(checkbox_settings.get("ff_speechnorm", False))
        self.tempo_checkbox.setChecked(checkbox_settings.get("tempo_checkbox", False))
        
        output_formats = self.settings.get("output_formats", [])
        for fmt, cb in self.output_format_checkboxes.items():
            cb.setChecked(fmt in output_formats)

def main():
    app = QApplication(sys.argv)
    # The application window is only created if the dependency check succeeds
    window = WhisperGUI()
    # If the setup was cancelled, window.executable_path would be None and the
    # QTimer would have scheduled a close(). We only show if it's a valid window.
    if window.executable_path:
        window.show()
        sys.exit(app.exec())
    else:
        # Exit gracefully if setup was cancelled
        sys.exit(0)


if __name__ == '__main__':
    main()