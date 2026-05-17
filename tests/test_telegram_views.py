# tests/test_telegram_views.py
import pytest
from bot.telegram_views import (
    render_main_menu,
    render_status,
    render_symbols,
    render_symbol_active,
    render_symbol_disabled,
    render_symbol_paused,
    render_confirm_pause,
    render_trades_menu,
    render_real_open,
    render_real_history,
    render_virtual_symbols,
    render_virtual_symbol,
    render_virtual_history,
    render_backtest_symbols,
    render_backtest_symbol,
    render_controls,
    render_confirm_reset,
    render_paused_for_resume,
    render_manage_viewers,
    render_access_request,
)


def _buttons(reply_markup):
    """Flatten all button callback_data values into a list."""
    return [
        btn["callback_data"]
        for row in reply_markup["inline_keyboard"]
        for btn in row
    ]


def test_main_menu_owner_has_controls():
    text, rm = render_main_menu(is_owner=True)
    assert "controls" in _buttons(rm)
    assert "status" in _buttons(rm)


def test_main_menu_viewer_no_controls():
    text, rm = render_main_menu(is_owner=False)
    assert "controls" not in _buttons(rm)
    assert "status" in _buttons(rm)


def test_status_shows_hard_stop():
    text, rm = render_status(
        mode="live", balance=1234.56, hard_stop_active=True,
        hard_stop_since="2026-05-15T14:45:00Z",
        n_active=12, n_disabled=2, n_paused=1,
        uptime_str="3d 14h",
        last_candle_sym="BTCUSDT", last_candle_ago="2m ago",
    )
    assert "⛔" in text
    assert "1,234.56" in text


def test_status_clear_hard_stop():
    text, rm = render_status(
        mode="test", balance=500.0, hard_stop_active=False,
        hard_stop_since=None,
        n_active=5, n_disabled=0, n_paused=0,
        uptime_str="1h", last_candle_sym=None, last_candle_ago=None,
    )
    assert "✅" in text


def test_symbols_owner_has_enable_button():
    text, rm = render_symbol_disabled(
        symbol="DOGEUSDT", reason="5 consecutive failures",
        disabled_at="2026-05-15T14:45:00Z", is_owner=True,
    )
    assert "do_enable:DOGEUSDT" in _buttons(rm)


def test_symbols_viewer_no_enable_button():
    text, rm = render_symbol_disabled(
        symbol="DOGEUSDT", reason="5 consecutive failures",
        disabled_at="2026-05-15T14:45:00Z", is_owner=False,
    )
    assert "do_enable:DOGEUSDT" not in _buttons(rm)


def test_symbol_active_owner_has_pause():
    text, rm = render_symbol_active(
        symbol="BTCUSDT", price=104200.0,
        best_preset="r5_arm15_cooldown", is_owner=True,
    )
    assert "confirm_pause:BTCUSDT" in _buttons(rm)


def test_confirm_pause_has_do_pause_and_cancel():
    text, rm = render_confirm_pause("BTCUSDT")
    cbs = _buttons(rm)
    assert "do_pause:BTCUSDT" in cbs
    assert "sym:BTCUSDT" in cbs  # cancel goes back to symbol detail


def test_controls_shows_reset_only_when_active():
    text_active, rm_active = render_controls(
        hard_stop_active=True, paused_symbols=[]
    )
    assert "confirm_reset" in _buttons(rm_active)

    text_clear, rm_clear = render_controls(
        hard_stop_active=False, paused_symbols=[]
    )
    assert "confirm_reset" not in _buttons(rm_clear)


def test_controls_shows_paused_syms_only_when_present():
    _, rm_with = render_controls(hard_stop_active=False, paused_symbols=["SOLUSDT"])
    assert "paused_syms" in _buttons(rm_with)

    _, rm_empty = render_controls(hard_stop_active=False, paused_symbols=[])
    assert "paused_syms" not in _buttons(rm_empty)


def test_virtual_symbol_renders_all_ranks():
    ranks = [
        {"rank": 2, "preset_name": "r5_arm15_cooldown", "side": "BUY", "pnl_pct": 0.8, "status": "open"},
        {"rank": 3, "preset_name": "trail_15_full", "side": None, "pnl_pct": None, "status": "none"},
    ]
    text, rm = render_virtual_symbol("BTCUSDT", ranks)
    assert "Rank 2" in text
    assert "Rank 3" in text
    assert "vhist:BTCUSDT" in _buttons(rm)


def test_backtest_symbol_shows_top5():
    top5 = [
        {"name": "r5_arm15_cooldown", "profit_pct": 4.35, "n_trades": 21, "win_rate": 0.571},
        {"name": "r6_arm15_maxp3", "profit_pct": 3.94, "n_trades": 18, "win_rate": 0.611},
    ]
    text, _ = render_backtest_symbol("BTCUSDT", top5)
    assert "r5_arm15_cooldown" in text
    assert "+4.35%" in text


def test_access_request_has_allow_deny():
    text, rm = render_access_request("alice", 111222333)
    cbs = _buttons(rm)
    assert "allow:111222333" in cbs
    assert "deny:111222333" in cbs
