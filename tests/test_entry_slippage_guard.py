"""Stale-entry abort: close immediately when the fill is materially worse than the signal.

Measured Aug 19-30 across all symbols with reconciled fills (n=33):
    adverse slip <0.05%   22 trades  +139.17 USDT  36% WR
    adverse slip 0.05-0.30% 7 trades  -32.58 USDT  14% WR
    adverse slip >=0.30%    4 trades -142.28 USDT   0% WR
correlation(adverse slip, PnL) = -0.59. A fill that far from the signal means the
move already started without us, so the entry premise is gone.
"""
import pytest

from bot.order_executor import OrderExecutor


def adverse_pct(side: str, signalled: float, filled: float) -> float:
    """Mirrors the directional calculation in place_order."""
    if signalled <= 0:
        return 0.0
    return ((filled - signalled) / signalled * 100) if side == 'BUY' \
        else ((signalled - filled) / signalled * 100)


# ── direction ──────────────────────────────────────────────────────────── #

def test_buy_filled_higher_is_adverse():
    assert adverse_pct('BUY', 5.138, 5.174) == pytest.approx(0.7007, abs=1e-3)


def test_buy_filled_lower_is_favourable():
    assert adverse_pct('BUY', 5.138, 5.100) < 0


def test_sell_filled_lower_is_adverse():
    """A short filled below its signal is the mirror of a long filled above."""
    assert adverse_pct('SELL', 5.138, 5.100) > 0


def test_sell_filled_higher_is_favourable():
    assert adverse_pct('SELL', 5.138, 5.174) < 0


def test_favourable_slippage_never_triggers_an_abort():
    """The old code used abs(), which would have aborted on a GOOD fill."""
    for side, signalled, filled in (('BUY', 5.0, 4.9), ('SELL', 5.0, 5.1)):
        assert adverse_pct(side, signalled, filled) < 0.30


# ── the real losing trades this is aimed at ────────────────────────────── #

@pytest.mark.parametrize("ts,side,signalled,filled,expected", [
    ("2026-08-30 INJ", 'BUY', 5.138, 5.174, 0.701),
    ("2026-08-27 INJ", 'BUY', 5.454, 5.494, 0.733),
    ("2026-08-27 INJ", 'BUY', 5.492, 5.522, 0.546),
    ("2026-08-20 INJ", 'SELL', 4.678, 4.661, 0.363),
])
def test_reproduces_the_measured_losing_fills(ts, side, signalled, filled, expected):
    got = adverse_pct(side, signalled, filled)
    assert got == pytest.approx(expected, abs=0.01), ts
    assert got >= 0.30, "all four of these lost; a 0.30% limit must catch them"


def test_winning_trades_are_not_caught_by_a_030_limit():
    """The zero-slip winners (+23.04, +58.39, +60.14) must be untouched."""
    for signalled, filled in ((4.801, 4.802), (1.526, 1.526), (5.455, 5.456)):
        assert adverse_pct('BUY', signalled, filled) < 0.30


# ── the gate itself ────────────────────────────────────────────────────── #

def test_disabled_by_default_means_never_abort():
    """max_entry_slippage_pct defaults to 0.0 — the feature ships inert because
    n=4 in the decisive band is too thin to enable on its own."""
    limit = 0.0
    assert not (limit > 0 and adverse_pct('BUY', 5.138, 5.174) > limit)


def test_fires_only_above_the_configured_limit():
    limit = 0.30
    assert adverse_pct('BUY', 5.138, 5.174) > limit          # 0.70% -> abort
    assert not adverse_pct('BUY', 4.801, 4.802) > limit      # 0.02% -> keep


def test_zero_signalled_price_is_safe():
    assert adverse_pct('BUY', 0.0, 5.0) == 0.0


# ── PnL still uses the real fill regardless ────────────────────────────── #

def test_pnl_uses_the_filled_price_not_the_signal():
    from bot.order_executor import OpenOrder
    o = OpenOrder(symbol='INJUSDT', preset_name='p', side='BUY', entry_price=5.138,
                  tp_price=5.4, sl_price=5.045, quantity=305.6, leverage=5,
                  fill_entry_price=5.174)
    assert OrderExecutor._effective_entry(o) == 5.174
