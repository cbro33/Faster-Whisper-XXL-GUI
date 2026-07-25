import json
import os
import pytest

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
    expected_output_suffixes,
    expected_output_paths,
    find_existing_outputs,
)


# ---------------------------------------------------------------------------
# parse_txt_timestamp
# ---------------------------------------------------------------------------

class TestParseTxtTimestamp:
    def test_none_returns_none(self):
        assert parse_txt_timestamp(None) is None

    def test_empty_returns_none(self):
        assert parse_txt_timestamp("") is None
        assert parse_txt_timestamp("   ") is None

    def test_mm_ss(self):
        assert parse_txt_timestamp("1:30.0") == 90.0

    def test_mm_ss_fraction(self):
        result = parse_txt_timestamp("2:15.5")
        assert abs(result - 135.5) < 0.001

    def test_hh_mm_ss(self):
        assert parse_txt_timestamp("1:02:03.0") == 3723.0

    def test_hh_mm_ss_fraction(self):
        result = parse_txt_timestamp("0:01:30.500")
        assert abs(result - 90.5) < 0.001

    def test_single_part_returns_none(self):
        assert parse_txt_timestamp("123") is None

    def test_four_parts_returns_none(self):
        assert parse_txt_timestamp("1:2:3:4") is None

    def test_non_numeric_returns_none(self):
        assert parse_txt_timestamp("abc:def") is None


# ---------------------------------------------------------------------------
# parse_srt_timestamp
# ---------------------------------------------------------------------------

class TestParseSrtTimestamp:
    def test_none_returns_none(self):
        assert parse_srt_timestamp(None) is None

    def test_empty_returns_none(self):
        assert parse_srt_timestamp("") is None

    def test_valid(self):
        result = parse_srt_timestamp("01:02:03,456")
        expected = 1 * 3600 + 2 * 60 + 3 + 0.456
        assert abs(result - expected) < 0.001

    def test_zeros(self):
        assert parse_srt_timestamp("00:00:00,000") == 0.0

    def test_invalid_format(self):
        assert parse_srt_timestamp("1:2:3,4") is None

    def test_txt_format_rejected(self):
        assert parse_srt_timestamp("1:30.5") is None


# ---------------------------------------------------------------------------
# format_lrc_timestamp
# ---------------------------------------------------------------------------

class TestFormatLrcTimestamp:
    def test_zero(self):
        assert format_lrc_timestamp(0) == "00:00.00"

    def test_one_minute(self):
        assert format_lrc_timestamp(60) == "01:00.00"

    def test_fractional(self):
        assert format_lrc_timestamp(90.55) == "01:30.55"

    def test_negative_clamped(self):
        assert format_lrc_timestamp(-5) == "00:00.00"

    def test_none_input(self):
        assert format_lrc_timestamp(None) == "00:00.00"

    def test_string_number(self):
        assert format_lrc_timestamp("60") == "01:00.00"

    def test_non_numeric_string(self):
        assert format_lrc_timestamp("abc") == "00:00.00"


# ---------------------------------------------------------------------------
# match_speaker_for_segment
# ---------------------------------------------------------------------------

class TestMatchSpeakerForSegment:
    @pytest.fixture
    def segments(self):
        return [
            {"start": 0, "end": 10, "speaker": "SPEAKER_00"},
            {"start": 10, "end": 20, "speaker": "SPEAKER_01"},
            {"start": 20, "end": 30, "speaker": "SPEAKER_00"},
        ]

    def test_none_start(self, segments):
        assert match_speaker_for_segment(None, 5, segments) is None

    def test_none_end(self, segments):
        assert match_speaker_for_segment(0, None, segments) is None

    def test_empty_segments(self):
        assert match_speaker_for_segment(0, 5, []) is None

    def test_full_overlap(self, segments):
        assert match_speaker_for_segment(0, 10, segments) == "SPEAKER_00"

    def test_partial_overlap(self, segments):
        # 9-13 overlaps SPEAKER_00 by 1s, SPEAKER_01 by 3s
        assert match_speaker_for_segment(9, 13, segments) == "SPEAKER_01"

    def test_second_speaker(self, segments):
        assert match_speaker_for_segment(12, 18, segments) == "SPEAKER_01"

    def test_midpoint_tolerance(self):
        segs = [{"start": 10, "end": 20, "speaker": "SPEAKER_01"}]
        result = match_speaker_for_segment(9.0, 9.8, segs)
        assert result == "SPEAKER_01"

    def test_nearest_fallback(self):
        segs = [
            {"start": 0, "end": 5, "speaker": "SPEAKER_00"},
            {"start": 100, "end": 110, "speaker": "SPEAKER_01"},
        ]
        result = match_speaker_for_segment(50, 51, segs)
        assert result is not None


