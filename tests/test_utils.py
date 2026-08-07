import os
import re
import json
import pytest

import utils
from config import ENGINE_ARCHIVES

from utils import (
    format_path_for_display,
    normalize_path_signature,
    redact_path_text,
    looks_like_path_token,
    sanitize_command_display,
    is_windows_path,
    windows_to_posix_path,
    sanitize_model_name,
    parse_hf_repo_id,
    detect_model_arch_from_dir,
    filter_verbose_output,
    extract_links_from_text,
    normalize_version,
    version_tuple,
    text_indicates_transcription_success,
    find_7zip,
    is_safe_download_filename,
    is_within_directory,
)


# ---------------------------------------------------------------------------
# Existing tests (format_path_for_display, normalize_path_signature)
# ---------------------------------------------------------------------------

def test_format_path_for_display_normalizes_separators():
    path = "C:\\Users\\Example\\file.txt"
    result = format_path_for_display(path)
    assert "\\" not in result
    assert "Users/Example/file.txt" in result


def test_normalize_path_signature_lowercases_and_normalizes(tmp_path):
    file_path = tmp_path / "SubDir" / "File.TXT"
    file_path.parent.mkdir()
    file_path.write_text("data", encoding="utf-8")

    normalized = normalize_path_signature(str(file_path))
    assert normalized.endswith("/subdir/file.txt")
    assert normalized == normalized.lower()


# ---------------------------------------------------------------------------
# redact_path_text
# ---------------------------------------------------------------------------

class TestRedactPathText:
    def test_none_returns_none(self):
        assert redact_path_text(None) is None

    def test_empty_returns_empty(self):
        assert redact_path_text("") == ""

    def test_no_paths_unchanged(self):
        text = "Just a normal error message"
        assert redact_path_text(text) == text

    def test_unix_path_redacted(self):
        result = redact_path_text("Error at /home/user/file.txt")
        assert "/home/user/file.txt" not in result
        assert "<path>" in result

    def test_windows_drive_path_redacted(self):
        result = redact_path_text("Error at C:\\Users\\foo\\bar.txt")
        assert "C:\\Users" not in result
        assert "<path>" in result


# ---------------------------------------------------------------------------
# looks_like_path_token
# ---------------------------------------------------------------------------

class TestLooksLikePathToken:
    def test_none(self):
        assert looks_like_path_token(None) is False

    def test_empty(self):
        assert looks_like_path_token("") is False

    def test_windows_path(self):
        assert looks_like_path_token("C:\\Users\\foo") is True

    def test_unix_path(self):
        assert looks_like_path_token("/usr/bin/python") is True

    def test_unc_path(self):
        assert looks_like_path_token("\\\\server\\share") is True

    def test_network_path(self):
        assert looks_like_path_token("//server/share") is True

    def test_flag_not_path(self):
        assert looks_like_path_token("--model") is False

    def test_word_not_path(self):
        assert looks_like_path_token("hello") is False


# ---------------------------------------------------------------------------
# sanitize_command_display
# ---------------------------------------------------------------------------

class TestSanitizeCommandDisplay:
    def test_none_command_uses_fallback(self):
        result = sanitize_command_display(None, "some fallback /path/to/foo")
        assert "<path>" in result or "some fallback" in result

    def test_flags_preserved(self):
        result = sanitize_command_display(["--model", "large", "--device", "cuda"])
        assert "--model" in result
        assert "--device" in result

    def test_paths_redacted(self):
        result = sanitize_command_display(["/usr/bin/whisper", "/home/user/audio.wav", "--model", "large"])
        assert "/usr/bin/whisper" not in result
        assert "/home/user/audio.wav" not in result
        assert "<path>" in result
        assert "--model" in result

    def test_empty_command_and_fallback(self):
        assert sanitize_command_display(None, None) == ""
        assert sanitize_command_display(None, "") == ""


# ---------------------------------------------------------------------------
# is_windows_path / windows_to_posix_path
# ---------------------------------------------------------------------------

class TestWindowsPathUtils:
    def test_is_windows_path_true(self):
        assert is_windows_path("C:\\\\Users\\\\foo") is True

    def test_is_windows_path_false_unix(self):
        assert is_windows_path("/home/user") is False

    def test_is_windows_path_false_relative(self):
        assert is_windows_path("relative/path") is False

    def test_windows_to_posix(self):
        result = windows_to_posix_path("C:\\Users\\foo\\bar")
        assert result == "/mnt/c/Users/foo/bar"

    def test_windows_to_posix_lowercase_drive(self):
        result = windows_to_posix_path("D:\\Data")
        assert result.startswith("/mnt/d/")


