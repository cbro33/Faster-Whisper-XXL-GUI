# Faster Whisper XXL GUI

![AMOLED Theme Screenshot](AMOLEDThemeScreenshot.png)

Faster Whisper XXL GUI is a desktop interface for the Faster Whisper XXL transcription engine. It supports local files, YouTube downloads, and a wide range of output formats with configurable VAD/audio settings.

## Features

- File and YouTube transcription (audio-only or full video).
- Automatic dependency setup (Faster Whisper XXL + FFmpeg).
- Model/task/language controls plus VAD and audio options.
- Model Manager with custom HF/local models and Transformers -> CT2 conversion.
- Multiple output formats (SRT, VTT, JSON, TXT, etc.).
- Skip or overwrite existing outputs, for resuming interrupted batches.
- Light/Dark/AMOLED themes.
- Persistent settings.

## Quick Start (Windows)

1. Download the latest `.exe` from the [Releases](https://github.com/cbro33/Faster-Whisper-XXL-GUI/releases) page.
2. Run it (no installation required).
3. On first launch, accept the prompt to download and set up Faster Whisper XXL + FFmpeg.

## Antivirus False Positives

Some antivirus products flag the `.exe` as a trojan, usually when it first downloads a model or the Faster Whisper XXL archive. This is a false positive: the release is an unsigned PyInstaller build, and antivirus engines score that packaging heavily on its own.

Add an exclusion for the app folder, or run from source instead. Reporting the file to your antivirus vendor as a false positive helps everyone using that product. Microsoft Defender submissions go [here](https://www.microsoft.com/en-us/wdsi/filesubmission). See the [Wiki](https://github.com/cbro33/Faster-Whisper-XXL-GUI/wiki) for details.

## Code Signing

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

Windows releases are built by GitHub Actions from the tagged commit and signed by SignPath. The private key is held by SignPath and is never in this repository or on a maintainer machine. See [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md).

## Manual yt-dlp Updates (No Python)

1. Download the latest `yt-dlp.exe` from the official [yt-dlp releases page](https://github.com/yt-dlp/yt-dlp/releases).
2. Place it in a stable folder (or anywhere on your PATH).
3. In the app, go to Settings → yt-dlp and set Source to `EXE (custom or PATH)`, then browse to the file.
4. To update later, replace that `yt-dlp.exe` with a newer one.

## Run From Source

1. Install Python 3.8+ and `pip`.
2. Clone and install:
   ```bash
   git clone https://github.com/cbro33/Faster-Whisper-XXL-GUI.git
   cd Faster-Whisper-XXL-GUI
   pip install -r requirements.txt
   ```
   If you want Transformers model conversion from source:
   ```bash
   pip install ctranslate2 transformers[torch] safetensors sentencepiece
   ```
3. Launch:
   ```bash
   python src/faster-whisper-xxl-gui.py
   ```

## Manual Setup (If Auto Download Fails)

Auto Setup is still a WIP and may not work all the time on every machine. If there are issues, you can do a manual installation.
Download the standalone Faster Whisper XXL archive and extract its contents into the app `bin` folder.

- [Release page](https://github.com/Purfview/whisper-standalone-win/releases/tag/Faster-Whisper-XXL)
- [Windows archive](https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z)
- [Linux archive](https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_linux.7z)

If extraction fails on Windows, install [7-Zip](https://www.7-zip.org/).

## Usage

1. Add files in the **File** tab or provide a URL in **yt-dlp**.
2. Adjust settings in **Global Settings**, **Advanced**, **VAD**, or **Audio** tabs.
3. Manage models in **Manage Models** (download, import, enable, verify).
4. Click **Run** and check the console output for progress.
5. Outputs are saved to your chosen output directory (defaults to `output` in the app folder).

## Custom Models (HF + Local)

Open **Manage Models** to add custom models from Hugging Face or import local CT2 folders.

- HF repos with `model.bin` (CTranslate2) download directly.
- HF repos with `model.safetensors` / `pytorch_model.bin` will prompt to convert to CT2.
  - EXE: downloads a converter bundle (~250 MB) once.
  - Source: uses your current Python environment (install deps above).
- Advanced setting: **Converter Python** lets you point conversion at a specific Python (useful for conda).

## Docs

Detailed options and hardware guidance live in the [Wiki](https://github.com/cbro33/Faster-Whisper-XXL-GUI/wiki).

## Contributing

Issues and pull requests are welcome.

## License

This project uses the [GNU GPL 3](https://www.gnu.org/licenses/gpl-3.0.html). See [LICENSE](LICENSE).
