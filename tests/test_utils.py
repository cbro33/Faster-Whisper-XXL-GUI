import os

from utils import format_path_for_display, normalize_path_signature


def test_format_path_for_display_normalizes_separators():
    path = "C:\\Users\\Example\\file.txt"
    assert format_path_for_display(path) == "C:/Users/Example/file.txt"


def test_normalize_path_signature_lowercases_and_normalizes(tmp_path):
    file_path = tmp_path / "SubDir" / "File.TXT"
    file_path.parent.mkdir()
    file_path.write_text("data", encoding="utf-8")

    normalized = normalize_path_signature(str(file_path))
    assert normalized.endswith("/subdir/file.txt")
    assert normalized == normalized.lower()