# ---------------------------------------------------------------------------
# read_speaker_segments_from_srt
# ---------------------------------------------------------------------------

class TestReadSpeakerSegmentsFromSrt:
    def test_nonexistent_file(self, tmp_path):
        assert read_speaker_segments_from_srt(str(tmp_path / "nope.srt")) == []

    def test_none_path(self):
        assert read_speaker_segments_from_srt(None) == []

    def test_valid_srt_with_speakers(self, tmp_path):
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n"
            "00:00:01,000 --> 00:00:05,000\n"
            "[SPEAKER_00]: Hello world\n"
            "\n"
            "2\n"
            "00:00:05,000 --> 00:00:10,000\n"
            "[SPEAKER_01]: Goodbye world\n"
            "\n",
            encoding="utf-8",
        )
        segments = read_speaker_segments_from_srt(str(srt))
        assert len(segments) == 2
        assert segments[0]["speaker"] == "SPEAKER_00"
        assert abs(segments[0]["start"] - 1.0) < 0.01
        assert segments[1]["speaker"] == "SPEAKER_01"

    def test_srt_without_speakers(self, tmp_path):
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n"
            "00:00:01,000 --> 00:00:05,000\n"
            "Hello world\n"
            "\n",
            encoding="utf-8",
        )
        assert read_speaker_segments_from_srt(str(srt)) == []


# ---------------------------------------------------------------------------
# read_speaker_segments_from_txt
# ---------------------------------------------------------------------------

