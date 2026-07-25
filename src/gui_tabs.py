"""Tab setup methods for WhisperGUI, extracted as a mixin class.

This module contains all setup_*_tab methods plus create_output_console,
create_button_layout, and handle_all_formats_toggle.
"""

import sys
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QCheckBox, QTextEdit, QLineEdit,
    QDoubleSpinBox, QSpinBox, QGroupBox, QScrollArea, QListWidget,
    QAbstractItemView, QSizePolicy, QCompleter, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from utils import executable_word
from gui_components import (
    FileDropGroupBox, FileDropListWidget,
    LinkDropGroupBox, LinkDropListWidget,
)


class TabSetupMixin:
    """Mixin providing tab-setup methods for WhisperGUI."""

    def setup_file_tab(self, tab):
        layout = QFormLayout(tab)
        input_group = FileDropGroupBox("Input", add_files_callback=self.add_input_files)
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(8, 6, 8, 6)
        input_layout.setSpacing(4)

        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_label = QLabel("Drag & drop files or folders here.")
        hint_label.setStyleSheet("color: #a0a0a0;")
        self.file_count_label = QLabel("0 files")
        self.file_count_label.setStyleSheet("color: #a0a0a0; font-weight: bold;")
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
        self.file_list.setMinimumHeight(92)
        self.file_list.setMaximumHeight(104)
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
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        link_group = LinkDropGroupBox("Links", add_links_callback=self.add_input_links)
        link_layout = QVBoxLayout(link_group)
        link_layout.setContentsMargins(8, 6, 8, 4)
        link_layout.setSpacing(2)
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_label = QLabel("Paste one link per line or drop URLs.")
        hint_label.setStyleSheet("color: gray;")
        self.link_count_label = QLabel("0 links")
        hint_row.addWidget(hint_label)
        hint_row.addStretch()
        hint_row.addWidget(self.link_count_label)
        link_layout.addLayout(hint_row)

        self.link_list = LinkDropListWidget(add_links_callback=self.add_input_links)
        self.link_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.link_list.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.link_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.link_list.setAcceptDrops(True)
        self.link_list.viewport().setAcceptDrops(True)
        self.link_list.setMinimumHeight(92)
        self.link_list.setMaximumHeight(104)
        self.link_list.setFrameShape(QListWidget.Shape.NoFrame)
        link_layout.addWidget(self.link_list)

        link_button_layout = QHBoxLayout()
        self.add_link_btn = QPushButton("Add Links")
        self.add_link_btn.clicked.connect(self.prompt_add_links)
        self.paste_links_btn = QPushButton("Paste Links")
        self.paste_links_btn.clicked.connect(self.paste_links)
        self.remove_link_btn = QPushButton("Remove Selected")
        self.remove_link_btn.clicked.connect(self.remove_selected_links)
        self.clear_links_btn = QPushButton("Clear All")
        self.clear_links_btn.clicked.connect(self.clear_links)
        link_button_layout.addWidget(self.add_link_btn)
        link_button_layout.addWidget(self.paste_links_btn)
        link_button_layout.addWidget(self.remove_link_btn)
        link_button_layout.addWidget(self.clear_links_btn)
        link_layout.addLayout(link_button_layout)
        link_group.setFixedHeight(190)
        layout.addWidget(link_group)

        options_widget = QWidget()
        options_layout = QVBoxLayout(options_widget)
        options_layout.setContentsMargins(0, 6, 0, 0)
        options_layout.setSpacing(2)

        self.audio_only_checkbox = QCheckBox("Audio Only (Download faster)")
        self.audio_only_checkbox.setChecked(True)
        self.audio_only_checkbox.setObjectName("audio_only_checkbox")
        self.audio_only_checkbox.setToolTip("Downloads audio only for faster processing.")
        options_layout.addWidget(self.audio_only_checkbox)
        self.download_all_checkbox = QCheckBox("Download all before transcribing")
        self.download_all_checkbox.setChecked(False)
        self.download_all_checkbox.setToolTip(
            "When enabled, downloads all items first, then transcribes. "
            "When disabled, items are transcribed as soon as they finish downloading."
        )
        options_layout.addWidget(self.download_all_checkbox)
        options_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(options_widget)

    def setup_overrides_tab(self, tab):
        scroll = QScrollArea()
        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        def _compact_path_button(text, tooltip=""):
            button = QPushButton(text)
            if tooltip:
                button.setToolTip(tooltip)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setMinimumHeight(28)
            button.setMaximumHeight(32)
            metrics = QFontMetrics(button.font())
            width = metrics.horizontalAdvance(text) + 22
            width = max(56, min(width, 100))
            button.setMinimumWidth(width)
            button.setMaximumWidth(width)
            button.setStyleSheet("QPushButton { padding: 4px 8px; }")
            return button

        core_group = QGroupBox("Core Paths")
        core_layout = QFormLayout(core_group)
        core_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        core_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.fw_exe_path = QLineEdit()
        self.fw_exe_path.setPlaceholderText("Optional override for faster-whisper-xxl executable")
        fw_browse = _compact_path_button("Browse", "Browse for executable")
        fw_clear = _compact_path_button("Clear", "Clear override")
        fw_test = _compact_path_button("Test", "Validate executable")
        fw_find = _compact_path_button("Find", "Find from system PATH")
        fw_row = QHBoxLayout()
        fw_row.setSpacing(4)
        fw_row.addWidget(self.fw_exe_path, 1)
        fw_row.addWidget(fw_browse)
        fw_row.addWidget(fw_clear)
        fw_row.addWidget(fw_test)
        fw_row.addWidget(fw_find)
        core_layout.addRow(f"Whisper XXL {executable_word()}:", fw_row)

        self.model_dir_path = QLineEdit()
        self.model_dir_path.setPlaceholderText("Optional override for model directory")
        self.model_dir_path.setToolTip(
            "CT2 models are supported (requires model.bin). The test view can also flag "
            "Transformers models for troubleshooting."
        )
        model_browse = _compact_path_button("Browse", "Browse for model directory")
        model_clear = _compact_path_button("Clear", "Clear override")
        model_test = _compact_path_button("Test", "Validate model directory")
        model_row = QHBoxLayout()
        model_row.setSpacing(4)
        model_row.addWidget(self.model_dir_path, 1)
        model_row.addWidget(model_browse)
        model_row.addWidget(model_clear)
        model_row.addWidget(model_test)
        model_dir_label = QLabel("Model Directory:")
        model_dir_label.setToolTip(
            "CT2 models are supported (requires model.bin). The test view can also flag "
            "Transformers models for troubleshooting."
        )
        core_layout.addRow(model_dir_label, model_row)

        layout.addWidget(core_group)

        ytdlp_group = QGroupBox("yt-dlp")
        ytdlp_layout = QFormLayout(ytdlp_group)
        ytdlp_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        ytdlp_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        bundled_label = (
            "Uses Python module (current env)."
            if not getattr(sys, "frozen", False)
            else "Uses bundled Python module."
        )
        ytdlp_hint = QLabel(bundled_label)
        ytdlp_hint.setStyleSheet("color: #a0a0a0;")
        ytdlp_hint.setWordWrap(True)
        ytdlp_layout.addRow(ytdlp_hint)
        self.ytdlp_source_combo = QComboBox()
        self.ytdlp_source_combo.addItem("Python module (current env/bundled)", "bundled")
        self.ytdlp_source_combo.addItem(f"{executable_word()} (custom or PATH)", "path")
        self.ytdlp_source_combo.setToolTip(
            f"Choose whether downloads use the Python module or a yt-dlp {executable_word(False)}."
        )
        ytdlp_layout.addRow("Source:", self.ytdlp_source_combo)

        self.ytdlp_exe_path = QLineEdit()
        self.ytdlp_exe_path.setPlaceholderText("Optional override for yt-dlp.exe (manual use)")
        ytdlp_browse = _compact_path_button("Browse", "Browse for yt-dlp executable")
        ytdlp_clear = _compact_path_button("Clear", "Clear override")
        ytdlp_test = _compact_path_button("Test", "Validate yt-dlp executable")
        ytdlp_find = _compact_path_button("Find", "Find from system PATH")
        ytdlp_row = QHBoxLayout()
        ytdlp_row.setSpacing(4)
        ytdlp_row.addWidget(self.ytdlp_exe_path, 1)
        ytdlp_row.addWidget(ytdlp_browse)
        ytdlp_row.addWidget(ytdlp_clear)
        ytdlp_row.addWidget(ytdlp_test)
        ytdlp_row.addWidget(ytdlp_find)
        ytdlp_layout.addRow(f"yt-dlp {executable_word()}:", ytdlp_row)

        layout.addWidget(ytdlp_group)

        ffmpeg_group = QGroupBox("ffmpeg")
        ffmpeg_layout = QFormLayout(ffmpeg_group)
        ffmpeg_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        ffmpeg_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.ffmpeg_path_input = QLineEdit()
        self.ffmpeg_path_input.setPlaceholderText("Optional override for ffmpeg executable")
        ffmpeg_browse = _compact_path_button("Browse", "Browse for ffmpeg executable")
        ffmpeg_clear = _compact_path_button("Clear", "Clear override")
        ffmpeg_test = _compact_path_button("Test", "Validate ffmpeg executable")
        ffmpeg_find = _compact_path_button("Find", "Find from system PATH")
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.setSpacing(4)
        ffmpeg_row.addWidget(self.ffmpeg_path_input, 1)
        ffmpeg_row.addWidget(ffmpeg_browse)
        ffmpeg_row.addWidget(ffmpeg_clear)
        ffmpeg_row.addWidget(ffmpeg_test)
        ffmpeg_row.addWidget(ffmpeg_find)
        ffmpeg_layout.addRow(f"FFMPEG {executable_word()}:", ffmpeg_row)

        layout.addWidget(ffmpeg_group)

        cli_group = QGroupBox("CLI Overrides")
        cli_layout = QFormLayout(cli_group)
        cli_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        cli_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.extra_cli_args = QTextEdit()
        self.extra_cli_args.setMaximumHeight(100)
        self.extra_cli_args.setToolTip(
            "Extra Faster Whisper XXL CLI arguments. These are appended to the command as-is. "
            "Use --help with the executable to see available flags."
        )
        cli_layout.addRow("Extra CLI Args:", self.extra_cli_args)
        cli_hint = QLabel("Hint: run the Faster Whisper XXL executable with --help to see available flags.")
        cli_hint.setWordWrap(True)
        cli_hint.setStyleSheet("color: #a0a0a0;")
        cli_layout.addRow(cli_hint)

        layout.addWidget(cli_group)
        layout.addStretch()

        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(scroll)

        fw_browse.clicked.connect(lambda: self.browse_executable_path(self.fw_exe_path))
        fw_clear.clicked.connect(lambda: self.clear_config_path(self.fw_exe_path))
        fw_test.clicked.connect(self.test_faster_whisper_path)
        fw_find.clicked.connect(self.show_faster_whisper_path_picker)
        model_browse.clicked.connect(lambda: self.browse_directory_path(self.model_dir_path))
        model_clear.clicked.connect(lambda: self.clear_config_path(self.model_dir_path))
        model_test.clicked.connect(self.test_model_dir_path)
        ytdlp_browse.clicked.connect(lambda: self.browse_executable_path(self.ytdlp_exe_path))
        ytdlp_clear.clicked.connect(lambda: self.clear_config_path(self.ytdlp_exe_path))
        ytdlp_test.clicked.connect(self.test_ytdlp_path)
        ytdlp_find.clicked.connect(self.show_ytdlp_path_picker)
        ffmpeg_browse.clicked.connect(lambda: self.browse_executable_path(self.ffmpeg_path_input))
        ffmpeg_clear.clicked.connect(lambda: self.clear_config_path(self.ffmpeg_path_input))
        ffmpeg_test.clicked.connect(self.test_ffmpeg_path)
        ffmpeg_find.clicked.connect(self.show_ffmpeg_path_picker)

        self.fw_exe_path.editingFinished.connect(self.save_config_settings)
        self.model_dir_path.editingFinished.connect(self.save_config_settings)
        self.ytdlp_exe_path.editingFinished.connect(self.save_config_settings)
        self.ffmpeg_path_input.editingFinished.connect(self.save_config_settings)
        self.ytdlp_source_combo.currentIndexChanged.connect(self.save_config_settings)

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

        # Both of these are single checkboxes; sharing one row keeps the
        # Global Settings panel from overflowing its scroll area.
        self.output_dir_source_checkbox = QCheckBox("Use source folder")
        self.output_dir_source_checkbox.setObjectName("output_dir_source_checkbox")
        self.output_dir_source_checkbox.setToolTip("Save outputs next to each input file.")

        self.output_name_match_checkbox = QCheckBox("Use input filename")
        self.output_name_match_checkbox.setObjectName("output_name_match_checkbox")
        self.output_name_match_checkbox.setToolTip(
            "Prefer the original input name for outputs. A suffix may still be added to avoid conflicts."
        )
        self.output_name_match_checkbox.setChecked(True)

        output_toggles = QWidget()
        output_toggles_layout = QHBoxLayout(output_toggles)
        output_toggles_layout.setContentsMargins(0, 0, 0, 0)
        output_toggles_layout.setSpacing(12)
        output_toggles_layout.addWidget(self.output_dir_source_checkbox)
        output_toggles_layout.addWidget(self.output_name_match_checkbox)
        output_toggles_layout.addStretch()
        output_toggles_label = QLabel("Output:")
        output_toggles_label.setToolTip(
            "Where outputs are saved, and whether they reuse the input filename."
        )
        layout.addRow(output_toggles_label, output_toggles)

        existing_tip = (
            "What to do when the outputs for a file already exist in the output folder.\n\n"
            "Add suffix: name the new output e.g. 'video_mp4.srt' (default, current behavior).\n"
            "Skip file: leave the existing outputs alone and move to the next file. "
            "Useful for resuming an interrupted batch.\n"
            "Overwrite: replace the existing outputs in place.\n\n"
            "A file is only skipped when every selected output format is already present."
        )
        self.existing_output_combo = QComboBox()
        for label, mode in (
            ("Add suffix", "suffix"),
            ("Skip file", "skip"),
            ("Overwrite", "overwrite"),
        ):
            self.existing_output_combo.addItem(label, mode)
        self.existing_output_combo.setToolTip(existing_tip)
        existing_label = QLabel("Existing Outputs:")
        existing_label.setToolTip(existing_tip)
        layout.addRow(existing_label, self.existing_output_combo)

        self.model_combo = QComboBox()
        self.model_combo.setToolTip(
            "Choose a model. Larger models are more accurate but use more resources. "
            "large-v3-turbo downloads a community CTranslate2 conversion (dropbox-dash)."
        )
        model_label = QLabel("Model:")
        model_label.setToolTip(
            "Choose a model. Larger models are more accurate but use more resources. "
            "large-v3-turbo downloads a community CTranslate2 conversion (dropbox-dash)."
        )
        self.model_manage_button = QPushButton("Manage")
        self.model_manage_button.setToolTip("Download, import, and enable models.")
        self.model_manage_button.setObjectName("manage_model_button")
        self.model_manage_button.setFixedWidth(90)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        model_row.addWidget(self.model_combo)
        model_row.addWidget(self.model_manage_button)
        layout.addRow(model_label, model_row)
        self.model_manage_button.clicked.connect(self.show_model_manager)
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
        scroll = QScrollArea()
        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        vad_group = QGroupBox("Voice Activity Detection")
        vad_layout = QFormLayout(vad_group)
        vad_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        vad_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        vad_layout.setHorizontalSpacing(10)
        vad_layout.setVerticalSpacing(4)
        vad_layout.setContentsMargins(8, 8, 8, 8)

        self.vad_filter = QCheckBox("Enable VAD Filter")
        self.vad_filter.setObjectName("vad_filter_checkbox")
        self.vad_filter.setToolTip(
            "Enable voice activity detection to filter non-speech."
        )
        vad_layout.addRow(self.vad_filter)
        vad_hint = QLabel("Hint: if quiet speech is missing, try disabling VAD or lowering the threshold.")
        vad_hint.setWordWrap(True)
        vad_hint.setStyleSheet("color: #a0a0a0;")
        vad_layout.addRow(vad_hint)
        self.vad_method = QComboBox()
        self.vad_method.addItems(['silero_v4_fw', 'silero_v5_fw', 'silero_v3', 'silero_v4', 'silero_v5', 'pyannote_v3', 'pyannote_onnx_v3', 'auditok', 'webrtc'])
        self.vad_method.setToolTip("Choose the VAD backend.")
        vad_layout.addRow("VAD Method:", self.vad_method)
        self.vad_device = QComboBox()
        self.vad_device.addItems(["Auto", "CUDA", "CPU"])
        self.vad_device.setToolTip("Choose the VAD device for pyannote VAD. Auto follows recommendations.")
        vad_layout.addRow("VAD Device:", self.vad_device)
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
        vad_layout.addRow("VAD Threshold:", self.vad_threshold)
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
        vad_layout.addRow("Min Speech Duration:", self.vad_min_speech)

        layout.addWidget(vad_group)

        diarize_group = QGroupBox("Diarization")
        diarize_layout = QVBoxLayout(diarize_group)
        diarize_layout.setContentsMargins(8, 8, 8, 8)
        diarize_layout.setSpacing(6)

        self.diarize_enable = QCheckBox("Enable Diarization")
        self.diarize_enable.setObjectName("diarize_enable_checkbox")
        self.diarize_enable.setToolTip("Identify speaker turns and label segments.")
        diarize_layout.addWidget(self.diarize_enable)

        diarize_hint = QLabel("Hint: diarization can be memory intensive on GPU; try CPU if you see OOM errors.")
        diarize_hint.setWordWrap(True)
        diarize_hint.setStyleSheet("color: #a0a0a0;")
        diarize_layout.addWidget(diarize_hint)

        self.diarize_controls_container = QWidget()
        diarize_controls_layout = QFormLayout(self.diarize_controls_container)
        diarize_controls_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        diarize_controls_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        diarize_controls_layout.setHorizontalSpacing(10)
        diarize_controls_layout.setVerticalSpacing(4)
        diarize_controls_layout.setContentsMargins(0, 6, 0, 0)

        self.diarize_backend = QComboBox()
        self.diarize_backend.addItems(["pyannote_v3.1", "pyannote_v3.0", "reverb_v1", "reverb_v2"])
        self.diarize_backend.setToolTip("Choose the diarization backend.")
        diarize_controls_layout.addRow("Backend:", self.diarize_backend)

        self.diarize_device = QComboBox()
        self.diarize_device.addItems(["Auto", "CUDA", "CPU"])
        self.diarize_device.setToolTip("Choose the diarization device. Auto follows backend defaults.")
        diarize_controls_layout.addRow("Device:", self.diarize_device)

        self.diarize_num_speakers = QSpinBox()
        self.diarize_num_speakers.setRange(0, 30)
        self.diarize_num_speakers.setToolTip("Fixed number of speakers. Set 0 to disable.")
        diarize_controls_layout.addRow("Fixed Speakers:", self.diarize_num_speakers)

        self.diarize_min_speakers = QSpinBox()
        self.diarize_min_speakers.setRange(0, 30)
        self.diarize_min_speakers.setToolTip("Minimum speakers. Set 0 to disable.")
        diarize_controls_layout.addRow("Min Speakers:", self.diarize_min_speakers)

        self.diarize_max_speakers = QSpinBox()
        self.diarize_max_speakers.setRange(0, 30)
        self.diarize_max_speakers.setToolTip("Maximum speakers. Set 0 to disable.")
        diarize_controls_layout.addRow("Max Speakers:", self.diarize_max_speakers)

        self.diarize_only_checkbox = QCheckBox("Diarize only (skip transcription)")
        self.diarize_only_checkbox.setObjectName("diarize_only_checkbox")
        self.diarize_only_checkbox.setToolTip(
            "Run diarization without transcription output."
        )
        diarize_controls_layout.addRow(self.diarize_only_checkbox)

        self.diarize_return_embeddings_checkbox = QCheckBox("Return embeddings")
        self.diarize_return_embeddings_checkbox.setObjectName("diarize_return_embeddings_checkbox")
        self.diarize_return_embeddings_checkbox.setToolTip(
            "Include speaker embedding vectors in diarization output (advanced)."
        )
        diarize_controls_layout.addRow(self.diarize_return_embeddings_checkbox)

        diarize_layout.addWidget(self.diarize_controls_container)

        self.diarize_review_prompt_checkbox = QCheckBox("Prompt to review after diarization")
        self.diarize_review_prompt_checkbox.setObjectName("diarize_review_prompt_checkbox")
        self.diarize_review_prompt_checkbox.setToolTip(
            "Show a prompt to review and rename speakers when diarization finishes."
        )
        self.diarize_review_prompt_checkbox.setChecked(True)
        diarize_layout.addWidget(self.diarize_review_prompt_checkbox)

        self.diarize_review_button = QPushButton("Review Diarization Output")
        self.diarize_review_button.setToolTip("Review segments and rename speakers.")
        self.diarize_review_button.setVisible(False)
        self.diarize_review_button.clicked.connect(self.show_diarization_review_dialog)
        diarize_layout.addWidget(self.diarize_review_button)

        layout.addWidget(diarize_group)
        layout.addStretch()

        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(scroll)

        self.diarize_enable.toggled.connect(self.update_diarization_controls)
        self.diarize_num_speakers.valueChanged.connect(self.update_diarization_controls)
        self.diarize_min_speakers.valueChanged.connect(self.update_diarization_controls)
        self.diarize_max_speakers.valueChanged.connect(self.update_diarization_controls)
        self.update_diarization_controls(self.diarize_enable.isChecked())

    def setup_audio_tab(self, tab):
        scroll = QScrollArea()
        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        pre_group = QGroupBox("Pre-Processing")
        self.audio_pre_group = pre_group
        pre_layout = QFormLayout(pre_group)
        pre_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        pre_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        pre_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        pre_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        pre_layout.setHorizontalSpacing(10)
        pre_layout.setVerticalSpacing(4)
        pre_layout.setContentsMargins(8, 8, 8, 8)

        self.ff_mp3 = QCheckBox("Convert to MP3")
        self.ff_mp3.setObjectName("ff_mp3_checkbox")
        self.ff_mp3.setToolTip("Convert input audio to MP3 before processing.")
        pre_layout.addRow(self.ff_mp3)

        self.audio_preprocess_enable = QCheckBox("Enable Audio Pre-Processing")
        self.audio_preprocess_enable.setObjectName("audio_preprocess_enable")
        pre_layout.addRow(self.audio_preprocess_enable)

        self.keep_preprocessed_audio = QCheckBox("Keep preprocessed audio files")
        self.keep_preprocessed_audio.setObjectName("keep_preprocessed_audio_checkbox")
        self.keep_preprocessed_audio.setToolTip("Leave the temporary WAV files in the output folder.")
        pre_layout.addRow(self.keep_preprocessed_audio)

        self.audio_gain_label = QLabel("Gain:")
        self.audio_gain = QDoubleSpinBox()
        self.audio_gain.setRange(-30.0, 30.0)
        self.audio_gain.setSingleStep(0.5)
        self.audio_gain.setDecimals(1)
        self.audio_gain.setSuffix(" dB")
        self.audio_gain.setToolTip("Boost or reduce input audio before transcription.")
        self.audio_gain.setValue(0.0)
        pre_layout.addRow(self.audio_gain_label, self.audio_gain)

        layout.addWidget(pre_group)

        norm_group = QGroupBox("Normalization")
        self.audio_norm_group = norm_group
        norm_layout = QFormLayout(norm_group)
        norm_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        norm_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        norm_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        norm_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        norm_layout.setHorizontalSpacing(10)
        norm_layout.setVerticalSpacing(4)
        norm_layout.setContentsMargins(8, 8, 8, 8)

        self.audio_normalize = QCheckBox("Normalize Loudness (LUFS)")
        self.audio_normalize.setObjectName("audio_normalize_checkbox")
        norm_layout.addRow(self.audio_normalize)

        self.audio_lufs_target_label = QLabel("Target LUFS:")
        self.audio_lufs_target = QDoubleSpinBox()
        self.audio_lufs_target.setRange(-30.0, -10.0)
        self.audio_lufs_target.setSingleStep(0.5)
        self.audio_lufs_target.setDecimals(1)
        self.audio_lufs_target.setSuffix(" LUFS")
        self.audio_lufs_target.setToolTip("Target loudness for normalization.")
        self.audio_lufs_target.setValue(-16.0)
        norm_layout.addRow(self.audio_lufs_target_label, self.audio_lufs_target)

        self.audio_true_peak_enable = QCheckBox("Limit True Peak")
        self.audio_true_peak_enable.setObjectName("audio_true_peak_checkbox")
        self.audio_true_peak_enable.setChecked(True)
        norm_layout.addRow(self.audio_true_peak_enable)

        self.audio_true_peak_label = QLabel("True Peak:")
        self.audio_true_peak = QDoubleSpinBox()
        self.audio_true_peak.setRange(-6.0, 0.0)
        self.audio_true_peak.setSingleStep(0.1)
        self.audio_true_peak.setDecimals(1)
        self.audio_true_peak.setSuffix(" dBTP")
        self.audio_true_peak.setToolTip("True peak ceiling when normalizing.")
        self.audio_true_peak.setValue(-1.5)
        norm_layout.addRow(self.audio_true_peak_label, self.audio_true_peak)

        self.audio_lra_label = QLabel("Target LRA:")
        self.audio_lra = QDoubleSpinBox()
        self.audio_lra.setRange(1.0, 20.0)
        self.audio_lra.setSingleStep(0.5)
        self.audio_lra.setDecimals(1)
        self.audio_lra.setSuffix(" LU")
        self.audio_lra.setToolTip("Loudness range target for normalization.")
        self.audio_lra.setValue(11.0)
        norm_layout.addRow(self.audio_lra_label, self.audio_lra)

        layout.addWidget(norm_group)

        self.audio_analyze_button = QPushButton("Analyze Loudness")
        self.audio_analyze_button.clicked.connect(self.analyze_loudness)
        self.audio_analyze_button.setMinimumHeight(28)
        self.audio_analyze_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.audio_analyze_button)
        self.audio_quiet_preset_button = QPushButton("Quiet Speech Preset")
        self.audio_quiet_preset_button.setCheckable(True)
        self.audio_quiet_preset_button.toggled.connect(self.toggle_quiet_speech_preset)
        self.audio_quiet_preset_button.setMinimumHeight(28)
        self.audio_quiet_preset_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.audio_quiet_preset_button)
        layout.addStretch()

        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(scroll)

        self.audio_preprocess_enable.toggled.connect(self.update_audio_preprocess_controls)
        self.audio_normalize.toggled.connect(self.update_audio_normalize_controls)
        self.audio_true_peak_enable.toggled.connect(self.update_audio_normalize_controls)
        self.update_audio_preprocess_controls(self.audio_preprocess_enable.isChecked())
        self.update_audio_normalize_controls(self.audio_normalize.isChecked())

