from types import SimpleNamespace

from ytdlp_utils import parse_version_tuple, read_package_version, get_ytdlp_version_safe


def test_parse_version_tuple():
    assert parse_version_tuple("2025.12.08") == (2025, 12, 8)
    assert parse_version_tuple("") == ()


def test_read_package_version(tmp_path):
    version_file = tmp_path / "version.py"
    version_file.write_text("__version__ = '2025.12.08'\n", encoding="utf-8")
    assert read_package_version(str(tmp_path)) == "2025.12.08"


def test_get_ytdlp_version_safe_prefers_module_version():
    module = SimpleNamespace(__version__="2025.12.08")
    assert get_ytdlp_version_safe(module) == "2025.12.08"


def test_get_ytdlp_version_safe_uses_version_file(tmp_path):
    version_file = tmp_path / "version.py"
    version_file.write_text("__version__ = '2024.04.01'\n", encoding="utf-8")
    module = SimpleNamespace(__file__=str(tmp_path / "__init__.py"))
    assert get_ytdlp_version_safe(module) == "2024.04.01"
