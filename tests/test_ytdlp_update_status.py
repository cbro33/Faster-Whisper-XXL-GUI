import json
import time

import ytdlp_utils


def test_load_update_status_recovers_from_corrupt_json(tmp_path, monkeypatch):
    update_file = tmp_path / "ytdlp_updates.json"
    update_file.write_text("not json", encoding="utf-8")

    monkeypatch.setattr(ytdlp_utils, "get_portable_settings_directory", lambda: str(tmp_path))

    data = ytdlp_utils.load_ytdlp_update_status()
    assert "ytdlp_updates" in data
    assert data["ytdlp_updates"]

    saved = json.loads(update_file.read_text(encoding="utf-8"))
    assert "ytdlp_updates" in saved


def test_should_check_update_skips_recent_success(monkeypatch):
    monkeypatch.setattr(
        ytdlp_utils,
        "get_ytdlp_installation_info",
        lambda: {"environment": "source", "version": "2025.12.08"},
    )

    def fake_load():
        return {
            "ytdlp_updates": {
                "source": {
                    "last_updated_version": "2025.12.08",
                    "last_update_timestamp": time.time(),
                }
            }
        }

    monkeypatch.setattr(ytdlp_utils, "load_ytdlp_update_status", fake_load)
    monkeypatch.setattr(ytdlp_utils, "is_within_update_cooldown", lambda *_: True)

    assert ytdlp_utils.should_check_ytdlp_update() is False


def test_should_check_update_clears_failure_on_version_change(monkeypatch):
    saved_payloads = []

    monkeypatch.setattr(
        ytdlp_utils,
        "get_ytdlp_installation_info",
        lambda: {"environment": "source", "version": "2025.12.08"},
    )

    def fake_load():
        return {
            "ytdlp_updates": {
                "source": {
                    "last_failure_version": "2025.12.01",
                    "last_failure_timestamp": time.time(),
                }
            }
        }

    def fake_save(payload):
        saved_payloads.append(payload)

    monkeypatch.setattr(ytdlp_utils, "load_ytdlp_update_status", fake_load)
    monkeypatch.setattr(ytdlp_utils, "save_ytdlp_update_status", fake_save)
    monkeypatch.setattr(ytdlp_utils, "is_within_update_cooldown", lambda *_: False)

    assert ytdlp_utils.should_check_ytdlp_update() is True
    assert saved_payloads, "Expected failure markers to be cleared and saved"
    cleared = saved_payloads[-1]["ytdlp_updates"]["source"]
    assert "last_failure_timestamp" not in cleared
    assert "last_failure_version" not in cleared


def test_record_update_success_clears_failures(monkeypatch):
    data = {"ytdlp_updates": {"source": {"last_failure_version": "2025.12.01"}}}
    saved = []

    monkeypatch.setattr(ytdlp_utils, "get_execution_environment", lambda: "source")
    monkeypatch.setattr(ytdlp_utils, "load_ytdlp_update_status", lambda: data)
    monkeypatch.setattr(ytdlp_utils, "save_ytdlp_update_status", lambda payload: saved.append(payload))

    ytdlp_utils.record_ytdlp_update_success("2025.12.08")

    assert saved
    env_data = saved[-1]["ytdlp_updates"]["source"]
    assert env_data["last_updated_version"] == "2025.12.08"
    assert "last_failure_version" not in env_data


def test_record_update_failure_sets_fields(monkeypatch):
    data = {"ytdlp_updates": {}}
    saved = []

    monkeypatch.setattr(ytdlp_utils, "get_execution_environment", lambda: "source")
    monkeypatch.setattr(ytdlp_utils, "load_ytdlp_update_status", lambda: data)
    monkeypatch.setattr(ytdlp_utils, "save_ytdlp_update_status", lambda payload: saved.append(payload))

    ytdlp_utils.record_ytdlp_update_failure("boom", "2025.12.01")

    assert saved
    env_data = saved[-1]["ytdlp_updates"]["source"]
    assert env_data["last_failure_reason"] == "boom"
    assert env_data["last_failure_version"] == "2025.12.01"
    assert env_data["last_failure_timestamp"] > 0
