"""A real order must not be judged against the candle that closed before it existed.

Real orders are placed inside the candle-close handler, which then calls
check_symbol_candle() with that just-closed candle. Before the fix, every new order was
checked against the pre-entry candle's high/low: 46 of the 48 real orders closed within a
minute in the 30 days to 2026-09-28 (30 'losses', 16 'trail' exits) were exactly that —
-186.66 USDT, at prices the market did not revisit.
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from bot.fake_order import FakeOrder
from bot.order_executor import OpenOrder, OrderState
from tests.factories import make_executor

CANDLE_CLOSE_MS = 1_790_000_899_999          # the candle that just closed


def _open(ex, opened_ms):
    ex._fake_orders['BTCUSDT'] = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=90.0,
                                           level=1, signal_type='test', candle_index=0)
    ex._open_orders['BTCUSDT'] = OpenOrder(
        symbol='BTCUSDT', preset_name='p', side='BUY', entry_price=100.0,
        tp_price=110.0, sl_price=90.0, quantity=1.0, leverage=5,
        open_time=datetime.fromtimestamp(opened_ms / 1000, timezone.utc).isoformat())
    ex._states['BTCUSDT'] = OrderState.OPEN
    closed = []

    async def fake_finalize(symbol, order, result, price):
        closed.append(result)
        return {'symbol': symbol, 'result': result}
    ex._finalize_close = fake_finalize
    return closed


def _check(ex, **kw):
    # the pre-entry candle's low (85) is through the SL (90)
    return asyncio.run(ex.check_symbol_candle('BTCUSDT', high=105.0, low=85.0,
                                              candle_open=102.0, candle_close=88.0, **kw))


def test_order_opened_after_the_candle_is_not_closed_by_it():
    ex = make_executor()
    closed = _open(ex, CANDLE_CLOSE_MS + 3_000)          # placed 3 s after the close
    assert _check(ex, candle_close_ms=CANDLE_CLOSE_MS) == []
    assert closed == []
    assert 'BTCUSDT' in ex._fake_orders                  # still monitored


def test_order_that_lived_through_the_candle_is_still_checked():
    ex = make_executor()
    closed = _open(ex, CANDLE_CLOSE_MS - 600_000)        # opened 10 min before the close
    out = _check(ex, candle_close_ms=CANDLE_CLOSE_MS)
    assert closed == ['loss'] and out


def test_without_a_close_time_the_old_behaviour_is_kept():
    ex = make_executor()
    closed = _open(ex, CANDLE_CLOSE_MS + 3_000)
    _check(ex)                                            # no candle_close_ms
    assert closed == ['loss']


def test_main_passes_the_candle_close_time():
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    call = src[src.index('candle_closed = await order_executor.check_symbol_candle('):][:400]
    assert 'candle_close_ms=int(candle_to_add[6])' in call
