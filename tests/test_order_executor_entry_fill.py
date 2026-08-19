"""PnL and fee must be computed off the price the entry actually filled at.

Incident (2026-08-18/19, INJUSDT): four real orders were reported as +11.81,
+18.03, +36.78, +49.10 = +115.72 USDT. Binance's income ledger showed +109.27.
The whole 6.46 USDT gap came from trade #1, signalled at 4.052 but filled at
4.0670 — _calc_pnl used the signalled price, so 1.5 cents of entry slippage on
372.1 units silently became 5.57 USDT of phantom profit.
"""
import asyncio
import types

import pytest

from bot.order_executor import OpenOrder, OrderExecutor


def _run(coro):
    """Drive a coroutine without disturbing the shared event loop (see
    tests/test_order_executor_balance.py for why this matters)."""
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def _order(**kw):
    base = dict(
        symbol='INJUSDT', preset_name='oscillating_zone', side='BUY',
        entry_price=4.052, tp_price=4.16, sl_price=4.02,
        quantity=372.1, leverage=5,
    )
    base.update(kw)
    return OpenOrder(**base)


# ── _effective_entry ────────────────────────────────────────────────────── #

def test_effective_entry_prefers_the_real_fill():
    order = _order(fill_entry_price=4.0670)
    assert OrderExecutor._effective_entry(order) == 4.0670


def test_effective_entry_falls_back_to_signalled_price_when_unreconciled():
    assert OrderExecutor._effective_entry(_order()) == 4.052


def test_effective_entry_treats_zero_fill_as_unreconciled():
    assert OrderExecutor._effective_entry(_order(fill_entry_price=0.0)) == 4.052


# ── PnL against the real incident numbers ───────────────────────────────── #

def test_pnl_off_the_real_fill_matches_binance_ledger():
    """Binance realized +7.4537 gross on this close, 0.6053+0.6083 commission,
    so the true net was +6.2401 USDT. Reconciled PnL lands within ~0.01 of that
    (residual is the 1.3-unit 4.0580 partial the weighted average smooths over),
    versus 5.57 USDT of overstatement before the fix."""
    order = _order(fill_entry_price=4.0670)
    pnl = OrderExecutor._calc_pnl(order, 4.0870)
    assert pnl == pytest.approx(6.2401, abs=0.02)


def test_pnl_off_the_signalled_price_reproduces_the_overstatement():
    """Guards the regression: without reconciliation the same close reports +11.81."""
    pnl = OrderExecutor._calc_pnl(_order(), 4.0870)
    assert pnl == pytest.approx(11.81, abs=0.01)


def test_sell_side_uses_the_fill_too():
    order = _order(side='SELL', entry_price=4.052, fill_entry_price=4.0670)
    # A short filled 1.5c higher than signalled earns more, not less.
    assert (
        OrderExecutor._calc_pnl(order, 4.0)
        > OrderExecutor._calc_pnl(_order(side='SELL'), 4.0)
    )


def test_fee_is_charged_on_the_filled_notional():
    fee = OrderExecutor._order_fee(372.1, 4.0670, 4.0870)
    # Binance charged 0.6032+0.0021 in and 0.5400+0.0683 out = 1.2136.
    assert fee == pytest.approx(1.2136, abs=0.002)


# ── _reconcile_entry_fill ───────────────────────────────────────────────── #

def _executor_with_trades(trades, raises=None):
    ex = OrderExecutor.__new__(OrderExecutor)

    calls = {'n': 0}

    def futures_account_trades(symbol, orderId):
        calls['n'] += 1
        if raises is not None:
            raise raises
        return trades

    ex._feed = types.SimpleNamespace(
        client=types.SimpleNamespace(futures_account_trades=futures_account_trades)
    )
    return ex, calls


def test_reconcile_returns_quantity_weighted_average_of_the_fills():
    """The real trade #1 filled in two parts: 1.3 @ 4.0580 and 370.8 @ 4.0670."""
    ex, _ = _executor_with_trades([
        {'price': '4.0580', 'qty': '1.3'},
        {'price': '4.0670', 'qty': '370.8'},
    ])
    avg = _run(ex._reconcile_entry_fill('INJUSDT', '123'))
    assert avg == pytest.approx(4.0670, abs=0.0002)


def test_reconcile_returns_zero_on_api_error_so_caller_keeps_signalled_price():
    ex, _ = _executor_with_trades(None, raises=RuntimeError('-1003 IP banned'))
    assert _run(ex._reconcile_entry_fill('INJUSDT', '123')) == 0.0


def test_reconcile_retries_while_trade_records_are_empty():
    ex, calls = _executor_with_trades([])
    assert _run(ex._reconcile_entry_fill('INJUSDT', '123')) == 0.0
    assert calls['n'] == 3


def test_reconcile_skips_api_entirely_without_an_order_id():
    ex, calls = _executor_with_trades([{'price': '4.0', 'qty': '1'}])
    assert _run(ex._reconcile_entry_fill('INJUSDT', None)) == 0.0
    assert calls['n'] == 0


def test_reconcile_returns_zero_without_a_feed():
    ex = OrderExecutor.__new__(OrderExecutor)
    ex._feed = None
    assert _run(ex._reconcile_entry_fill('INJUSDT', '123')) == 0.0


# ── wallet_at_open is distinct from balance_at_open ─────────────────────── #

def test_wallet_at_open_is_separate_from_the_trade_cap():
    """balance_at_open carries the allocated per-symbol cap (~296 USDT on the
    incident trades); wallet_at_open carries the account wallet (~3050). Mixing
    them up is what made the old 'Balance' line unreadable."""
    order = _order(balance_at_open=296.30, wallet_at_open=3050.18)
    assert order.balance_at_open != order.wallet_at_open
    assert order.wallet_at_open == 3050.18


def test_new_fields_default_to_zero_for_restored_positions():
    """restore_open_positions rebuilds via OpenOrder(**dict); pre-upgrade state
    files have neither field, so both must be optional."""
    order = OpenOrder(
        symbol='INJUSDT', preset_name='p', side='BUY', entry_price=4.0,
        tp_price=4.1, sl_price=3.9, quantity=1.0, leverage=5,
    )
    assert order.fill_entry_price == 0.0
    assert order.wallet_at_open == 0.0
