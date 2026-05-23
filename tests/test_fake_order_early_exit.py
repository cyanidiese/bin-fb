"""Tests for FakeOrder early-loss exit: max_losing_pct, max_losing_candles, early_loss_sl."""
import pytest
from bot.fake_order import FakeOrder


def make_long(max_losing_pct=0.0, max_losing_candles=0, early_loss_sl=0.0) -> FakeOrder:
    """BUY order: entry=100, tp=120, sl=80."""
    return FakeOrder(
        side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
        max_losing_pct=max_losing_pct,
        max_losing_candles=max_losing_candles,
        early_loss_sl=early_loss_sl,
    )


def make_short(max_losing_pct=0.0, max_losing_candles=0, early_loss_sl=0.0) -> FakeOrder:
    """SELL order: entry=100, tp=80, sl=120."""
    return FakeOrder(
        side='SELL', entry_price=100.0, tp=80.0, sl=120.0,
        level=1, signal_type='test', candle_index=0,
        max_losing_pct=max_losing_pct,
        max_losing_candles=max_losing_candles,
        early_loss_sl=early_loss_sl,
    )


# ── max_losing_pct ─────────────────────────────────────────────────────────────

def test_pct_zero_no_early_exit():
    """Zero value = disabled."""
    order = make_long(max_losing_pct=0.0)
    result = order.check(91.0, 90.0, 1, candle_open=99.0, candle_close=90.0)
    assert result is None


def test_pct_50_long_fires_at_halfway():
    """50% of SL dist from entry for BUY: entry=100, sl=80 → early exit at 90."""
    order = make_long(max_losing_pct=50.0)
    result = order.check(99.0, 90.0, 1, candle_open=99.0, candle_close=90.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(90.0)


def test_pct_50_long_no_trigger_above_threshold():
    """Price stays above 90 — no early exit."""
    order = make_long(max_losing_pct=50.0)
    result = order.check(99.0, 91.0, 1, candle_open=99.0, candle_close=91.0)
    assert result is None


def test_pct_50_short_fires_at_halfway():
    """50% of SL dist from entry for SELL: entry=100, sl=120 → early exit at 110."""
    order = make_short(max_losing_pct=50.0)
    result = order.check(110.0, 99.0, 1, candle_open=101.0, candle_close=110.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(110.0)


def test_pct_no_exit_when_armed():
    """Once partial_price is hit (order armed), early exit must NOT fire."""
    order = FakeOrder(
        side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
        partial_take_pct=0.3, max_losing_pct=50.0,
    )
    # Candle 1: arm the order (high reaches 106)
    order.check(106.0, 101.0, 1, candle_open=101.0, candle_close=104.0)
    assert order._partial_armed is True
    # Candle 2: price drops below early-exit threshold (90) — but armed, so no early exit.
    result = order.check(95.0, 88.0, 2, candle_open=95.0, candle_close=88.0)
    assert result == 'partial'
    assert order.close_price == pytest.approx(106.0)


# ── early_loss_sl (amount-based, pre-computed) ─────────────────────────────────

def test_early_loss_sl_long():
    """Pre-computed amount-based SL: early_loss_sl=95 for a BUY."""
    order = make_long(early_loss_sl=95.0)
    result = order.check(99.0, 95.0, 1, candle_open=99.0, candle_close=95.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(95.0)


def test_early_loss_sl_short():
    """Pre-computed amount-based SL: early_loss_sl=105 for a SELL."""
    order = make_short(early_loss_sl=105.0)
    result = order.check(105.0, 99.0, 1, candle_open=101.0, candle_close=105.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(105.0)


def test_tighter_threshold_wins():
    """When both pct-based and amount-based are set, tighter (closer to entry) fires."""
    order = make_long(max_losing_pct=50.0, early_loss_sl=95.0)
    result = order.check(99.0, 95.0, 1, candle_open=99.0, candle_close=95.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(95.0)


# ── max_losing_candles ─────────────────────────────────────────────────────────

def test_losing_candles_triggers_after_n():
    """N=3 consecutive below-entry closes → exit on candle 3."""
    order = make_long(max_losing_candles=3)
    assert order.check(99.0, 97.0, 1, candle_open=99.0, candle_close=97.0) is None
    assert order.check(98.0, 96.0, 2, candle_open=98.0, candle_close=96.0) is None
    result = order.check(97.0, 95.0, 3, candle_open=97.0, candle_close=95.0)
    assert result == 'loss'


def test_losing_candles_resets_on_recovery():
    """Counter resets when candle close is back above entry."""
    order = make_long(max_losing_candles=3)
    order.check(99.0, 97.0, 1, candle_open=99.0, candle_close=97.0)
    order.check(98.0, 96.0, 2, candle_open=98.0, candle_close=96.0)
    order.check(102.0, 99.0, 3, candle_open=99.0, candle_close=101.0)
    assert order.check(99.0, 97.0, 4, candle_open=99.0, candle_close=97.0) is None
    assert order.check(98.0, 96.0, 5, candle_open=98.0, candle_close=96.0) is None


def test_losing_candles_not_updated_by_price_tick():
    """check_price() must not update the consecutive-candle counter."""
    order = make_long(max_losing_candles=1)
    order.check_price(98.0)
    order.check_price(97.0)
    assert order._consecutive_losing_candles == 0


def test_losing_candles_zero_disabled():
    """max_losing_candles=0 → no early exit regardless of candle direction."""
    order = make_long(max_losing_candles=0)
    for i in range(10):
        result = order.check(99.0, 97.0, i, candle_open=99.0, candle_close=97.0)
        assert result is None


def test_all_zero_defaults_no_early_exit():
    """FakeOrder with default params (all zeros) behaves exactly as before."""
    order = FakeOrder(
        side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
    )
    result = order.check(99.0, 79.0, 1, candle_open=99.0, candle_close=79.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(80.0)