# ---------------------------------------------------------------------------
# sanitize_model_name
# ---------------------------------------------------------------------------

class TestSanitizeModelName:
    def test_none(self):
        assert sanitize_model_name(None) == ""

    def test_empty(self):
        assert sanitize_model_name("") == ""

    def test_simple_name(self):
        assert sanitize_model_name("large-v3") == "large-v3"

    def test_spaces_replaced(self):
        result = sanitize_model_name("My Custom Model")
        assert " " not in result
        assert result == "my-custom-model"

    def test_special_chars(self):
        result = sanitize_model_name("model@v2!beta#1")
        assert "@" not in result
        assert "!" not in result
        assert "#" not in result

    def test_leading_trailing_stripped(self):
        result = sanitize_model_name("---model---")
        assert result == "model"

    def test_lowercased(self):
        assert sanitize_model_name("Large-V3-Turbo") == "large-v3-turbo"


# ---------------------------------------------------------------------------
# parse_hf_repo_id
# ---------------------------------------------------------------------------

class TestParseHfRepoId:
    def test_none(self):
        assert parse_hf_repo_id(None) is None

    def test_empty(self):
        assert parse_hf_repo_id("") is None
        assert parse_hf_repo_id("   ") is None

    def test_direct_repo_id(self):
        assert parse_hf_repo_id("openai/whisper-large-v3") == "openai/whisper-large-v3"

    def test_https_url(self):
        result = parse_hf_repo_id("https://huggingface.co/openai/whisper-large-v3")
        assert result == "openai/whisper-large-v3"

    def test_url_with_tree(self):
        result = parse_hf_repo_id("https://huggingface.co/openai/whisper-large-v3/tree/main")
        assert result == "openai/whisper-large-v3"

    def test_url_with_blob(self):
        result = parse_hf_repo_id("https://huggingface.co/openai/whisper-large-v3/blob/main/config.json")
        assert result == "openai/whisper-large-v3"

    def test_url_with_query_params(self):
        result = parse_hf_repo_id("https://huggingface.co/openai/whisper-large-v3?foo=bar")
        assert result == "openai/whisper-large-v3"

    def test_single_part_returns_none(self):
        assert parse_hf_repo_id("openai") is None

    def test_non_hf_url_returns_none(self):
        assert parse_hf_repo_id("https://github.com/openai/whisper") is None

    def test_bare_domain(self):
        result = parse_hf_repo_id("huggingface.co/openai/whisper-large-v3")
        assert result == "openai/whisper-large-v3"


# ---------------------------------------------------------------------------
# detect_model_arch_from_dir
# ---------------------------------------------------------------------------

