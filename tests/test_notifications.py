"""Operator-facing notifications: the Telegram notifier and the Telegram bot menu.

Sections:
  TestNotifier       bot.notifier: system log / alert state, rate limiting, close messages
  TestTelegramMenu   bot.telegram_menu: owner / viewer roles and write-action gating
  TestTelegramViews  bot.telegram_views: rendered text and inline-keyboard callbacks
"""
import json
from unittest.mock import MagicMock, patch

from bot.notifier import Notifier
from tests.factories import run_coro
from bot.telegram_menu import TelegramMenu
from bot.telegram_views import (
    render_access_request,
    render_backtest_symbol,
    render_confirm_pause,
    render_controls,
    render_main_menu,
    render_status,
    render_symbol_active,
    render_symbol_disabled,
    render_virtual_symbol,
)


# ────────────────────────── notifier ────────────────────────── #

def _make_notifier(tmp_path):
    return Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="",
        telegram_chat_id="",
    )


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


def _close_text(mock_post):
    return mock_post.call_args[1]["json"]["text"]


class TestNotifier:
    def test_info_writes_log_not_alert(self, tmp_path):
        n = _make_notifier(tmp_path)
        n.notify("info", "Boot", "started", "main")
        log = json.loads((tmp_path / "system_log.json").read_text())
        assert len(log) == 1
        assert log[0]["level"] == "info"
        # alert_state should not be created for info
        assert not (tmp_path / "alert_state.json").exists()

    def test_emergency_writes_log_and_alert(self, tmp_path):
        n = _make_notifier(tmp_path)
        n.notify("emergency", "Crash", "details", "main")
        alert_state = json.loads((tmp_path / "alert_state.json").read_text())
        assert len(alert_state["alerts"]) == 1
        assert alert_state["alerts"][0]["level"] == "emergency"
        assert "dismissed_ids" in alert_state

    def test_warning_writes_alert(self, tmp_path):
        n = _make_notifier(tmp_path)
        n.notify("warning", "Warn", "details", "main")
        alert_state = json.loads((tmp_path / "alert_state.json").read_text())
        assert len(alert_state["alerts"]) == 1

    def test_notify_never_throws_on_telegram_error(self, tmp_path):
        n = Notifier(
            log_path=tmp_path / "system_log.json",
            alert_path=tmp_path / "alert_state.json",
            telegram_token="bad_token",
            telegram_chat_id="12345",
        )
        # Mock requests.post to raise immediately — avoids real network call and slow timeouts
        with patch("requests.post", side_effect=ConnectionError("network unreachable")):
            n.notify("emergency", "Test", "body", "test")

    def test_dismiss_removes_id(self, tmp_path):
        n = _make_notifier(tmp_path)
        n.notify("emergency", "Alert", "body", "src")
        state = json.loads((tmp_path / "alert_state.json").read_text())
        alert_id = state["alerts"][0]["id"]
        n.dismiss(alert_id)
        state2 = json.loads((tmp_path / "alert_state.json").read_text())
        assert alert_id in state2["dismissed_ids"]

    def test_dismiss_is_idempotent(self, tmp_path):
        n = _make_notifier(tmp_path)
        n.notify("emergency", "Alert", "body", "src")
        state = json.loads((tmp_path / "alert_state.json").read_text())
        alert_id = state["alerts"][0]["id"]
        n.dismiss(alert_id)
        n.dismiss(alert_id)  # second dismiss
        state2 = json.loads((tmp_path / "alert_state.json").read_text())
        assert state2["dismissed_ids"].count(alert_id) == 1

    def test_corrupt_alert_state_is_reset(self, tmp_path):
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

    def test_send_test_no_credentials(self, tmp_path):
        n = _make_notifier(tmp_path)
        ok, msg = n.send_test()
        assert ok is False
        assert "not configured" in msg

    # ── Rate limiting ───────────────────────────────────────────────────── #

    def test_closes_on_different_symbols_both_send(self, tmp_path):
        """Session 42 moved trade closes to per-symbol keys; session 63 replaced the
        time throttle with content dedup. Either way, two different symbols closing
        together must both notify — this previously asserted the opposite."""
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close("BTCUSDT", "BUY", 10.0, 68000.0, 68500.0, "preset_a", balance_after=100.0)
            n.notify_trade_close("ETHUSDT", "SELL", -5.0, 3200.0, 3250.0, "preset_b", balance_after=95.0)
        assert mock_post.call_count == 2

    def test_non_trade_alerts_still_rate_limited(self, tmp_path):
        """The generic time throttle still applies to ordinary warnings, which can
        repeat every candle; only trade closes are exempt."""
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify("warning", "Same warning", "same body", "test")
            n.notify("warning", "Same warning", "same body", "test")
        assert mock_post.call_count == 1

    def test_emergency_bypasses_rate_limit(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify("emergency", "Alert 1", "body", "test")
            n.notify("emergency", "Alert 2", "body", "test")
        assert mock_post.call_count == 2

    # ── Message format ──────────────────────────────────────────────────── #

    def test_trade_close_win_format(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close("BTCUSDT", "BUY", 12.34, 68000.0, 68500.0, "trail_15", balance_after=1234.56)
        text = mock_post.call_args[1]["json"]["text"]
        assert "Win" in text
        assert "BTCUSDT" in text
        assert "+12.34" in text
        assert "trail_15" in text
        assert "1,234.56" in text

    def test_trade_close_loss_format(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close("ETHUSDT", "SELL", -5.20, 3200.0, 3220.0, "trail_15", balance_after=994.80)
        text = mock_post.call_args[1]["json"]["text"]
        assert "Loss" in text
        assert "ETHUSDT" in text
        assert "5.20" in text
        assert "994.80" in text

    # ── Before / Net / Fee / After reporting ─────────────────────────────── #
    # The Aug-19 incident: the "Balance" line carried a pre-close wallet read on
    # two of four messages because main.py served it from a 5s cache populated
    # before the close settled. The message now names both sides explicitly and
    # must print "n/a" rather than any number it cannot vouch for.

    def test_trade_close_reports_before_net_fee_after(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close(
                "INJUSDT", "BUY", 18.03, 4.082, 4.134, "oscillating_zone",
                balance_before=3050.18, balance_after=3068.06, fee_usdt=1.2163,
            )
        text = _close_text(mock_post)
        assert "Before: 3,050.18 USDT" in text
        assert "Net PnL: <b>+18.03 USDT</b>" in text
        assert "Fee: 1.2163 USDT" in text
        assert "After: 3,068.06 USDT" in text

    def test_before_and_after_appear_in_that_order(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close(
                "INJUSDT", "BUY", 18.03, 4.082, 4.134, "oscillating_zone",
                balance_before=3050.18, balance_after=3068.06, fee_usdt=1.2163,
            )
        text = _close_text(mock_post)
        assert text.index("Before:") < text.index("Net PnL:") < text.index("Fee:") < text.index("After:")

    def test_unavailable_after_balance_prints_na_not_a_stale_number(self, tmp_path):
        """0.0 means 'the wallet read failed'. Showing the pre-close figure there
        is the exact bug this replaces, so nothing numeric may be substituted."""
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close(
                "INJUSDT", "BUY", 49.10, 4.216, 4.354, "oscillating_zone",
                balance_before=3104.47, balance_after=0.0, fee_usdt=1.2374,
            )
        text = _close_text(mock_post)
        assert "After: n/a" in text
        assert "3,104.47" in text          # Before is known and still shown
        assert "After: 3,104.47" not in text

    def test_unavailable_before_balance_prints_na(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close(
                "INJUSDT", "BUY", 49.10, 4.216, 4.354, "oscillating_zone",
                balance_before=0.0, balance_after=3153.21, fee_usdt=1.2374,
            )
        text = _close_text(mock_post)
        assert "Before: n/a" in text
        assert "After: 3,153.21 USDT" in text

    def test_net_pnl_is_labelled_net_of_fee(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close(
                "INJUSDT", "BUY", 18.03, 4.082, 4.134, "oscillating_zone",
                balance_before=3050.18, balance_after=3068.06, fee_usdt=1.2163,
            )
        text = _close_text(mock_post)
        assert "net of fee" in text

    def test_emergency_includes_mention(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify("emergency", "Crash", "details", "main")
        text = mock_post.call_args[1]["json"]["text"]
        assert "@bo_pal" in text

    # ── send_test ───────────────────────────────────────────────────────── #

    def test_send_test_unknown_type(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        ok, err = n.send_test("foobar")
        assert ok is False
        assert "foobar" in err

    def test_send_test_bypasses_rate_limit(self, tmp_path):
        import time as _time
        n = _make_notifier_with_creds(tmp_path)
        n._last_sent["trade"] = _time.monotonic()  # saturate trade category
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            ok, _ = n.send_test("trade_win")
        assert ok is True
        assert mock_post.call_count == 1

    # ── Trade closes must never be silently dropped ──────────────────────── #
    # A close notification is the user's only real-time record that money moved.
    # The 120s per-symbol throttle could swallow a genuine second close on the same
    # symbol, which reads exactly like "the bot is hiding losing trades".

    def test_two_distinct_closes_on_same_symbol_both_send(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            n.notify_trade_close("INJUSDT", "BUY", 18.03, 4.082, 4.134, "oscillating_zone",
                                 balance_before=3050.18, balance_after=3068.06, fee_usdt=1.21)
            n.notify_trade_close("INJUSDT", "BUY", -5.40, 4.134, 4.100, "oscillating_zone",
                                 balance_before=3068.06, balance_after=3062.66, fee_usdt=1.21)
        assert mock_post.call_count == 2, "second distinct close on same symbol was dropped"

    def test_identical_duplicate_close_is_still_suppressed(self, tmp_path):
        """Protects against a repeat-send bug spamming the channel."""
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()) as mock_post:
            for _ in range(3):
                n.notify_trade_close("INJUSDT", "BUY", 18.03, 4.082, 4.134, "oscillating_zone",
                                     balance_before=3050.18, balance_after=3068.06, fee_usdt=1.21)
        assert mock_post.call_count == 1

    def test_close_is_logged_even_if_telegram_send_is_suppressed(self, tmp_path):
        n = _make_notifier_with_creds(tmp_path)
        with patch("requests.post", return_value=_mock_resp()):
            for _ in range(2):
                n.notify_trade_close("INJUSDT", "BUY", 18.03, 4.082, 4.134, "oscillating_zone",
                                     balance_before=3050.18, balance_after=3068.06, fee_usdt=1.21)
        log = json.loads((tmp_path / "system_log.json").read_text())
        assert sum(1 for e in log if "INJUSDT" in e.get("title", "")) == 2


# ────────────────────────── telegram_menu ────────────────────────── #

def _make_menu(tmp_path, owner_id=111):
    risk_manager = MagicMock()
    risk_manager.snapshot.return_value = {
        "hard_stop_active": False,
        "balance": 1000.0,
        "mode": "test",
    }
    symbol_registry = MagicMock()
    symbol_registry.get_symbols.return_value = ["BTCUSDT"]
    symbol_registry.get_disabled.return_value = {}
    symbol_registry.get_paused_symbols.return_value = {}

    return TelegramMenu(
        token="fake_token",
        owner_chat_id=owner_id,
        risk_manager=risk_manager,
        symbol_registry=symbol_registry,
        project_root=tmp_path,
        get_mode=lambda: "test",
        get_active_symbols=lambda: ["BTCUSDT"],
        get_open_orders=lambda: {},
        rank_max=6,
    )


class TestTelegramMenu:
    def test_owner_role(self, tmp_path):
        menu = _make_menu(tmp_path, owner_id=111)
        assert menu._resolve_role(111) == "owner"

    def test_viewer_role(self, tmp_path):
        viewers_path = tmp_path / "data" / "telegram_viewers.json"
        viewers_path.parent.mkdir(parents=True)
        viewers_path.write_text(json.dumps({
            "viewers": [{"chat_id": 222, "username": "alice", "added_at": "2026-01-01T00:00:00Z"}]
        }))
        menu = _make_menu(tmp_path, owner_id=111)
        assert menu._resolve_role(222) == "viewer"

    def test_unknown_role(self, tmp_path):
        menu = _make_menu(tmp_path, owner_id=111)
        assert menu._resolve_role(999) == "unknown"

    def test_approve_viewer_persists(self, tmp_path):
        menu = _make_menu(tmp_path, owner_id=111)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        menu._pending[333] = "bob"
        menu._approve_viewer(333, "bob")
        assert menu._resolve_role(333) == "viewer"

    def test_revoke_viewer(self, tmp_path):
        viewers_path = tmp_path / "data" / "telegram_viewers.json"
        viewers_path.parent.mkdir(parents=True)
        viewers_path.write_text(json.dumps({
            "viewers": [{"chat_id": 222, "username": "alice", "added_at": "2026-01-01T00:00:00Z"}]
        }))
        menu = _make_menu(tmp_path, owner_id=111)
        menu._revoke_viewer(222)
        assert menu._resolve_role(222) == "unknown"

    def test_viewer_cannot_call_write_action(self, tmp_path):
        viewers_path = tmp_path / "data" / "telegram_viewers.json"
        viewers_path.parent.mkdir(parents=True)
        viewers_path.write_text(json.dumps({
            "viewers": [{"chat_id": 222, "username": "alice", "added_at": "2026-01-01T00:00:00Z"}]
        }))
        menu = _make_menu(tmp_path, owner_id=111)
        # Viewer trying do_reset should be silently ignored (no exception)
        run_coro(menu._dispatch_callback(222, 0, "viewer", "dummy_qid", "do_reset"))
        menu.risk_manager.reset_hard_stop.assert_not_called()


# ────────────────────────── telegram_views ────────────────────────── #

def _buttons(reply_markup):
    """Flatten all button callback_data values into a list."""
    return [
        btn["callback_data"]
        for row in reply_markup["inline_keyboard"]
        for btn in row
    ]


class TestTelegramViews:
    def test_main_menu_owner_has_controls(self):
        text, rm = render_main_menu(is_owner=True)
        assert "controls" in _buttons(rm)
        assert "status" in _buttons(rm)

    def test_main_menu_viewer_no_controls(self):
        text, rm = render_main_menu(is_owner=False)
        assert "controls" not in _buttons(rm)
        assert "status" in _buttons(rm)

    def test_status_shows_hard_stop(self):
        text, rm = render_status(
            mode="live", balance=1234.56, hard_stop_active=True,
            hard_stop_since="2026-05-15T14:45:00Z",
            n_active=12, n_disabled=2, n_paused=1,
            uptime_str="3d 14h",
            last_candle_sym="BTCUSDT", last_candle_ago="2m ago",
        )
        assert "⛔" in text
        assert "1,234.56" in text

    def test_status_clear_hard_stop(self):
        text, rm = render_status(
            mode="test", balance=500.0, hard_stop_active=False,
            hard_stop_since=None,
            n_active=5, n_disabled=0, n_paused=0,
            uptime_str="1h", last_candle_sym=None, last_candle_ago=None,
        )
        assert "✅" in text

    def test_symbols_owner_has_enable_button(self):
        text, rm = render_symbol_disabled(
            symbol="DOGEUSDT", reason="5 consecutive failures",
            disabled_at="2026-05-15T14:45:00Z", is_owner=True,
        )
        assert "do_enable:DOGEUSDT" in _buttons(rm)

    def test_symbols_viewer_no_enable_button(self):
        text, rm = render_symbol_disabled(
            symbol="DOGEUSDT", reason="5 consecutive failures",
            disabled_at="2026-05-15T14:45:00Z", is_owner=False,
        )
        assert "do_enable:DOGEUSDT" not in _buttons(rm)

    def test_symbol_active_owner_has_pause(self):
        text, rm = render_symbol_active(
            symbol="BTCUSDT", price=104200.0,
            best_preset="r5_arm15_cooldown", is_owner=True,
        )
        assert "confirm_pause:BTCUSDT" in _buttons(rm)

    def test_confirm_pause_has_do_pause_and_cancel(self):
        text, rm = render_confirm_pause("BTCUSDT")
        cbs = _buttons(rm)
        assert "do_pause:BTCUSDT" in cbs
        assert "sym:BTCUSDT" in cbs  # cancel goes back to symbol detail

    def test_controls_shows_reset_only_when_active(self):
        text_active, rm_active = render_controls(
            hard_stop_active=True, paused_symbols=[]
        )
        assert "confirm_reset" in _buttons(rm_active)

        text_clear, rm_clear = render_controls(
            hard_stop_active=False, paused_symbols=[]
        )
        assert "confirm_reset" not in _buttons(rm_clear)

    def test_controls_shows_paused_syms_only_when_present(self):
        _, rm_with = render_controls(hard_stop_active=False, paused_symbols=["SOLUSDT"])
        assert "paused_syms" in _buttons(rm_with)

        _, rm_empty = render_controls(hard_stop_active=False, paused_symbols=[])
        assert "paused_syms" not in _buttons(rm_empty)

    def test_virtual_symbol_renders_all_ranks(self):
        ranks = [
            {"rank": 2, "preset_name": "r5_arm15_cooldown", "side": "BUY", "pnl_pct": 0.8, "status": "open"},
            {"rank": 3, "preset_name": "trail_15_full", "side": None, "pnl_pct": None, "status": "none"},
        ]
        text, rm = render_virtual_symbol("BTCUSDT", ranks)
        assert "Rank 2" in text
        assert "Rank 3" in text
        assert "vhist:BTCUSDT" in _buttons(rm)

    def test_backtest_symbol_shows_top5(self):
        top5 = [
            {"name": "r5_arm15_cooldown", "profit_pct": 4.35, "n_trades": 21, "win_rate": 0.571},
            {"name": "r6_arm15_maxp3", "profit_pct": 3.94, "n_trades": 18, "win_rate": 0.611},
        ]
        text, _ = render_backtest_symbol("BTCUSDT", top5)
        assert "r5_arm15_cooldown" in text
        assert "+4.35%" in text

    def test_access_request_has_allow_deny(self):
        text, rm = render_access_request("alice", 111222333)
        cbs = _buttons(rm)
        assert "allow:111222333" in cbs
        assert "deny:111222333" in cbs
