"""Output format helpers: timestamp parsing, speaker segment extraction,
sentences-only file creation, LRC generation, and speaker label operations.

All functions in this module are pure or do only file I/O — no GUI dependencies.
"""

import json
import os
import re


def parse_txt_timestamp(value):
    """Parse a txt-style timestamp (``mm:ss.xxx`` or ``hh:mm:ss.xxx``) to seconds."""
    value = (value or "").strip()
    if not value:
        return None
    parts = value.split(":")
    try:
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None
    return None


def parse_srt_timestamp(value):
    """Parse an SRT-style timestamp (``HH:MM:SS,mmm``) to seconds."""
    value = (value or "").strip()
    if not value:
        return None
    match = re.match(r"^(?P<hh>\d{2}):(?P<mm>\d{2}):(?P<ss>\d{2}),(?P<ms>\d{3})$", value)
    if not match:
        return None
    hours = int(match.group("hh"))
    minutes = int(match.group("mm"))
    seconds = int(match.group("ss"))
    millis = int(match.group("ms"))
    return hours * 3600 + minutes * 60 + seconds + (millis / 1000.0)


def format_lrc_timestamp(seconds):
    """Format *seconds* as an LRC timestamp ``mm:ss.cc``."""
    try:
        total_centis = int(round(max(0.0, float(seconds)) * 100))
    except (TypeError, ValueError):
        total_centis = 0
    minutes = total_centis // 6000
    secs = (total_centis // 100) % 60
    centis = total_centis % 100
    return f"{minutes:02d}:{secs:02d}.{centis:02d}"


def read_speaker_segments_from_srt(srt_path):
    """Return a list of ``{start, end, speaker}`` dicts parsed from an SRT file."""
    if not srt_path or not os.path.exists(srt_path):
        return []
    speaker_pattern = re.compile(r'^\s*\[(SPEAKER_?\d+)\]:\s*(.*)$')
    time_pattern = re.compile(
        r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
    )
    segments = []
    try:
        with open(srt_path, "r", encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle]
        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if not line:
                idx += 1
                continue
            if line.isdigit():
                idx += 1
                if idx >= len(lines):
                    break
                line = lines[idx].strip()
            time_match = time_pattern.match(line)
            if not time_match:
                idx += 1
                continue
            start = parse_srt_timestamp(time_match.group("start"))
            end = parse_srt_timestamp(time_match.group("end"))
            idx += 1
            text_lines = []
            while idx < len(lines) and lines[idx].strip():
                text_lines.append(lines[idx].strip())
                idx += 1
            text = " ".join(text_lines).strip()
            speaker_match = speaker_pattern.match(text)
            if speaker_match and start is not None and end is not None:
                speaker = speaker_match.group(1)
                segments.append({"start": start, "end": end, "speaker": speaker})
            idx += 1
    except Exception:
        return []
    return segments


def read_speaker_segments_from_txt(txt_path):
    """Return a list of ``{start, end, speaker}`` dicts parsed from a timestamped TXT file."""
    if not txt_path or not os.path.exists(txt_path):
        return []
    line_pattern = re.compile(
        r'^\[(?P<start>[\d:.]+)\s*-->\s*(?P<end>[\d:.]+)\]\s*(?P<text>.*)$'
    )
    speaker_pattern = re.compile(r'^\s*\[(SPEAKER_?\d+)\]:\s*(.*)$')
    segments = []
    try:
        with open(txt_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                match = line_pattern.match(line)
                if not match:
                    continue
                start = parse_txt_timestamp(match.group("start"))
                end = parse_txt_timestamp(match.group("end"))
                text = match.group("text").strip()
                speaker_match = speaker_pattern.match(text)
                if speaker_match and start is not None and end is not None:
                    speaker = speaker_match.group(1)
                    segments.append({"start": start, "end": end, "speaker": speaker})
    except Exception:
        return []
    return segments


def match_speaker_for_segment(start, end, speaker_segments):
    """Find the best-matching speaker for the time range *start*–*end*.

    Uses overlap first, then midpoint tolerance, then nearest midpoint.
    """
    if start is None or end is None or not speaker_segments:
        return None
    best_speaker = None
    best_overlap = 0.0
    for segment in speaker_segments:
        overlap = min(end, segment["end"]) - max(start, segment["start"])
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = segment["speaker"]
    if best_overlap > 0:
        return best_speaker
    mid = (start + end) / 2.0
    tolerance = 0.5
    for segment in speaker_segments:
        if (segment["start"] - tolerance) <= mid <= (segment["end"] + tolerance):
            return segment["speaker"]
    nearest = None
    nearest_delta = None
    for segment in speaker_segments:
        seg_mid = (segment["start"] + segment["end"]) / 2.0
        delta = abs(mid - seg_mid)
        if nearest_delta is None or delta < nearest_delta:
            nearest_delta = delta
            nearest = segment["speaker"]
    return nearest


def replace_speaker_labels_in_text(path, mapping):
    """Replace ``[SPEAKER_XX]:`` labels in a text file using *mapping*.

    Returns True if any replacements were made.
    """
    if not path or not os.path.exists(path):
        return False
    pattern = re.compile(r"\[(SPEAKER_?\d+)\]:")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()

        def repl(match):
            speaker_id = match.group(1)
            name = mapping.get(speaker_id)
            if name:
                return f"[{name}]:"
            return match.group(0)

        updated = pattern.sub(repl, content)
        if updated == content:
            return False
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)
        return True
    except Exception:
        return False


def apply_speaker_names_to_json(json_path, mapping):
    """Add ``speaker_name`` fields to JSON segments using *mapping*.

    Returns True if any names were added.
    """
    if not json_path or not os.path.exists(json_path):
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            segments = data.get("segments") or []
        elif isinstance(data, list):
            segments = data
        else:
            return False
        updated = False
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            speaker_id = segment.get("speaker")
            if not speaker_id:
                continue
            name = mapping.get(speaker_id)
            if name:
                segment["speaker_name"] = name
                updated = True
        if not updated:
            return False
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


def inject_speakers_into_json(json_path):
    """Inject speaker labels from companion SRT/TXT into a JSON output file.

    Looks for ``<basename>.srt`` and ``<basename>.txt`` next to *json_path*.
    Returns True if any speaker labels were added.
    """
    if not json_path or not os.path.exists(json_path):
        return False
    base = os.path.splitext(json_path)[0]
    srt_path = base + ".srt"
    txt_path = base + ".txt"
    speaker_segments = read_speaker_segments_from_srt(srt_path)
    if not speaker_segments:
        speaker_segments = read_speaker_segments_from_txt(txt_path)
    if not speaker_segments:
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            segments = data.get("segments") or []
        elif isinstance(data, list):
            segments = data
        else:
            return False
        updated = False
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            if segment.get("speaker"):
                continue
            start = segment.get("start")
            end = segment.get("end")
            speaker = match_speaker_for_segment(start, end, speaker_segments)
            if speaker:
                segment["speaker"] = speaker
                updated = True
        if not updated:
            return False
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


def create_sentences_only(txt_with_timestamps, sentences_only_path):
    """Create a sentences-only text file from a timestamped transcript.

    Returns ``(success: bool, warning: str | None)``.
    """
    if not os.path.exists(txt_with_timestamps):
        return False, f"Timestamped txt file not found at {txt_with_timestamps}"

    try:
        with open(txt_with_timestamps, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return False, f"Error reading txt file: {exc}"

    if not content.strip():
        return False, "Timestamped txt file is empty"

    pattern = re.compile(r'^\[[\d:.\->\s]+\]\s*(.*)$')
    speaker_pattern = re.compile(r'^\[(SPEAKER_?\d+)\]:\s*(.*)$')
    records = []
    saw_speaker = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if not match:
            continue
        text = match.group(1).strip()
        if not text:
            continue
        speaker_match = speaker_pattern.match(text)
        if speaker_match:
            speaker = speaker_match.group(1)
            remainder = speaker_match.group(2).strip()
            saw_speaker = True
            records.append((speaker, remainder))
        else:
            records.append((None, text))

    if not records:
        return False, "No timestamped content found in txt file"

    if saw_speaker:
        lines = []
        current_speaker = None
        current_parts = []
        for speaker, text in records:
            if speaker:
                if current_speaker and speaker != current_speaker:
                    prefix = f"[{current_speaker}]:"
                    if current_parts:
                        lines.append(prefix + " " + " ".join(current_parts))
                    else:
                        lines.append(prefix)
                    current_parts = []
                current_speaker = speaker
                if text:
                    current_parts.append(text)
            else:
                if current_speaker:
                    current_parts.append(text)
                else:
                    lines.append(text)
        if current_speaker:
            prefix = f"[{current_speaker}]:"
            if current_parts:
                lines.append(prefix + " " + " ".join(current_parts))
            else:
                lines.append(prefix)
        sentences_text = "\n".join(line for line in lines if line.strip())
    else:
        sentences_text = " ".join(text for _, text in records if text)

    if not sentences_text:
        return False, "No valid sentences extracted from txt file"

    try:
        with open(sentences_only_path, "w", encoding="utf-8") as f:
            f.write(sentences_text)
    except Exception as exc:
        return False, f"Error writing sentences file: {exc}"

    return True, None


def build_lrc_lines(segments):
    """Build LRC content lines from a list of JSON segment dicts.

    Returns a list of formatted LRC lines (strings).
    """
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
                word_ts = format_lrc_timestamp(word_start)
                word_chunks.append(f"<{word_ts}>{word_text}")
            if not word_chunks:
                text = str(segment.get("text", "")).strip()
                if not text:
                    continue
                lines.append(f"[{format_lrc_timestamp(start)}]{text}")
            else:
                line = f"[{format_lrc_timestamp(start)}]" + " ".join(word_chunks)
                lines.append(line)
        else:
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            lines.append(f"[{format_lrc_timestamp(start)}]{text}")
    return lines


def extra_args_has_flag(flag, text):
    """Return True if *flag* (e.g. ``--diarize``) appears in *text*."""
    if not text:
        return False
    pattern = re.compile(rf"(^|\s){re.escape(flag)}(=|\s|$)")
    return bool(pattern.search(text))


# Maps a GUI output-format label to the filename suffix it produces for a run
# with basename ``base`` (i.e. the output file is ``base`` + suffix).
FORMAT_SUFFIXES = {
    "json": ".json",
    "vtt": ".vtt",
    "srt": ".srt",
    "lrc": ".lrc",
    "tsv": ".tsv",
    "txt (with timestamps)": ".txt",
    "txt (sentences only)": "_sentences.txt",
}

# What the exe's ``--output_format all`` actually writes.
ALL_FORMAT_SUFFIXES = (".json", ".vtt", ".srt", ".lrc", ".tsv", ".txt")


def expected_output_suffixes(selected_formats):
    """Filename suffixes a given GUI format selection is expected to produce.

    *selected_formats* holds GUI checkbox labels (``"srt"``, ``"txt (sentences
    only)"``, ``"all"``, ...). Returns a sorted, de-duplicated list. An empty or
    unrecognised selection falls back to ``.srt``, matching ``build_command``'s
    default when nothing is checked.
    """
    if "all" in (selected_formats or []):
        return sorted(ALL_FORMAT_SUFFIXES)
    suffixes = {
        FORMAT_SUFFIXES[fmt]
        for fmt in (selected_formats or [])
        if fmt in FORMAT_SUFFIXES
    }
    if not suffixes:
        return [".srt"]
    return sorted(suffixes)


def expected_output_paths(output_dir, basename, selected_formats):
    """Absolute paths the run is expected to produce for *basename*."""
    if not output_dir or not basename:
        return []
    return [
        os.path.join(output_dir, f"{basename}{suffix}")
        for suffix in expected_output_suffixes(selected_formats)
    ]


def find_existing_outputs(output_dir, basename, selected_formats):
    """Split expected output paths into ``(existing, missing)``.

    Callers treat "safe to skip" as ``existing and not missing``. This is
    deliberately strict, because a batch interrupted partway through writing its formats
    leaves some files missing, and that file should be redone rather than
    silently skipped.
    """
    existing, missing = [], []
    for path in expected_output_paths(output_dir, basename, selected_formats):
        (existing if os.path.exists(path) else missing).append(path)
    return existing, missing
