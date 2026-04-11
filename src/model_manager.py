"""
Model Manager dialog — extracted from gui_main.py.

This is a self-contained QDialog that lets users manage whisper models
(enable/disable, add from HF, import local, verify, delete).
It communicates with the main WhisperGUI window via ``self.parent``.
"""

import os
import sys
import shutil
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt, QTimer, QModelIndex

from converter_utils import scan_transformers_weights
from gui_components import ModelDownloadDialog
from python_utils import enumerate_python_runtimes

MODEL_DIR_PREFIX = "faster-whisper-"

class ModelManagerDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self._loading_table = False
        self.setWindowTitle("Manage Models")
        self.setModal(True)
        self.setMinimumSize(720, 720)

        layout = QVBoxLayout(self)
        info_label = QLabel(
            "Enable models to show them in the dropdown. Built-in models can be disabled too.\n"
            "Custom models are stored in the _models/custom folder."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        conversion_group = QGroupBox("Conversion Settings")
        conversion_layout = QFormLayout(conversion_group)
        self.auto_convert_checkbox = QCheckBox("Auto-convert Transformers models")
        self.auto_convert_checkbox.setToolTip(
            "When a model repo only has Transformers weights (model.safetensors / pytorch_model.bin), "
            "automatically convert it to CTranslate2 (model.bin)."
        )
        conversion_layout.addRow(self.auto_convert_checkbox)

        self.converter_python_path_input = QLineEdit()
        self.converter_python_path_input.setPlaceholderText("Optional: override converter Python")
        self.converter_python_path_input.setToolTip(
            "Use a specific Python for model conversion (e.g., your conda env python.exe). "
            "Leave empty to use the current Python."
        )
        self.converter_pick_button = QToolButton()
        self.converter_pick_button.setText("Detect ▾")
        self.converter_pick_button.setToolTip("Select from detected Python installations.")
        self.converter_pick_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.converter_pick_menu = QMenu(self.converter_pick_button)
        self.converter_pick_button.setMenu(self.converter_pick_menu)
        self.converter_pick_button.setStyleSheet(
            "QToolButton { padding-right: 6px; }"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        converter_browse = QPushButton("Browse")
        converter_browse.setToolTip("Select a Python executable for conversion.")
        self.converter_python_row_widget = QWidget()
        converter_row = QHBoxLayout(self.converter_python_row_widget)
        converter_row.setContentsMargins(0, 0, 0, 0)
        converter_row.addWidget(self.converter_python_path_input)
        converter_row.addWidget(self.converter_pick_button)
        converter_row.addWidget(converter_browse)
        self.converter_python_label = QLabel("Converter Python:")
        conversion_layout.addRow(self.converter_python_label, self.converter_python_row_widget)

        self.verify_bundle_button = QPushButton("Verify Bundle")
        self.verify_bundle_button.setToolTip("Check if the converter bundle is healthy.")
        self.repair_bundle_button = QPushButton("Repair Bundle")
        self.repair_bundle_button.setToolTip("Delete and re-download the converter bundle.")
        bundle_row = QHBoxLayout()
        bundle_row.addWidget(self.verify_bundle_button)
        bundle_row.addWidget(self.repair_bundle_button)
        bundle_row.addStretch()
        conversion_layout.addRow("Converter Bundle:", bundle_row)
        layout.addWidget(conversion_group)

        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Enabled", "Model", "Status", "Source", "Location"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._handle_item_changed)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.add_hf_button = QPushButton("Add from HF...")
        self.import_button = QPushButton("Import Local...")
        self.refresh_button = QPushButton("Rescan")
        self.verify_button = QPushButton("Verify Enabled")
        self.more_button = QToolButton()
        self.more_button.setText("More ▾")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self.more_button)
        self.enable_all_action = more_menu.addAction("Enable All")
        self.disable_all_action = more_menu.addAction("Disable All")
        more_menu.addSeparator()
        self.delete_selected_action = more_menu.addAction("Delete Selected...")
        self.more_button.setMenu(more_menu)
        self.more_button.setStyleSheet(
            "QToolButton { padding-right: 6px; }"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        self.close_button = QPushButton("Close")
        button_row.addWidget(self.add_hf_button)
        button_row.addWidget(self.import_button)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.verify_button)
        button_row.addWidget(self.more_button)
        button_row.addStretch()
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.add_hf_button.clicked.connect(self._add_from_hf)
        self.import_button.clicked.connect(self._import_local_model)
        self.refresh_button.clicked.connect(self._rescan_models)
        self.enable_all_action.triggered.connect(self._enable_all_models)
        self.disable_all_action.triggered.connect(self._disable_all_models)
        self.delete_selected_action.triggered.connect(self._delete_selected_models)
        self.verify_button.clicked.connect(self._verify_selected_model)
        self.close_button.clicked.connect(self.accept)
        self.auto_convert_checkbox.toggled.connect(self._save_auto_convert_setting)
        self.converter_python_path_input.textChanged.connect(self._save_converter_python_path)
        self.converter_pick_menu.aboutToShow.connect(self._populate_converter_python_menu)
        self.converter_pick_menu.triggered.connect(self._select_converter_python_action)
        converter_browse.clicked.connect(self._browse_converter_python)
        if getattr(sys, "frozen", False):
            self.converter_python_row_widget.setVisible(False)
            self.converter_python_label.setVisible(False)
            self.converter_python_hint = QLabel(
                "EXE builds use the converter bundle by default."
            )
            self.converter_python_hint.setWordWrap(True)
            self.converter_python_hint.setStyleSheet("color: #a0a0a0;")
            self.converter_python_link = QPushButton("Use custom Python anyway...")
            self.converter_python_link.setFlat(True)
            self.converter_python_link.setStyleSheet(
                "QPushButton { color: #4da3ff; text-decoration: underline; border: none; padding: 0; }"
            )
            conversion_layout.addRow(self.converter_python_hint)
            conversion_layout.addRow(self.converter_python_link)
            self.converter_python_link.clicked.connect(self._show_converter_python_row)
        self.verify_bundle_button.clicked.connect(self.parent.verify_converter_bundle)
        self.repair_bundle_button.clicked.connect(self.parent.repair_converter_bundle)

        self._load_table()
        self._load_conversion_settings()
        QTimer.singleShot(0, self._clear_table_focus)

    def _clear_table_focus(self):
        self.table.setCurrentIndex(QModelIndex())
        self.table.clearSelection()
        if self.close_button:
            self.close_button.setFocus()

    def _load_conversion_settings(self):
        checkbox_settings = self.parent.settings.get("checkboxes", {})
        auto_convert = bool(checkbox_settings.get("auto_convert_transformers_checkbox", False))
        self.auto_convert_checkbox.blockSignals(True)
        self.auto_convert_checkbox.setChecked(auto_convert)
        self.auto_convert_checkbox.blockSignals(False)

        converter_path = self.parent.settings.get("converter_python_path", "")
        self.converter_python_path_input.blockSignals(True)
        self.converter_python_path_input.setText(converter_path)
        self.converter_python_path_input.blockSignals(False)

    def _save_auto_convert_setting(self, checked):
        checkbox_settings = dict(self.parent.settings.get("checkboxes", {}))
        checkbox_settings["auto_convert_transformers_checkbox"] = bool(checked)
        self.parent.settings["checkboxes"] = checkbox_settings
        self.parent.save_settings_to_file()

    def _save_converter_python_path(self):
        converter_path = self.converter_python_path_input.text().strip()
        self.parent.settings["converter_python_path"] = converter_path
        if converter_path:
            os.environ["FWHISPER_CONVERTER_PYTHON"] = converter_path
        else:
            os.environ.pop("FWHISPER_CONVERTER_PYTHON", None)
        self.parent.save_settings_to_file()

    def _browse_converter_python(self):
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
        self._save_converter_python_path()

    def _show_converter_python_row(self):
        if getattr(self, "converter_python_row_widget", None):
            self.converter_python_row_widget.setVisible(True)
        if getattr(self, "converter_python_label", None):
            self.converter_python_label.setVisible(True)
        if getattr(self, "converter_python_hint", None):
            self.converter_python_hint.setVisible(False)
        if getattr(self, "converter_python_link", None):
            self.converter_python_link.setVisible(False)

    def _populate_converter_python_menu(self):
        self.converter_pick_menu.clear()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            runtimes = enumerate_python_runtimes()
        finally:
            QApplication.restoreOverrideCursor()
        items = []
        for runtime in runtimes:
            exe = runtime.get("executable")
            version = runtime.get("version") or "unknown"
            if not exe:
                continue
            label = f"Python {version} - {exe}"
            if getattr(sys, "frozen", False):
                try:
                    if os.path.abspath(exe) == os.path.abspath(sys.executable):
                        label = f"Bundled (EXE) - Python {version} - {exe}"
                except Exception:
                    pass
            items.append((label, exe))
        if not items:
            action = self.converter_pick_menu.addAction("No Python installations found")
            action.setEnabled(False)
            return
        for label, exe in items:
            action = self.converter_pick_menu.addAction(label)
            action.setData(exe)

    def _select_converter_python_action(self, action):
        path = action.data()
        if not path:
            return
        self.converter_python_path_input.setText(str(path))
        self._save_converter_python_path()

    def _sorted_entries(self):
        priority = {
            "builtin": 0,
            "hf": 1,
            "local": 2,
        }
        entries = list(self.parent._get_models_registry())
        return sorted(
            entries,
            key=lambda entry: (
                priority.get(entry.get("source", "unknown"), 99),
                (entry.get("display_name") or entry.get("name") or "").lower(),
            ),
        )

    def _load_table(self):
        entries = self._sorted_entries()
        self._loading_table = True
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            enabled_item.setCheckState(Qt.CheckState.Checked if entry.get("enabled") else Qt.CheckState.Unchecked)
            enabled_item.setData(Qt.ItemDataRole.UserRole, entry.get("name"))
            self.table.setItem(row, 0, enabled_item)

            display_name = entry.get("display_name") or entry.get("name") or ""
            name_item = QTableWidgetItem(display_name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            tooltip_parts = [f"Internal name: {entry.get('name', '')}"]
            if entry.get("repo_id"):
                tooltip_parts.append(f"Repo: {entry.get('repo_id')}")
            name_item.setToolTip("\n".join(part for part in tooltip_parts if part))
            self.table.setItem(row, 1, name_item)

            status_value = entry.get("verify_status")
            status_label = "Unknown"
            if status_value == "ok":
                status_label = "OK"
            elif status_value == "failed":
                status_label = "Failed"
            elif status_value == "missing":
                status_label = "Not Downloaded"
            elif status_value == "skipped":
                status_label = "Skipped"
            status_item = QTableWidgetItem(status_label)
            status_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if entry.get("verify_message"):
                status_item.setToolTip(entry.get("verify_message"))
            self.table.setItem(row, 2, status_item)

            source_value = entry.get("source", "unknown")
            source_label = {
                "builtin": "Built-in",
                "hf": "HF",
                "local": "Local",
            }.get(source_value, "Unknown")
            source_item = QTableWidgetItem(source_label)
            source_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 3, source_item)

            location_label = "Custom" if entry.get("root") == "custom" else "Default"
            location_item = QTableWidgetItem(location_label)
            location_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 4, location_item)
        self._loading_table = False

    def _handle_item_changed(self, item):
        if self._loading_table or item.column() != 0:
            return
        model_name = item.data(Qt.ItemDataRole.UserRole)
        entry = self.parent._find_model_entry(model_name)
        if not entry:
            return
        new_enabled = item.checkState() == Qt.CheckState.Checked
        if not new_enabled:
            enabled_count = sum(1 for e in self.parent._get_models_registry() if e.get("enabled"))
            if enabled_count <= 1:
                QMessageBox.information(
                    self,
                    "Model Required",
                    "At least one model must remain enabled.",
                )
                self._loading_table = True
                item.setCheckState(Qt.CheckState.Checked)
                self._loading_table = False
                return
        entry["enabled"] = new_enabled
        self.parent._models_registry_dirty = True

    def _add_from_hf(self):
        repo_text, ok = QInputDialog.getText(
            self,
            "Add Model from Hugging Face",
            "Enter Hugging Face repo ID or URL:",
        )
        if not ok or not repo_text.strip():
            return
        repo_id = self.parent._parse_hf_repo_id(repo_text)
        if not repo_id:
            QMessageBox.warning(self, "Invalid Repo", "Please enter a valid Hugging Face repo ID or URL.")
            return
        default_name = repo_id.split("/", 1)[-1]
        display_name, ok = QInputDialog.getText(
            self,
            "Model Name",
            "Display name (used for folder and dropdown):",
            text=default_name,
        )
        if not ok:
            return
        display_name = (display_name or "").strip() or default_name
        model_name = self.parent._sanitize_model_name(display_name)
        if not model_name:
            QMessageBox.warning(self, "Invalid Name", "Please provide a valid model name.")
            return
        _, base_local = self.parent.get_model_dirs()
        if not base_local:
            QMessageBox.warning(self, "Model Directory", "Model directory is not available yet.")
            return
        custom_root = os.path.join(base_local, "custom")
        target_dir = os.path.join(custom_root, self.parent._get_model_folder_name(model_name))
        existing_entry = self.parent._find_model_entry(model_name)
        if existing_entry:
            folder_exists = os.path.isdir(target_dir)
            has_model_bin = folder_exists and os.path.isfile(os.path.join(target_dir, "model.bin"))
            has_weights = folder_exists and bool(scan_transformers_weights(target_dir))
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Model Already Exists")
            dialog.setIcon(QMessageBox.Icon.Warning)
            if has_model_bin:
                dialog.setText(
                    "A model with this name already exists and contains model.bin:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Replace the folder to re-download a clean copy."
                )
            elif has_weights:
                dialog.setText(
                    "A model with this name already exists, but it still needs conversion:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Convert the existing files now, or replace the folder to re-download."
                )
            else:
                dialog.setText(
                    "A model with this name already exists, but the folder looks incomplete:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Replace the folder to download a clean copy."
                )
            convert_button = None
            if has_weights and not has_model_bin:
                convert_button = dialog.addButton("Convert existing files", QMessageBox.ButtonRole.ActionRole)
            replace_button = dialog.addButton("Replace folder", QMessageBox.ButtonRole.DestructiveRole)
            dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(replace_button)
            dialog.exec()
            clicked = dialog.clickedButton()
            if convert_button and clicked is convert_button:
                self.parent._maybe_convert_transformers_model(target_dir, parent=self)
                return
            if clicked is replace_button:
                registry = self.parent._get_models_registry()
                registry[:] = [entry for entry in registry if entry.get("name") != model_name]
                self.parent._models_registry_dirty = True
                self.parent.save_settings_to_file()
                if folder_exists:
                    try:
                        shutil.rmtree(target_dir)
                    except Exception as exc:
                        QMessageBox.warning(
                            self,
                            "Delete Failed",
                            f"Could not delete existing folder:\n{exc}",
                        )
                        return
            else:
                return
        if os.path.exists(target_dir):
            has_model_bin = os.path.isfile(os.path.join(target_dir, "model.bin"))
            has_weights = bool(scan_transformers_weights(target_dir))
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Folder Exists")
            dialog.setIcon(QMessageBox.Icon.Warning)
            if has_model_bin or has_weights:
                dialog.setText(
                    "A model folder already exists for this name:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Replace it to re-download a clean copy (recommended). "
                    "Use existing only if you trust the current files."
                )
            else:
                dialog.setText(
                    "A model folder already exists, but it looks incomplete:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Replace it to download a clean copy."
                )
            use_button = None
            if has_model_bin or has_weights:
                use_button = dialog.addButton("Use existing files", QMessageBox.ButtonRole.ActionRole)
            delete_button = dialog.addButton("Replace folder", QMessageBox.ButtonRole.DestructiveRole)
            dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(delete_button)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is delete_button:
                try:
                    shutil.rmtree(target_dir)
                except Exception as exc:
                    QMessageBox.warning(
                        self,
                        "Delete Failed",
                        f"Could not delete existing folder:\n{exc}",
                    )
                    return
            elif use_button and clicked is use_button:
                model_bin = os.path.join(target_dir, "model.bin")
                if not os.path.isfile(model_bin) and not scan_transformers_weights(target_dir):
                    QMessageBox.warning(
                        self,
                        "Invalid Model Folder",
                        "No model.bin or Transformers weights were found in that folder.\n"
                        "Please delete it and re-download.",
                    )
                    return
                self.parent._get_models_registry().append({
                    "name": model_name,
                    "display_name": display_name,
                    "source": "hf",
                    "enabled": True,
                    "root": "custom",
                    "repo_id": repo_id,
                })
                self.parent._models_registry_dirty = True
                self._load_table()
                if not os.path.isfile(model_bin):
                    self.parent._maybe_convert_transformers_model(target_dir, parent=self)
                return
            else:
                return
        confirm = QMessageBox.question(
            self,
            "Download Model",
            f"Download '{display_name}' from {repo_id} to:\n{target_dir}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        dialog = ModelDownloadDialog(
            model_name,
            target_dir,
            parent=self,
            repo_id=repo_id,
            display_name=display_name,
            download_all_files=True,
            auto_convert_transformers=self.parent._should_auto_convert_transformers(),
        )
        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted:
            return
        self.parent._get_models_registry().append({
            "name": model_name,
            "display_name": display_name,
            "source": "hf",
            "enabled": True,
            "root": "custom",
            "repo_id": repo_id,
        })
        self.parent._models_registry_dirty = True
        self._load_table()

    def _import_local_model(self):
        source_dir = QFileDialog.getExistingDirectory(self, "Select a CTranslate2 Model Folder")
        if not source_dir:
            return
        model_bin = os.path.join(source_dir, "model.bin")
        if not os.path.isfile(model_bin):
            if not self.parent._maybe_convert_transformers_model(source_dir, parent=self):
                QMessageBox.warning(self, "Invalid Model", "model.bin was not found in that folder.")
                return
        default_name = os.path.basename(source_dir)
        if default_name.startswith(MODEL_DIR_PREFIX):
            default_name = default_name[len(MODEL_DIR_PREFIX):]
        display_name, ok = QInputDialog.getText(
            self,
            "Model Name",
            "Display name (used for folder and dropdown):",
            text=default_name,
        )
        if not ok:
            return
        display_name = (display_name or "").strip() or default_name
        model_name = self.parent._sanitize_model_name(display_name)
        if not model_name:
            QMessageBox.warning(self, "Invalid Name", "Please provide a valid model name.")
            return
        _, base_local = self.parent.get_model_dirs()
        if not base_local:
            QMessageBox.warning(self, "Model Directory", "Model directory is not available yet.")
            return
        custom_root = os.path.join(base_local, "custom")
        target_dir = os.path.join(custom_root, self.parent._get_model_folder_name(model_name))
        existing_entry = self.parent._find_model_entry(model_name)
        if existing_entry:
            folder_exists = os.path.isdir(target_dir)
            has_model_bin = folder_exists and os.path.isfile(os.path.join(target_dir, "model.bin"))
            has_weights = folder_exists and bool(scan_transformers_weights(target_dir))
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Model Already Exists")
            dialog.setIcon(QMessageBox.Icon.Warning)
            if has_model_bin:
                dialog.setText(
                    "A model with this name already exists and contains model.bin:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Replace the folder to re-import a clean copy."
                )
            elif has_weights:
                dialog.setText(
                    "A model with this name already exists, but it still needs conversion:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Convert the existing files now, or replace the folder to re-import."
                )
            else:
                dialog.setText(
                    "A model with this name already exists, but the folder looks incomplete:\n"
                    f"{target_dir}"
                )
                dialog.setInformativeText(
                    "Replace the folder to import a clean copy."
                )
            convert_button = None
            if has_weights and not has_model_bin:
                convert_button = dialog.addButton("Convert existing files", QMessageBox.ButtonRole.ActionRole)
            replace_button = dialog.addButton("Replace folder", QMessageBox.ButtonRole.DestructiveRole)
            dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(replace_button)
            dialog.exec()
            clicked = dialog.clickedButton()
            if convert_button and clicked is convert_button:
                self.parent._maybe_convert_transformers_model(target_dir, parent=self)
                return
            if clicked is replace_button:
                registry = self.parent._get_models_registry()
                registry[:] = [entry for entry in registry if entry.get("name") != model_name]
                self.parent._models_registry_dirty = True
                self.parent.save_settings_to_file()
                if folder_exists:
                    try:
                        shutil.rmtree(target_dir)
                    except Exception as exc:
                        QMessageBox.warning(
                            self,
                            "Delete Failed",
                            f"Could not delete existing folder:\n{exc}",
                        )
                        return
            else:
                return
        if os.path.exists(target_dir):
            QMessageBox.warning(
                self,
                "Folder Exists",
                f"The folder already exists:\n{target_dir}",
            )
            return
        confirm = QMessageBox.question(
            self,
            "Import Model",
            "This will copy the selected model folder into:\n"
            f"{target_dir}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.copytree(source_dir, target_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Import Failed", f"Failed to copy model:\n{exc}")
            return
        self.parent._get_models_registry().append({
            "name": model_name,
            "display_name": display_name,
            "source": "local",
            "enabled": True,
            "root": "custom",
        })
        self.parent._models_registry_dirty = True
        self._load_table()

    def _rescan_models(self):
        self.parent._sync_models_from_disk()
        self._load_table()

    def _enable_all_models(self):
        entries = self.parent._get_models_registry()
        for entry in entries:
            entry["enabled"] = True
        self.parent._models_registry_dirty = True
        self._load_table()

    def _disable_all_models(self):
        entries = self.parent._get_models_registry()
        if not entries:
            return
        keep_name = self.parent._get_selected_model_name()
        keep_entry = self.parent._find_model_entry(keep_name)
        if not keep_entry and entries:
            keep_entry = entries[0]
        for entry in entries:
            entry["enabled"] = False
        if keep_entry:
            keep_entry["enabled"] = True
        self.parent._models_registry_dirty = True
        self._load_table()

    def _get_selected_table_models(self):
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        models = []
        for row in sorted(selected_rows):
            name_item = self.table.item(row, 0)
            if name_item:
                model_name = name_item.data(Qt.ItemDataRole.UserRole)
                if model_name:
                    models.append(model_name)
        return models

    def _verify_selected_model(self):
        model_names = [entry.get("name") for entry in self.parent._get_enabled_model_entries()]
        model_names = [name for name in model_names if name]
        if not model_names:
            QMessageBox.warning(self, "Verify Model", "No model selected.")
            return
        self.parent.verify_model_files(model_names, parent=self)
        self._load_table()

    def _delete_selected_models(self):
        model_names = self._get_selected_table_models()
        if not model_names:
            QMessageBox.warning(self, "Delete Models", "No models selected.")
            return

        custom_names = []
        blocked_names = []
        for name in model_names:
            entry = self.parent._find_model_entry(name)
            if not entry:
                continue
            if entry.get("root") != "custom":
                blocked_names.append(name)
                continue
            custom_names.append(name)

        if blocked_names and not custom_names:
            QMessageBox.information(
                self,
                "Delete Models",
                "Only custom models can be deleted from disk.",
            )
            return

        if not custom_names:
            QMessageBox.warning(self, "Delete Models", "No custom models selected.")
            return

        message = (
            "Delete selected custom models?\n\n"
            "• Delete Files: removes model folders from disk (recommended to free space)\n"
            "• Remove Only: removes from the list but keeps files on disk (may reappear after Rescan)\n"
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Delete Models")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(message)
        delete_button = dialog.addButton("Delete Files", QMessageBox.ButtonRole.DestructiveRole)
        remove_button = dialog.addButton("Remove Only", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()

        if clicked not in (delete_button, remove_button):
            return

        delete_files = clicked is delete_button
        errors = []
        removed = 0

        registry = self.parent._get_models_registry()
        remaining = []
        _, base_local = self.parent.get_model_dirs()
        custom_root = os.path.join(base_local, "custom") if base_local else None

        for entry in registry:
            entry_name = entry.get("name")
            entry_root = entry.get("root")
            if entry_name in custom_names and entry_root == "custom":
                removed += 1
                if delete_files and custom_root:
                    folder = self.parent._get_model_folder_name(entry_name)
                    target_dir = os.path.join(custom_root, folder)
                    if os.path.isdir(target_dir):
                        try:
                            shutil.rmtree(target_dir)
                        except Exception as exc:
                            errors.append(f"{entry_name}: {exc}")
                continue
            remaining.append(entry)

        if removed:
            self.parent.settings["models_registry"] = remaining
            self.parent._models_registry_dirty = True
            self.parent.save_settings_to_file()
            self.parent._models_registry_dirty = False
            self._load_table()
            self.parent._refresh_model_combo(preferred_name=None)

        if errors:
            QMessageBox.warning(
                self,
                "Delete Models",
                "Some models could not be deleted:\n" + "\n".join(errors),
            )