class TestReadSpeakerSegmentsFromTxt:
    def test_nonexistent_file(self, tmp_path):
        assert read_speaker_segments_from_txt(str(tmp_path / "nope.txt")) == []

    def test_none_path(self):
        assert read_speaker_segments_from_txt(None) == []

    def test_valid_txt_with_speakers(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text(
            "[0:00.0 --> 0:05.0] [SPEAKER_00]: Hello world\n"
            "[0:05.0 --> 0:10.0] [SPEAKER_01]: Goodbye world\n",
            encoding="utf-8",
        )
        segments = read_speaker_segments_from_txt(str(txt))
        assert len(segments) == 2
        assert segments[0]["speaker"] == "SPEAKER_00"
        assert segments[1]["speaker"] == "SPEAKER_01"

    def test_txt_without_speakers(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text(
            "[0:00.0 --> 0:05.0] Hello world\n",
            encoding="utf-8",
        )
        assert read_speaker_segments_from_txt(str(txt)) == []


# ---------------------------------------------------------------------------
# replace_speaker_labels_in_text
# ---------------------------------------------------------------------------

class TestReplaceSpeakerLabelsInText:
    def test_nonexistent_file(self, tmp_path):
        assert replace_speaker_labels_in_text(str(tmp_path / "nope.txt"), {"X": "Y"}) is False

    def test_none_path(self):
        assert replace_speaker_labels_in_text(None, {"X": "Y"}) is False

    def test_replaces_labels(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("[SPEAKER_00]: Hello\n[SPEAKER_01]: World\n", encoding="utf-8")
        mapping = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        assert replace_speaker_labels_in_text(str(f), mapping) is True
        content = f.read_text(encoding="utf-8")
        assert "[Alice]:" in content
        assert "[Bob]:" in content
        assert "[SPEAKER_00]:" not in content

    def test_no_match_returns_false(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("No speakers here\n", encoding="utf-8")
        assert replace_speaker_labels_in_text(str(f), {"SPEAKER_00": "Alice"}) is False

    def test_partial_mapping(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("[SPEAKER_00]: Hello\n[SPEAKER_01]: World\n", encoding="utf-8")
        mapping = {"SPEAKER_00": "Alice"}
        assert replace_speaker_labels_in_text(str(f), mapping) is True
        content = f.read_text(encoding="utf-8")
        assert "[Alice]:" in content
        assert "[SPEAKER_01]:" in content


# ---------------------------------------------------------------------------
# apply_speaker_names_to_json
# ---------------------------------------------------------------------------

class TestApplySpeakerNamesToJson:
    def test_nonexistent_file(self, tmp_path):
        assert apply_speaker_names_to_json(str(tmp_path / "nope.json"), {}) is False

    def test_applies_names(self, tmp_path):
        f = tmp_path / "out.json"
        data = {"segments": [
            {"start": 0, "end": 5, "speaker": "SPEAKER_00", "text": "Hello"},
            {"start": 5, "end": 10, "speaker": "SPEAKER_01", "text": "World"},
        ]}
        f.write_text(json.dumps(data), encoding="utf-8")
        mapping = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        assert apply_speaker_names_to_json(str(f), mapping) is True
        result = json.loads(f.read_text(encoding="utf-8"))
        assert result["segments"][0]["speaker_name"] == "Alice"
        assert result["segments"][1]["speaker_name"] == "Bob"

    def test_no_speakers_returns_false(self, tmp_path):
        f = tmp_path / "out.json"
        data = {"segments": [{"start": 0, "end": 5, "text": "Hello"}]}
        f.write_text(json.dumps(data), encoding="utf-8")
        assert apply_speaker_names_to_json(str(f), {"SPEAKER_00": "Alice"}) is False

    def test_list_format(self, tmp_path):
        f = tmp_path / "out.json"
        data = [{"start": 0, "end": 5, "speaker": "SPEAKER_00", "text": "Hello"}]
        f.write_text(json.dumps(data), encoding="utf-8")
        assert apply_speaker_names_to_json(str(f), {"SPEAKER_00": "Alice"}) is True
        result = json.loads(f.read_text(encoding="utf-8"))
        assert result[0]["speaker_name"] == "Alice"


# ---------------------------------------------------------------------------
# inject_speakers_into_json
# ---------------------------------------------------------------------------

class TestInjectSpeakersIntoJson:
    def test_nonexistent_file(self, tmp_path):
        assert inject_speakers_into_json(str(tmp_path / "nope.json")) is False

    def test_injects_from_srt(self, tmp_path):
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n"
            "00:00:00,000 --> 00:00:05,000\n"
            "[SPEAKER_00]: Hello\n"
            "\n"
            "2\n"
            "00:00:05,000 --> 00:00:10,000\n"
            "[SPEAKER_01]: World\n"
            "\n",
            encoding="utf-8",
        )
        jf = tmp_path / "test.json"
        data = {"segments": [
            {"start": 0, "end": 5, "text": "Hello"},
            {"start": 5, "end": 10, "text": "World"},
        ]}
        jf.write_text(json.dumps(data), encoding="utf-8")
        assert inject_speakers_into_json(str(jf)) is True
        result = json.loads(jf.read_text(encoding="utf-8"))
        assert result["segments"][0]["speaker"] == "SPEAKER_00"
        assert result["segments"][1]["speaker"] == "SPEAKER_01"

    def test_skips_segments_with_speaker(self, tmp_path):
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n"
            "00:00:00,000 --> 00:00:05,000\n"
            "[SPEAKER_00]: Hello\n"
            "\n",
            encoding="utf-8",
        )
        jf = tmp_path / "test.json"
        data = {"segments": [
            {"start": 0, "end": 5, "text": "Hello", "speaker": "SPEAKER_99"},
        ]}
        jf.write_text(json.dumps(data), encoding="utf-8")
        assert inject_speakers_into_json(str(jf)) is False

    def test_no_companion_files(self, tmp_path):
        jf = tmp_path / "test.json"
        data = {"segments": [{"start": 0, "end": 5, "text": "Hello"}]}
        jf.write_text(json.dumps(data), encoding="utf-8")
        assert inject_speakers_into_json(str(jf)) is False


# ---------------------------------------------------------------------------
# create_sentences_only
# ---------------------------------------------------------------------------

class TestCreateSentencesOnly:
    def test_missing_file(self, tmp_path):
        success, warning = create_sentences_only(
            str(tmp_path / "nope.txt"), str(tmp_path / "out.txt")
        )
        assert success is False
        assert "not found" in warning

    def test_empty_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("", encoding="utf-8")
        success, warning = create_sentences_only(str(src), str(tmp_path / "out.txt"))
        assert success is False
        assert "empty" in warning.lower()

    def test_no_timestamps(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("Just plain text\nNo timestamps here\n", encoding="utf-8")
        success, warning = create_sentences_only(str(src), str(tmp_path / "out.txt"))
        assert success is False
        assert "No timestamped content" in warning

    def test_basic_extraction(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text(
            "[00:00.0 --> 00:05.0] Hello world\n"
            "[00:05.0 --> 00:10.0] This is a test\n",
            encoding="utf-8",
        )
        out = tmp_path / "out.txt"
        success, warning = create_sentences_only(str(src), str(out))
        assert success is True
        assert warning is None
        content = out.read_text(encoding="utf-8")
        assert "Hello world" in content
        assert "This is a test" in content
        assert "[" not in content

    def test_speaker_diarization(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text(
            "[00:00.0 --> 00:05.0] [SPEAKER_00]: Hello\n"
            "[00:05.0 --> 00:10.0] [SPEAKER_00]: world\n"
            "[00:10.0 --> 00:15.0] [SPEAKER_01]: Goodbye\n",
            encoding="utf-8",
        )
        out = tmp_path / "out.txt"
        success, warning = create_sentences_only(str(src), str(out))
        assert success is True
        content = out.read_text(encoding="utf-8")
        assert "[SPEAKER_00]: Hello world" in content
        assert "[SPEAKER_01]: Goodbye" in content


# ---------------------------------------------------------------------------
# build_lrc_lines
# ---------------------------------------------------------------------------

class TestBuildLrcLines:
    def test_empty_segments(self):
        assert build_lrc_lines([]) == []

    def test_segment_with_text_only(self):
        segments = [{"start": 60, "text": "Hello world"}]
        lines = build_lrc_lines(segments)
        assert len(lines) == 1
        assert lines[0] == "[01:00.00]Hello world"

    def test_segment_with_words(self):
        segments = [{
            "start": 0,
            "words": [
                {"word": "Hello", "start": 0},
                {"word": "world", "start": 1.5},
            ],
        }]
        lines = build_lrc_lines(segments)
        assert len(lines) == 1
        assert "<00:00.00>Hello" in lines[0]
        assert "<00:01.50>world" in lines[0]

    def test_empty_text_skipped(self):
        segments = [{"start": 0, "text": ""}]
        assert build_lrc_lines(segments) == []

    def test_non_dict_skipped(self):
        segments = ["not a dict", {"start": 0, "text": "OK"}]
        lines = build_lrc_lines(segments)
        assert len(lines) == 1

    def test_words_with_empty_entries(self):
        segments = [{
            "start": 0,
            "words": [
                {"word": "", "start": 0},
                {"word": "Hello", "start": 1},
            ],
        }]
        lines = build_lrc_lines(segments)
        assert len(lines) == 1
        assert "Hello" in lines[0]

    def test_words_all_empty_falls_back_to_text(self):
        segments = [{
            "start": 0,
            "text": "Fallback text",
            "words": [{"word": "", "start": 0}],
        }]
        lines = build_lrc_lines(segments)
        assert len(lines) == 1
        assert "Fallback text" in lines[0]


# ---------------------------------------------------------------------------
# extra_args_has_flag
# ---------------------------------------------------------------------------

class TestExtraArgsHasFlag:
    def test_empty_text(self):
        assert extra_args_has_flag("--diarize", "") is False
        assert extra_args_has_flag("--diarize", None) is False

    def test_present_standalone(self):
        assert extra_args_has_flag("--diarize", "--diarize") is True

    def test_present_with_other_args(self):
        assert extra_args_has_flag("--diarize", "--model large --diarize --beam_size 5") is True

    def test_present_with_value(self):
        assert extra_args_has_flag("--language", "--language=en") is True

    def test_not_present(self):
        assert extra_args_has_flag("--diarize", "--model large") is False

    def test_substring_not_matched(self):
        assert extra_args_has_flag("--dia", "--diarize") is False


# ---------------------------------------------------------------------------
# expected_output_suffixes
# ---------------------------------------------------------------------------

class TestExpectedOutputSuffixes:
    def test_single_format(self):
        assert expected_output_suffixes(["srt"]) == [".srt"]

    def test_multiple_formats_sorted_and_deduped(self):
        assert expected_output_suffixes(["vtt", "json", "vtt"]) == [".json", ".vtt"]

    def test_txt_variants_map_to_distinct_files(self):
        assert expected_output_suffixes(["txt (with timestamps)"]) == [".txt"]
        assert expected_output_suffixes(["txt (sentences only)"]) == ["_sentences.txt"]

    def test_both_txt_variants(self):
        assert expected_output_suffixes(
            ["txt (with timestamps)", "txt (sentences only)"]
        ) == [".txt", "_sentences.txt"]

    def test_all_expands_to_every_written_format(self):
        assert expected_output_suffixes(["all"]) == [
            ".json", ".lrc", ".srt", ".tsv", ".txt", ".vtt"
        ]

    def test_all_wins_over_other_selections(self):
        assert expected_output_suffixes(["srt", "all"]) == expected_output_suffixes(["all"])

    def test_empty_selection_defaults_to_srt(self):
        # Mirrors build_command, which falls back to srt when nothing is checked.
        assert expected_output_suffixes([]) == [".srt"]
        assert expected_output_suffixes(None) == [".srt"]

    def test_unknown_format_ignored(self):
        assert expected_output_suffixes(["bogus"]) == [".srt"]
        assert expected_output_suffixes(["bogus", "vtt"]) == [".vtt"]


# ---------------------------------------------------------------------------
# expected_output_paths / find_existing_outputs
# ---------------------------------------------------------------------------

class TestExpectedOutputPaths:
    def test_builds_paths_from_basename(self, tmp_path):
        paths = expected_output_paths(str(tmp_path), "video", ["srt", "json"])
        assert paths == [
            os.path.join(str(tmp_path), "video.json"),
            os.path.join(str(tmp_path), "video.srt"),
        ]

    def test_sentences_suffix_has_no_extra_dot(self, tmp_path):
        paths = expected_output_paths(str(tmp_path), "video", ["txt (sentences only)"])
        assert paths == [os.path.join(str(tmp_path), "video_sentences.txt")]

    def test_missing_inputs_yield_nothing(self, tmp_path):
        assert expected_output_paths("", "video", ["srt"]) == []
        assert expected_output_paths(str(tmp_path), "", ["srt"]) == []


class TestFindExistingOutputs:
    def test_nothing_exists(self, tmp_path):
        existing, missing = find_existing_outputs(str(tmp_path), "video", ["srt"])
        assert existing == []
        assert len(missing) == 1

    def test_all_exist_means_safe_to_skip(self, tmp_path):
        (tmp_path / "video.srt").write_text("x")
        (tmp_path / "video.json").write_text("x")
        existing, missing = find_existing_outputs(str(tmp_path), "video", ["srt", "json"])
        assert len(existing) == 2
        assert missing == []

    def test_partial_outputs_are_not_skippable(self, tmp_path):
        # An interrupted run leaves some formats behind; it must be redone.
        (tmp_path / "video.srt").write_text("x")
        existing, missing = find_existing_outputs(str(tmp_path), "video", ["srt", "json"])
        assert [os.path.basename(p) for p in existing] == ["video.srt"]
        assert [os.path.basename(p) for p in missing] == ["video.json"]

    def test_suffixed_basename_checked_independently(self, tmp_path):
        # video.srt existing must not make video_mp4 look complete.
        (tmp_path / "video.srt").write_text("x")
        existing, missing = find_existing_outputs(str(tmp_path), "video_mp4", ["srt"])
        assert existing == []
        assert len(missing) == 1

    def test_sentences_only_run_ignores_plain_txt(self, tmp_path):
        (tmp_path / "video.txt").write_text("x")
        existing, missing = find_existing_outputs(
            str(tmp_path), "video", ["txt (sentences only)"]
        )
        assert existing == []
        assert [os.path.basename(p) for p in missing] == ["video_sentences.txt"]
