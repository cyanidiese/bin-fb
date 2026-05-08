# tests/test_notifier.py
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from bot.notifier import Notifier


def _make_notifier(tmp_path):
    return Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="",
        telegram_chat_id="",
    )


def test_info_writes_log_not_alert(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("info", "Boot", "started", "main")
    log = json.loads((tmp_path / "system_log.json").read_text())
    assert len(log) == 1
    assert log[0]["level"] == "info"
    # alert_state should not be created for info
    assert not (tmp_path / "alert_state.json").exists()


def test_emergency_writes_log_and_alert(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("emergency", "Crash", "details", "main")
    alert_state = json.loads((tmp_path / "alert_state.json").read_text())
    assert len(alert_state["alerts"]) == 1
    assert alert_state["alerts"][0]["level"] == "emergency"
    assert "dismissed_ids" in alert_state


def test_warning_writes_alert(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("warning", "Warn", "details", "main")
    alert_state = json.loads((tmp_path / "alert_state.json").read_text())
    assert len(alert_state["alerts"]) == 1


def test_notify_never_throws_on_telegram_error(tmp_path):
    n = Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="bad_token",
        telegram_chat_id="12345",
    )
    # Mock requests.post to raise immediately — avoids real network call and slow timeouts
    with patch("requests.post", side_effect=ConnectionError("network unreachable")):
        n.notify("emergency", "Test", "body", "test")


def test_dismiss_removes_id(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("emergency", "Alert", "body", "src")
    state = json.loads((tmp_path / "alert_state.json").read_text())
    alert_id = state["alerts"][0]["id"]
    n.dismiss(alert_id)
    state2 = json.loads((tmp_path / "alert_state.json").read_text())
    assert alert_id in state2["dismissed_ids"]
