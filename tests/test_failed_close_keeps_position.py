"""A close that did not execute must not be recorded as a close.

Before this, a failed market close (which a rate-limit ban guarantees) was booked at
the *software* price: the trade was written to the preset record, the position was
deleted from the bot's books and the symbol went IDLE — while the position was still
open on the exchange. The fabricated PnL then flowed into
virtual_tracker.record_closed_trade(), and preset ranking is sum(recent_trades[-10:]),
so the bot would promote a preset on profit it never earned.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.order_executor import OpenOrder, OrderExecutor, OrderState


def _run(coro):
    try:
        prev = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)


@pytest.fixture
def ex():
    e = OrderExecutor.__new__(OrderExecutor)
    e._open_orders = {}
    e._fake_orders = {}
    e._states = {}
    e._closing = set()
    e._pending_close = {}
    e._pending_close_logged = {}
    e._symbol_candle_index = {}
    e._notifier = MagicMock()
    e._record_real_order_close = MagicMock()
    e._record_success = MagicMock()
    e._calc_pnl = MagicMock(return_value=42.0)
    e._order_fee = MagicMock(return_value=1.2)
    e._effective_entry = MagicMock(return_value=100.0)
    e._open_orders['EIGENUSDT'] = OpenOrder(
        symbol='EIGENUSDT', preset_name='r5_sl_filter', side='BUY',
        entry_price=100.0, tp_price=110.0, sl_price=95.0,
        quantity=10.0, leverage=5,
    )
    e._fake_orders['EIGENUSDT'] = MagicMock()
    e._states['EIGENUSDT'] = OrderState.OPEN if hasattr(OrderState, 'OPEN') else OrderState.PLACING
    return e


BAN = Exception("APIError(code=-1003): Way too many requests; IP(x) banned until 1788591715275")


def test_failed_close_records_nothing(ex):
    ex._market_close = AsyncMock(side_effect=BAN)
    info = _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    assert info is None, 'a failed close must not produce a trade record'
    ex._record_real_order_close.assert_not_called()


def test_failed_close_keeps_the_position(ex):
    ex._market_close = AsyncMock(side_effect=BAN)
    _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    assert 'EIGENUSDT' in ex._open_orders, 'position was abandoned while still open on the exchange'
    assert 'EIGENUSDT' in ex._fake_orders, 'exit management was dropped'


def test_failed_close_does_not_free_the_symbol(ex):
    """IDLE would let a second position open on top of the live one."""
    ex._market_close = AsyncMock(side_effect=BAN)
    _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    assert ex._states['EIGENUSDT'] != OrderState.IDLE


def test_failed_close_is_queued_for_retry(ex):
    ex._market_close = AsyncMock(side_effect=BAN)
    _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    assert ex._pending_close['EIGENUSDT'] == ('win', 110.0)


def test_retry_closes_once_the_exchange_recovers(ex):
    ex._market_close = AsyncMock(side_effect=BAN)
    _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))

    ex._market_close = AsyncMock(return_value=109.5)
    out = _run(ex.retry_pending_closes())

    assert len(out) == 1
    assert out[0]['symbol'] == 'EIGENUSDT'
    assert out[0]['close_price'] == 109.5, 'must book the real fill, not the software price'
    assert 'EIGENUSDT' not in ex._open_orders
    assert 'EIGENUSDT' not in ex._pending_close
    assert ex._states['EIGENUSDT'] == OrderState.IDLE
    ex._record_real_order_close.assert_called_once()


def test_repeated_failures_notify_only_once(ex):
    """A ban lasts many candles; one alert, not one per retry."""
    ex._market_close = AsyncMock(side_effect=BAN)
    for _ in range(5):
        _run(ex.retry_pending_closes()) if ex._pending_close else _run(
            ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    assert ex._notifier.notify.call_count == 1


def test_successful_close_still_records_normally(ex):
    ex._market_close = AsyncMock(return_value=110.0)
    info = _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    assert info is not None
    assert info['close_price'] == 110.0
    assert info['pnl_usdt'] == 42.0
    assert 'EIGENUSDT' not in ex._open_orders
    assert ex._states['EIGENUSDT'] == OrderState.IDLE
    ex._record_real_order_close.assert_called_once()


def test_retry_drops_intent_if_position_vanished(ex):
    """Reconciliation may have closed it elsewhere — do not resurrect a ghost."""
    ex._market_close = AsyncMock(side_effect=BAN)
    _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
    del ex._open_orders['EIGENUSDT']
    assert _run(ex.retry_pending_closes()) == []
    assert ex._pending_close == {}


# ── The live path ────────────────────────────────────────────────────────────
# main.py calls check_symbol_candle / check_symbol_price, NOT the executor's
# on_candle_close (which nothing calls). These pin the retry to the entry point
# that actually runs in production.

def _fake(result_seq):
    fo = MagicMock()
    fo.check.side_effect = list(result_seq)
    fo.close_price = 110.0
    return fo


def test_live_candle_path_does_not_book_a_failed_close(ex):
    ex._fake_orders['EIGENUSDT'] = _fake(['win'])
    ex._market_close = AsyncMock(side_effect=BAN)
    out = _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
    assert out == []
    ex._record_real_order_close.assert_not_called()
    assert 'EIGENUSDT' in ex._open_orders
    assert ex._pending_close['EIGENUSDT'][0] == 'win'


def test_live_candle_path_retries_next_candle_and_books_the_real_fill(ex):
    ex._fake_orders['EIGENUSDT'] = _fake(['win', 'win'])
    ex._market_close = AsyncMock(side_effect=BAN)
    assert _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0)) == []

    # ban lifts; next candle must complete the exit at the real price
    ex._market_close = AsyncMock(return_value=108.25)
    out = _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
    assert len(out) == 1
    assert out[0]['close_price'] == 108.25
    assert 'EIGENUSDT' not in ex._open_orders
    assert ex._pending_close == {}


def test_stranded_symbol_is_not_re_evaluated_while_pending(ex):
    """Re-running FakeOrder.check on a stranded position would double-handle it."""
    fo = _fake(['win'])
    ex._fake_orders['EIGENUSDT'] = fo
    ex._market_close = AsyncMock(side_effect=BAN)
    _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
    calls_after_first = fo.check.call_count
    _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
    assert fo.check.call_count == calls_after_first, 'FakeOrder re-evaluated while stranded'
