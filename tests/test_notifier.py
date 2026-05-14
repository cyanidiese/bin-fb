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


# ── Rate limiting ───────────────────────────────────────────────────── #

def _make_notifier_with_creds(tmp_path, min_interval_s=120.0):
    return Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="tok",
        telegram_chat_id="123",
        min_interval_s=min_interval_s,
    )


def _mock_resp():
    m = MagicMock()
    m.raise_for_status.return_value = None
    return m


def test_rate_limit_drops_second_trade_message(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("BTCUSDT", "BUY", 10.0, 68000.0, 68500.0, "preset_a", balance_after=100.0)
        n.notify_trade_close("ETHUSDT", "SELL", -5.0, 3200.0, 3250.0, "preset_b", balance_after=95.0)
    assert mock_post.call_count == 1


def test_rate_limit_allows_after_interval(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("BTCUSDT", "BUY", 10.0, 68000.0, 68500.0, "preset_a", balance_after=100.0)
        n._last_sent["trade"] = 0.0  # simulate interval elapsed
        n.notify_trade_close("ETHUSDT", "SELL", -5.0, 3200.0, 3250.0, "preset_b", balance_after=95.0)
    assert mock_post.call_count == 2


def test_emergency_bypasses_rate_limit(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify("emergency", "Alert 1", "body", "test")
        n.notify("emergency", "Alert 2", "body", "test")
    assert mock_post.call_count == 2


# ── Message format ──────────────────────────────────────────────────── #

def test_trade_close_win_format(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("BTCUSDT", "BUY", 12.34, 68000.0, 68500.0, "trail_15", balance_after=1234.56)
    text = mock_post.call_args[1]["json"]["text"]
    assert "Win" in text
    assert "BTCUSDT" in text
    assert "+12.34" in text
    assert "trail_15" in text
    assert "1,234.56" in text


def test_trade_close_loss_format(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("ETHUSDT", "SELL", -5.20, 3200.0, 3220.0, "trail_15", balance_after=994.80)
    text = mock_post.call_args[1]["json"]["text"]
    assert "Loss" in text
    assert "ETHUSDT" in text
    assert "5.20" in text
    assert "994.80" in text


def test_emergency_includes_mention(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify("emergency", "Crash", "details", "main")
    text = mock_post.call_args[1]["json"]["text"]
    assert "@bo_pal" in text


# ── send_test ───────────────────────────────────────────────────────── #

def test_send_test_unknown_type(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    ok, err = n.send_test("foobar")
    assert ok is False
    assert "foobar" in err


def test_send_test_bypasses_rate_limit(tmp_path):
    import time as _time
    n = _make_notifier_with_creds(tmp_path)
    n._last_sent["trade"] = _time.monotonic()  # saturate trade category
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        ok, _ = n.send_test("trade_win")
    assert ok is True
    assert mock_post.call_count == 1
