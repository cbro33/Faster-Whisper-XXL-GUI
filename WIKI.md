# Faster Whisper XXL GUI - Detailed Guide

This document provides an in-depth explanation of all the settings and options available in the Faster Whisper XXL GUI application, along with hardware recommendations for optimal performance.

## Table of Contents

1.  [Introduction](#1-introduction)
2.  [General Usage](#2-general-usage)
3.  [Input Options](#3-input-options)
    *   [File Tab](#file-tab)
    *   [yt-dlp Tab](#yt-dlp-tab)
4.  [Global Settings](#4-global-settings)
    *   [Model](#model)
    *   [Task](#task)
    *   [Language](#language)
    *   [Compute Type](#compute-type)
    *   [Device](#device)
    *   [Output Format](#output-format)
5.  [Advanced Settings](#5-advanced-settings)
    *   [Temperature](#temperature)
    *   [Beam Size](#beam-size)
    *   [Best Of](#best-of)
    *   [Patience](#patience)
    *   [Initial Prompt](#initial-prompt)
    *   [Word Timestamps](#word-timestamps)
    *   [Without Timestamps](#without-timestamps)
    *   [Verbose](#verbose)
    *   [Print Progress](#print-progress)
    *   [Highlight Words](#highlight-words)
6.  [VAD Settings (Voice Activity Detection)](#6-vad-settings-voice-activity-detection)
    *   [Enable VAD Filter](#enable-vad-filter)
    *   [VAD Method](#vad-method)
    *   [VAD Threshold](#vad-threshold)
    *   [Min Speech Duration](#min-speech-duration)
7.  [Audio Processing Settings](#7-audio-processing-settings)
    *   [Convert to MP3](#convert-to-mp3)
    *   [Loudness Normalization](#loudness-normalization)
    *   [Speech Normalization](#speech-normalization)
    *   [Adjust Tempo](#adjust-tempo)
8.  [Hardware Recommendations](#8-hardware-recommendations)
    *   [CPU vs. GPU](#cpu-vs-gpu)
    *   [Memory (RAM)](#memory-ram)
    *   [VRAM (GPU Memory)](#vram-gpu-memory)
    *   [Storage](#storage)
    *   [Recommended Configurations](#recommended-configurations)

---

## 1. Introduction

This guide serves as a comprehensive resource for users of the Faster Whisper XXL GUI, detailing each configuration option and providing insights into how they affect transcription quality and performance. Understanding these settings will help you achieve the best results for your specific needs and hardware.

## 2. General Usage

The application is divided into several tabs, each grouping related settings. The left panel contains all the controls, while the right panel displays the console output, showing the progress and any messages from the transcription process.

## 3. Input Options

### File Tab

*   **Input File:**
    *   **Description:** Specifies the path to your local audio or video file that you wish to transcribe.
    *   **Usage:** Click "Browse" to open a file dialog, or drag and drop a file directly onto the input field.
    *   **Supported Formats:** Common audio (MP3, WAV, M4A) and video (MP4, AVI, MOV, MKV) formats are supported.
*   **Output Directory:**
    *   **Description:** Defines where the generated transcription files (e.g., SRT, TXT) will be saved.
    *   **Usage:** Click "Browse" to select a folder. If left empty, files will be saved in an `output` folder within the application's directory.

### yt-dlp Tab

*   **YouTube URL:**
    *   **Description:** Enter the URL of a YouTube video you want to download and transcribe. The application uses `yt-dlp` to fetch the content.
    *   **Usage:** Paste the full YouTube video URL (e.g., `https://www.youtube.com/watch?v=dQw4w9WgXcQ`).
*   **Audio-only:**
    *   **Description:** If checked, only the audio track of the YouTube video will be downloaded and processed. If unchecked, the full video will be downloaded.
    *   **Recommendation:** For transcription purposes, downloading audio-only is usually sufficient and significantly faster, saving bandwidth and storage. Only uncheck if you specifically need the video file.
*   **Manual yt-dlp updates (no Python):**
    *   **Description:** Download the latest `yt-dlp.exe` and set Source to `EXE (custom or PATH)` in Settings.
    *   **Usage:** Replace the exe when a new release is available; the app will use that file for downloads.

## 4. Global Settings

These settings apply broadly to the transcription process and are crucial for performance and accuracy.

### Model

*   **Description:** Selects the Whisper model size to use for transcription. Larger models generally offer higher accuracy but require more computational resources (CPU/GPU and RAM/VRAM).
*   **Options:** `tiny`, `base`, `small`, `medium`, `large`, `large-v2`, `large-v3`, `large-v3-turbo`.
*   **Note:** `large-v3-turbo` uses a [community CTranslate2 conversion](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo). The official OpenAI repo is Transformers format and is not directly supported by the downloader.
*   **Note:** The app only recognizes CTranslate2 model folders (look for a `model.bin` file). You can set the Model Directory to either the parent `_models` folder or a specific model folder.
*   **Recommendation:**
    *   **`tiny`, `base`:** Good for quick transcriptions or systems with limited resources (e.g., older CPUs, integrated graphics). Accuracy might be lower.
    *   **`small`, `medium`:** A good balance between speed and accuracy for most modern systems.
    *   **`large`, `large-v2`, `large-v3`:** Highest accuracy, but demand significant resources. Recommended for systems with dedicated GPUs (especially NVIDIA with CUDA) and ample VRAM (8GB+). `large-v3` is the latest and most accurate.

### Task

*   **Description:** Determines whether the model should transcribe the audio (convert speech to text in the original language) or translate it (convert speech to English text).
*   **Options:** `transcribe`, `translate`.
*   **Usage:** Choose `transcribe` for same-language text output, `translate` for English output from any supported language.

### Language

*   **Description:** Specifies the language of the audio. Setting this correctly can improve accuracy, especially for non-English audio. If set to `auto`, the model will attempt to detect the language.
*   **Options:** `auto` or a specific language code (e.g., `en` for English, `es` for Spanish, `fr` for French).
*   **Recommendation:** Always specify the language if you know it. `auto` detection is generally good but can sometimes misidentify short or noisy audio segments.

### Compute Type

*   **Description:** Controls the precision of computations performed by the model. Lower precision types (e.g., `int8`, `float16`) can significantly speed up transcription and reduce memory usage, often with minimal impact on accuracy.
*   **Options:** `default`, `auto`, `int8`, `int8_float16`, `int8_float32`, `int8_bfloat16`, `int16`, `float16`, `float32`, `bfloat16`.
*   **Recommendation:**
    *   **`float16` (Half-precision floating point):** Recommended for most modern GPUs (NVIDIA, AMD, Intel Arc) as it offers a great balance of speed and accuracy. Requires GPU support.
    *   **`int8` (8-bit integer):** Fastest and lowest memory usage. Can be used on both CPU and GPU. May have a slight accuracy drop compared to `float16` or `float32`. Good for older hardware or when speed is paramount.
    *   **`float32` (Full-precision floating point):** Highest accuracy, but slowest and most memory-intensive. Use if `float16` or `int8` cause issues or if absolute maximum accuracy is required.
    *   **`bfloat16`:** Similar to `float16` but with a different internal representation. Some newer hardware might prefer this.
    *   **`auto` / `default`:** Let the system decide the optimal compute type based on your hardware.

### Device

*   **Description:** Selects the processing unit to use for transcription.
*   **Options:** `cuda`, `cpu`.
*   **Recommendation:**
    *   **`cuda`:** If you have an NVIDIA GPU with CUDA support, always choose `cuda`. This will leverage your GPU for significantly faster transcription times.
    *   **`cpu`:** If you do not have an NVIDIA GPU, or if you encounter issues with `cuda`, select `cpu`. Transcription will be slower but will work on any system.

### Output Format

*   **Description:** Choose the desired format(s) for the generated transcript files. You can select multiple formats.
*   **Options:** `json`, `vtt`, `srt`, `lrc`, `txt`, `text`, `tsv`, `all`.
*   **Usage:** Check the boxes for the formats you need. Checking `all` will generate all available formats.
*   **Common Formats:**
    *   `srt` (SubRip): Widely used for video subtitles.
    *   `vtt` (WebVTT): Another common subtitle format, especially for web videos.
    *   `txt` / `text`: Plain text transcript.
    *   `json`: Machine-readable format, useful for further processing.

## 5. Advanced Settings

These settings provide more granular control over the Whisper model's behavior.

### Temperature

*   **Description:** Controls the "creativity" or randomness of the model's output. A higher temperature (closer to 1.0) makes the output more diverse and potentially less predictable, while a lower temperature (closer to 0.0) makes it more deterministic and focused.
*   **Range:** 0.0 to 1.0
*   **Recommendation:**
    *   **0.0 (default):** Recommended for most transcription tasks where accuracy and consistency are paramount.
    *   **Higher values (e.g., 0.5-0.8):** Can be useful for very noisy audio or when the model struggles to produce any output, as it encourages more speculative decoding. However, it can also lead to more errors or hallucinations.

### Beam Size

*   **Description:** The number of alternative transcriptions the model considers at each step of the decoding process. A larger beam size explores more possibilities, potentially leading to a more accurate result, but increases computation time and memory usage.
*   **Range:** 1 to 10 (default 5)
*   **Recommendation:**
    *   **5 (default):** A good balance for most scenarios.
    *   **Higher values (e.g., 7-10):** Can improve accuracy for difficult audio, but will slow down transcription.
    *   **Lower values (e.g., 1-3):** Faster, but may reduce accuracy.

### Best Of

*   **Description:** The number of top candidates to consider when decoding. Similar to beam size, but applies to the final selection.
*   **Range:** 1 to 10 (default 5)
*   **Recommendation:** Keep it at the default (5) unless you have specific reasons to change it. Often used in conjunction with `beam_size`.

### Patience

*   **Description:** A parameter related to beam search decoding, influencing how long the model waits for a better hypothesis before committing to a segment. Higher patience can improve accuracy but increases latency.
*   **Range:** 0.0 to 10.0 (default 1.0)
*   **Recommendation:** The default of 1.0 is generally suitable. Adjusting this is usually for advanced users trying to fine-tune for very specific audio characteristics.

### Initial Prompt

*   **Description:** Provides an initial text prompt to the model, which can guide its transcription. This is useful for:
    *   **Context:** Giving the model context about the audio (e.g., "This is a meeting about quantum physics.").
    *   **Speaker Names:** Pre-filling common speaker names to improve consistency (e.g., "John: Hello. Jane: Hi.").
    *   **Acronyms/Jargon:** Introducing specific terms or acronyms that might not be in the model's vocabulary.
*   **Usage:** Enter a short phrase or sentence.
*   **Recommendation:** Use sparingly and precisely. An irrelevant or misleading prompt can degrade accuracy.

### Extra CLI Args

*   **Description:** Free-form command-line arguments passed directly to Faster Whisper XXL for advanced options not exposed in the UI. Located in the Paths and Overrides tab.
*   **Usage:** Enter flags as you would on the command line (e.g., `--diarize --vad_clip_duration 30`). These are appended to the command and can override earlier settings.
*   **Tip:** Run the Faster Whisper XXL executable with `--help` to see available flags.

### Word Timestamps

*   **Description:** If checked, the output will include timestamps for individual words, not just segments. This provides more granular timing information.
*   **Usage:** Useful for precise subtitle synchronization or detailed analysis of speech timing.

### Without Timestamps

*   **Description:** If checked, the output will *not* include any timestamps, producing a plain text transcript without timing information.
*   **Usage:** For simple text output where timing is not needed.

### Verbose

*   **Description:** If checked, the console output will be more detailed, showing more internal processing information from the Faster Whisper XXL engine.
*   **Usage:** Primarily for debugging or understanding the model's behavior.

### Print Progress

*   **Description:** If checked, the console output will display real-time progress updates during transcription.
*   **Usage:** Provides visual feedback on the transcription process.

### Highlight Words

*   **Description:** If checked, the output (e.g., in VTT or SRT) might include styling to highlight words as they are spoken, if supported by the output format and player.
*   **Usage:** Enhances readability for some subtitle viewers.

## 6. VAD Settings (Voice Activity Detection)

VAD helps the model focus only on segments containing speech, ignoring silence. This can improve accuracy and speed, especially for audio with long silent passages.

### Enable VAD Filter

*   **Description:** Toggles the Voice Activity Detection (VAD) filter on or off. When enabled, the VAD method and related settings become active.
*   **Recommendation:** Generally recommended for better accuracy and efficiency, especially with noisy audio or long silences.

### VAD Method

*   **Description:** Selects the algorithm used for Voice Activity Detection. Different methods have varying performance characteristics.
*   **Options:** `silero_v4_fw`, `silero_v5_fw`, `silero_v3`, `silero_v4`, `silero_v5`, `pyannote_v3`, `pyannote_onnx_v3`, `auditok`, `webrtc`.
*   **Recommendation:**
    *   **`silero_v5_fw` (or `silero_v4_fw`):** Often a good default, optimized for Faster Whisper.
    *   **`pyannote_v3` / `pyannote_onnx_v3`:** Highly accurate but can be more resource-intensive.
    *   **`webrtc`:** Fast and lightweight, good for real-time or less demanding scenarios.
    *   Experiment with different methods to find what works best for your specific audio.

### VAD Threshold

*   **Description:** The sensitivity threshold for the VAD filter. A higher value means the VAD is more aggressive in identifying speech (less likely to include silence), while a lower value is more lenient (more likely to include faint speech or background noise).
*   **Range:** 0.0 to 1.0
*   **Recommendation:** Adjust based on audio quality. For clean audio, a higher threshold might be fine. For noisy audio or faint speech, a lower threshold might be necessary to avoid cutting off words.

### Min Speech Duration

*   **Description:** The minimum duration (in milliseconds) that a detected segment must be considered speech. Shorter segments will be ignored.
*   **Range:** 0 to 10000 ms
*   **Usage:** Helps filter out very short noises or accidental sounds that might be misidentified as speech.

## 7. Audio Processing Settings

These options apply pre-processing to the audio before it's fed to the Whisper model, potentially improving transcription quality.

### Convert to MP3

*   **Description:** If checked, the input audio will be converted to MP3 format before transcription.
*   **Usage:** Can be useful if you encounter compatibility issues with certain audio formats or if you prefer a standardized input.

### Loudness Normalization

*   **Description:** Applies loudness normalization to the audio, adjusting its volume to a consistent level.
*   **Usage:** Can improve transcription accuracy for audio with varying volume levels, making quieter speech more audible to the model.

### Speech Normalization

*   **Description:** Applies specific normalization techniques optimized for speech audio.
*   **Usage:** Similar to loudness normalization, but potentially more targeted for speech characteristics.

### Adjust Tempo

*   **Description:** Allows you to adjust the playback speed (tempo) of the audio before transcription.
*   **Range:** 0.5 (half speed) to 2.0 (double speed)
*   **Usage:** Can be useful for very fast or very slow speech. Speeding up (e.g., 1.2x) can sometimes help the model process dense speech, while slowing down (e.g., 0.8x) might help with unclear or heavily accented speech.
*   **Note:** This is a pre-processing step; it does not affect the timestamps in the final transcript, which will still correspond to the original audio's timing.

## 8. Hardware Recommendations

The performance of Faster Whisper XXL is heavily dependent on your hardware, particularly the GPU.

### CPU vs. GPU

*   **CPU (Central Processing Unit):** Can run Faster Whisper XXL, but it will be significantly slower, especially for larger models. Modern multi-core CPUs can handle `tiny` or `base` models reasonably well.
*   **GPU (Graphics Processing Unit):** Highly recommended for faster transcription, especially with `medium`, `large`, or `large-v3` models. NVIDIA GPUs with CUDA support offer the best performance. AMD and Intel Arc GPUs can also be utilized, but performance may vary.

### Memory (RAM)

*   **Minimum:** 8GB RAM is a bare minimum for smaller models on CPU.
*   **Recommended:** 16GB RAM for general use, especially with larger models or when processing long audio files.
*   **Optimal:** 32GB+ RAM for heavy usage, very large models, or when running other demanding applications simultaneously.

### VRAM (GPU Memory)

*   **Crucial for GPU performance.** The larger the model, the more VRAM it requires.
*   **`tiny`, `base`:** Can often fit on GPUs with 2-4GB VRAM.
*   **`small`, `medium`:** Typically require 4-6GB VRAM.
*   **`large`, `large-v2`, `large-v3`:**
    *   **Minimum:** 8GB VRAM (e.g., NVIDIA RTX 3050/4050, GTX 1070/1080, RTX 2060/3060).
    *   **Recommended:** 12GB+ VRAM (e.g., NVIDIA RTX 3060 12GB, RTX 3070/3080/3090, RTX 4070/4080/4090). More VRAM allows for larger batch sizes and faster processing of long audio.

### Storage

*   **Models:** Whisper models can be large (e.g., `large-v3` is several GBs). Ensure you have enough disk space for the models and your output files.
*   **SSD (Solid State Drive):** Highly recommended for faster loading of models and processing of large audio/video files.

### Recommended Configurations

*   **Entry-Level (CPU Only / Older GPU):**
    *   **CPU:** Modern Quad-core i5/Ryzen 5 or better.
    *   **RAM:** 8GB - 16GB.
    *   **GPU:** (Optional) 2-4GB VRAM.
    *   **Settings:** `tiny` or `base` model, `cpu` device, `int8` compute type.
*   **Mid-Range (Modern GPU):**
    *   **CPU:** Modern Hexa-core i5/Ryzen 5 or better.
    *   **RAM:** 16GB.
    *   **GPU:** NVIDIA GTX 1660 Super / RTX 2060 / RTX 3050 / AMD RX 6600 (6-8GB VRAM).
    *   **Settings:** `small` or `medium` model, `cuda` (for NVIDIA) or `auto` device, `float16` compute type.
*   **High-End (Powerful GPU):**
    *   **CPU:** Modern Octa-core i7/Ryzen 7 or better.
    *   **RAM:** 32GB+.
    *   **GPU:** NVIDIA RTX 3070 / RTX 3080 / RTX 4070 / RTX 4080 / RTX 4090 (8GB+ VRAM).
    *   **Settings:** `large`, `large-v2`, or `large-v3` model, `cuda` device, `float16` compute type.

By carefully selecting your settings based on your hardware and the characteristics of your audio, you can optimize the performance and accuracy of your transcriptions with Faster Whisper XXL GUI.
