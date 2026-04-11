"""Info / diagnostic dialog methods for WhisperGUI, extracted as a mixin.

Contains: show_info_dialog, open_wiki_page, show_debug_log_dialog,
show_hardware_info, show_software_information, diagnose_gpu_detection.
"""

import logging
import os
import platform
import sys
import webbrowser

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices

from config import APP_VERSION
from utils import format_path_for_display, detect_faster_whisper_binary_version, get_app_directory
from python_utils import (
    enumerate_python_runtimes, get_execution_environment,
    get_executable_fallback_path, get_python_probe_log,
)
from ytdlp_utils import (
    get_python_update_plan, evaluate_yt_dlp_version_status,
    get_ytdlp_installation_info, set_ytdlp_debug_logging_enabled,
)
from gui_components import (
    show_setup_information, show_setup_critical, show_setup_warning,
    set_model_download_logging_enabled, get_model_download_log_path,
)
from gpu_utils import detect_hardware_capabilities
from converter_utils import get_converter_bundle_dir, get_converter_python_path


class InfoDialogsMixin:
    """Mixin providing info/diagnostic dialog methods for WhisperGUI."""

    def show_info_dialog(self, title, message, min_width=520, min_height=180):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumSize(min_width, min_height)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        label = QLabel(message)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        dialog.exec()

    def open_wiki_page(self):
        """Open the project wiki in the default browser."""
        try:
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
            set_ytdlp_debug_logging_enabled(enabled)

        enabled_checkbox.toggled.connect(on_toggle)

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
            bundle_dir = get_converter_bundle_dir()
            bundle_python = get_converter_python_path(bundle_dir)
            info_lines.append("Converter Bundle:")
            info_lines.append(f"• Bundle Directory: {format_path_for_display(bundle_dir)}")
            if bundle_python:
                info_lines.append(f"• Python: {format_path_for_display(bundle_python)}")
            else:
                info_lines.append("• Python: Not found (bundle not installed)")
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

            source = self.settings.get("yt_dlp_source", "bundled")
            plan = get_python_update_plan()
            info_lines.append("")
            info_lines.append("yt-dlp Update Planner:")
            if source == "path":
                info_lines.append("• Source: EXE (custom or PATH)")
                info_lines.append("• Update Method: Manual (download new yt-dlp.exe)")
            else:
                info_lines.append("• Source: Python module (bundled/current env)")
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
            failures = [e for e in probe_log if e.get("status") not in ("success",)]
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

            info_lines.append("")
            if source == "path":
                exe_version, exe_path, exe_error = self.get_ytdlp_exe_version()
                info_lines.append("yt-dlp EXE:")
                info_lines.append(f"• Version: {exe_version or 'Unknown'}")
                info_lines.append(f"• Location: {format_path_for_display(exe_path) or 'Unknown'}")
                if exe_error:
                    info_lines.append(f"• Status: {exe_error}")
                version_status = evaluate_yt_dlp_version_status(exe_version)
            else:
                ytdlp_info = get_ytdlp_installation_info()
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
