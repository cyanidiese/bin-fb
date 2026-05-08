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


def test_dismiss_is_idempotent(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("emergency", "Alert", "body", "src")
    state = json.loads((tmp_path / "alert_state.json").read_text())
    alert_id = state["alerts"][0]["id"]
    n.dismiss(alert_id)
    n.dismiss(alert_id)  # second dismiss
    state2 = json.loads((tmp_path / "alert_state.json").read_text())
    assert state2["dismissed_ids"].count(alert_id) == 1


def test_corrupt_alert_state_is_reset(tmp_path):
    alert_path = tmp_path / "alert_state.json"
    alert_path.write_text("{{broken json")
    n = Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=alert_path,
        telegram_token="",
        telegram_chat_id="",
    )
    n.notify("warning", "After corrupt", "detail", "test")
    state = json.loads(alert_path.read_text())
    assert len(state["alerts"]) == 1


def test_send_test_no_credentials(tmp_path):
    n = _make_notifier(tmp_path)
    ok, msg = n.send_test()
    assert ok is False
    assert "not configured" in msg
