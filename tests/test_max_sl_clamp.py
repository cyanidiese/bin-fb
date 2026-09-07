"""An over-wide stop is clamped to the cap, not thrown away with the signal.

`min_sl_pct` has always *widened* a too-tight stop ("SL floored: X% -> Y%") while
`max_sl_pct` *rejected the signal outright*. That asymmetry was expensive: TIAUSDT, the
#2 ranked symbol, had 107 signals rejected at 8.24-11.72% against an 8% cap, and in one
36h window 40 were rejected while the bot placed 1 real order in total.

Clamping is also strictly safer than rejecting at the extreme end. The only catastrophic
loss in 410 real trades was a TIAUSDT SELL with a 22.52% stop, -206.12; under a 12% clamp
that same signal risks 12% instead of 22.52%.

The clamp deliberately runs BEFORE the ATR floor and the RR rules in main.py, so a
clamped stop is still subject to the preset's own geometry checks — if pulling the stop
in makes it too tight for the instrument's volatility, min_sl_atr_mult rejects it.
"""
import pytest

from config.settings import clamp_sl_to_max


class TestBuy:
    def test_a_stop_inside_the_cap_is_untouched(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 94.0, 6.0, 'BUY', 12.0)
        assert (sl, pct, clamped) == (94.0, 6.0, False)

    def test_an_over_wide_stop_is_pulled_in_to_the_cap(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 78.0, 22.0, 'BUY', 12.0)
        assert clamped is True
        assert pct == pytest.approx(12.0)
        assert sl == pytest.approx(88.0)          # entry * (1 - 12/100)

    def test_the_clamped_stop_stays_below_entry(self):
        sl, _, _ = clamp_sl_to_max(100.0, 50.0, 50.0, 'BUY', 12.0)
        assert sl < 100.0


class TestSell:
    """sl_dist_pct is inflated x1.5 for SELL, so the inverse must divide by 1.5 —
    exactly what the existing min_sl_pct floor does."""

    def test_a_stop_inside_the_cap_is_untouched(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 104.0, 6.0, 'SELL', 12.0)
        assert (sl, pct, clamped) == (104.0, 6.0, False)

    def test_an_over_wide_stop_is_pulled_in_using_the_1_5_convention(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 122.52, 22.52 * 1.5, 'SELL', 12.0)
        assert clamped is True
        assert pct == pytest.approx(12.0)
        assert sl == pytest.approx(108.0)         # entry * (1 + 12/1.5/100)

    def test_the_clamped_stop_stays_above_entry(self):
        sl, _, _ = clamp_sl_to_max(100.0, 160.0, 90.0, 'SELL', 12.0)
        assert sl > 100.0

    def test_buy_and_sell_clamps_are_not_symmetric(self):
        """A SELL clamp lands closer in price terms because of the x1.5 weighting."""
        b, _, _ = clamp_sl_to_max(100.0, 70.0, 30.0, 'BUY', 12.0)
        s, _, _ = clamp_sl_to_max(100.0, 130.0, 30.0, 'SELL', 12.0)
        assert abs(100.0 - b) == pytest.approx(12.0)
        assert abs(100.0 - s) == pytest.approx(8.0)


class TestDisabled:
    def test_a_zero_cap_disables_clamping(self):
        """max_sl_pct=0 means 'no cap', the existing convention everywhere else."""
        sl, pct, clamped = clamp_sl_to_max(100.0, 50.0, 50.0, 'BUY', 0.0)
        assert (sl, pct, clamped) == (50.0, 50.0, False)

    def test_a_negative_cap_disables_clamping(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 50.0, 50.0, 'BUY', -1.0)
        assert clamped is False


class TestConsistency:
    def test_clamping_always_reduces_risk(self):
        """The whole point: never widen, only pull in."""
        for side, sl in (('BUY', 60.0), ('SELL', 140.0)):
            for cap in (8.0, 10.0, 12.0, 15.0):
                raw_pct = abs(100.0 - sl) / 100.0 * 100 * (1.5 if side == 'SELL' else 1)
                new_sl, new_pct, clamped = clamp_sl_to_max(100.0, sl, raw_pct, side, cap)
                if clamped:
                    assert abs(100.0 - new_sl) <= abs(100.0 - sl), (side, cap)
                    assert new_pct <= raw_pct

    def test_it_is_idempotent(self):
        sl, pct, _ = clamp_sl_to_max(100.0, 78.0, 22.0, 'BUY', 12.0)
        sl2, pct2, clamped2 = clamp_sl_to_max(100.0, sl, pct, 'BUY', 12.0)
        assert clamped2 is False
        assert (sl2, pct2) == (sl, pct)


def test_all_three_engines_use_the_shared_helper():
    """live, virtual and backtest must agree — otherwise the virtual statistics
    measure a different strategy than the one that trades, which is exactly why there
    was no data on the rejected signals for two months."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for f in ('main.py', 'bot/virtual_order_simulator.py', 'bot/backtester.py'):
        src = (root / f).read_text()
        assert 'clamp_sl_to_max' in src, f'{f} still has its own max_sl_pct handling'
        assert "decision='skip_max_sl_pct'" not in src, f'{f} still rejects on max_sl_pct'