class TestDetectModelArchFromDir:
    def test_none_path(self):
        assert detect_model_arch_from_dir(None) is None

    def test_nonexistent_dir(self, tmp_path):
        assert detect_model_arch_from_dir(str(tmp_path / "nope")) is None

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert detect_model_arch_from_dir(str(d)) is None

    def test_ct2_model(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model.bin").write_bytes(b"data")
        result = detect_model_arch_from_dir(str(d))
        assert result is not None
        assert result["arch"] == "CT2"
        assert result["model_dir"] == str(d)

    def test_transformers_safetensors(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model.safetensors").write_bytes(b"data")
        result = detect_model_arch_from_dir(str(d))
        assert result is not None
        assert result["arch"] == "Transformers"

    def test_transformers_config_only(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
        result = detect_model_arch_from_dir(str(d))
        assert result is not None
        assert result["arch"] == "Transformers"

    def test_transformers_pytorch_bin(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "pytorch_model.bin").write_bytes(b"data")
        result = detect_model_arch_from_dir(str(d))
        assert result is not None
        assert result["arch"] == "Transformers"

    def test_ct2_takes_priority(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model.bin").write_bytes(b"ct2 data")
        (d / "config.json").write_text("{}", encoding="utf-8")
        result = detect_model_arch_from_dir(str(d))
        assert result["arch"] == "CT2"

    def test_sharded_safetensors(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model-00001-of-00002.safetensors").write_bytes(b"data")
        result = detect_model_arch_from_dir(str(d))
        assert result is not None
        assert result["arch"] == "Transformers"


# ---------------------------------------------------------------------------
# filter_verbose_output
# ---------------------------------------------------------------------------

class TestFilterVerboseOutput:
    def test_empty(self):
        assert filter_verbose_output("") == ""

    def test_timestamp_lines_kept(self):
        data = "[0:00.000 --> 0:05.000] Hello world\nSome noise\n"
        result = filter_verbose_output(data)
        assert "[0:00.000 --> 0:05.000]" in result
        assert "Some noise" not in result

    def test_subtitles_written_kept(self):
        data = "noise\nSubtitles are written to output.srt\nmore noise\n"
        result = filter_verbose_output(data)
        assert "Subtitles are written to" in result
        assert "noise" not in result

    def test_no_matches_returns_empty(self):
        assert filter_verbose_output("just some random text\nno timestamps here") == ""


# ---------------------------------------------------------------------------
# extract_links_from_text
# ---------------------------------------------------------------------------

class TestExtractLinksFromText:
    def test_none(self):
        assert extract_links_from_text(None) == []

    def test_empty(self):
        assert extract_links_from_text("") == []

    def test_single_link(self):
        assert extract_links_from_text("https://example.com") == ["https://example.com"]

    def test_multiple_lines(self):
        text = "https://a.com\nhttps://b.com\n\nhttps://c.com"
        assert extract_links_from_text(text) == ["https://a.com", "https://b.com", "https://c.com"]

    def test_whitespace_separated(self):
        text = "https://a.com  https://b.com"
        assert extract_links_from_text(text) == ["https://a.com", "https://b.com"]


# ---------------------------------------------------------------------------
# normalize_version / version_tuple
# ---------------------------------------------------------------------------

class TestVersionUtils:
    def test_normalize_none(self):
        assert normalize_version(None) == ""

    def test_normalize_strips_v(self):
        assert normalize_version("v1.2.3") == "1.2.3"
        assert normalize_version("V2.0") == "2.0"

    def test_normalize_strips_whitespace(self):
        assert normalize_version("  v1.0  ") == "1.0"

    def test_version_tuple_basic(self):
        assert version_tuple("1.2.3") == (1, 2, 3)

    def test_version_tuple_with_prefix(self):
        assert version_tuple("v2024.01.15") == (2024, 1, 15)

    def test_version_tuple_non_numeric(self):
        assert version_tuple("not-a-version") == ()


# ---------------------------------------------------------------------------
# text_indicates_transcription_success
# ---------------------------------------------------------------------------

class TestTextIndicatesTranscriptionSuccess:
    def test_none(self):
        assert text_indicates_transcription_success(None) is False

    def test_empty(self):
        assert text_indicates_transcription_success("") is False

    def test_operation_finished(self):
        assert text_indicates_transcription_success("Operation finished in: 5.2s") is True

    def test_subtitles_written(self):
        assert text_indicates_transcription_success("Subtitles are written to output.srt") is True

    def test_no_match(self):
        assert text_indicates_transcription_success("Processing audio...") is False


# ---------------------------------------------------------------------------
# find_7zip
# ---------------------------------------------------------------------------

class TestFind7zip:
    """The Windows branches cannot run here, so they are driven with stubs."""

    def _windows(self, monkeypatch, tmp_path, registry=None, env=None):
        monkeypatch.setattr(utils.sys, "platform", "win32")
        monkeypatch.setattr(utils.shutil, "which", lambda name: None)
        monkeypatch.setattr(utils, "get_app_directory", lambda: str(tmp_path / "app"))
        monkeypatch.setattr(utils, "_7zip_dirs_from_registry", lambda: registry or [])
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            monkeypatch.delenv(key, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)

    def test_prefers_executable_on_path(self, monkeypatch):
        monkeypatch.setattr(utils.shutil, "which", lambda name: "/usr/bin/7z" if name == "7z" else None)
        assert find_7zip() == "/usr/bin/7z"

    def test_returns_none_when_nothing_found(self, monkeypatch, tmp_path):
        self._windows(monkeypatch, tmp_path)
        assert find_7zip() is None

    def test_finds_install_recorded_in_registry(self, monkeypatch, tmp_path):
        custom = tmp_path / "D_Tools" / "7-Zip"
        custom.mkdir(parents=True)
        (custom / "7z.exe").write_text("")
        self._windows(monkeypatch, tmp_path, registry=[str(custom)])
        assert find_7zip() == str(custom / "7z.exe")

    def test_registry_wins_over_program_files(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom" / "7-Zip"
        custom.mkdir(parents=True)
        (custom / "7z.exe").write_text("")
        progfiles = tmp_path / "Program Files"
        (progfiles / "7-Zip").mkdir(parents=True)
        (progfiles / "7-Zip" / "7z.exe").write_text("")
        self._windows(monkeypatch, tmp_path, registry=[str(custom)],
                      env={"ProgramFiles": str(progfiles)})
        assert find_7zip() == str(custom / "7z.exe")

    def test_finds_per_user_install(self, monkeypatch, tmp_path):
        local = tmp_path / "LocalAppData"
        target = local / "Programs" / "7-Zip"
        target.mkdir(parents=True)
        (target / "7z.exe").write_text("")
        self._windows(monkeypatch, tmp_path, env={"LOCALAPPDATA": str(local)})
        assert find_7zip() == str(target / "7z.exe")

    def test_falls_back_to_7zr_in_install_dir(self, monkeypatch, tmp_path):
        progfiles = tmp_path / "Program Files"
        target = progfiles / "7-Zip"
        target.mkdir(parents=True)
        (target / "7zr.exe").write_text("")
        self._windows(monkeypatch, tmp_path, env={"ProgramFiles": str(progfiles)})
        assert find_7zip() == str(target / "7zr.exe")

    def test_registry_helper_is_noop_off_windows(self, monkeypatch):
        monkeypatch.setattr(utils.sys, "platform", "linux")
        assert utils._7zip_dirs_from_registry() == []


# ---------------------------------------------------------------------------
# is_safe_download_filename / is_within_directory
# ---------------------------------------------------------------------------

class TestIsSafeDownloadFilename:
    @pytest.mark.parametrize("name", [
        "model.bin",
        "config.json",
        "model-00001-of-00002.safetensors",
        "tokenizer.model",
    ])
    def test_plain_filenames_allowed(self, name):
        assert is_safe_download_filename(name) is True

    @pytest.mark.parametrize("name", [
        "../../etc/passwd",
        "..\\..\\..\\Windows\\System32\\evil.dll",
        "sub/dir/model.bin",
        "sub\\dir\\model.bin",
        "C:evil.dll",
        "model.bin:hidden",
        "..",
        ".",
        "",
        None,
        "nul\0byte",
    ])
    def test_path_components_rejected(self, name):
        assert is_safe_download_filename(name) is False

    def test_backslash_rejected_on_every_platform(self):
        """The Windows separator must be rejected even when running on Linux."""
        assert is_safe_download_filename("..\\..\\evil.dll") is False


class TestIsWithinDirectory:
    def test_child_is_inside(self, tmp_path):
        assert is_within_directory(str(tmp_path), str(tmp_path / "model.bin")) is True

    def test_nested_child_is_inside(self, tmp_path):
        assert is_within_directory(str(tmp_path), str(tmp_path / "a" / "b.bin")) is True

    def test_parent_is_outside(self, tmp_path):
        assert is_within_directory(str(tmp_path / "models"), str(tmp_path / "evil.dll")) is False

    def test_traversal_is_outside(self, tmp_path):
        target = os.path.join(str(tmp_path), "..", "..", "evil.dll")
        assert is_within_directory(str(tmp_path), target) is False

    def test_directory_itself_counts_as_inside(self, tmp_path):
        assert is_within_directory(str(tmp_path), str(tmp_path)) is True

    def test_sibling_with_shared_prefix_is_outside(self, tmp_path):
        assert is_within_directory(str(tmp_path / "models"), str(tmp_path / "models-evil" / "x")) is False


# ---------------------------------------------------------------------------
# Pinned engine archives
# ---------------------------------------------------------------------------

class TestEngineArchivePinning:
    """A placeholder or malformed hash would break setup for every user."""

    @pytest.mark.parametrize("platform", ["windows", "linux"])
    def test_entry_exists(self, platform):
        assert platform in ENGINE_ARCHIVES

    @pytest.mark.parametrize("platform", ["windows", "linux"])
    def test_sha256_is_a_real_digest(self, platform):
        digest = ENGINE_ARCHIVES[platform]["sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{platform}: {digest!r}"

    @pytest.mark.parametrize("platform", ["windows", "linux"])
    def test_url_is_https(self, platform):
        assert ENGINE_ARCHIVES[platform]["url"].startswith("https://")

    @pytest.mark.parametrize("platform", ["windows", "linux"])
    def test_size_is_positive(self, platform):
        assert ENGINE_ARCHIVES[platform]["size"] > 0

    def test_platforms_do_not_share_a_hash(self):
        assert ENGINE_ARCHIVES["windows"]["sha256"] != ENGINE_ARCHIVES["linux"]["sha256"]
