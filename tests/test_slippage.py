"""Virtual PnL must be charged what real fills actually cost.

Virtual orders open at the signalled price; real orders are MARKET orders. Measured
2026-09-13 over 137 real fills spanning 60 days:

    all symbols   mean +0.0980%   median +0.0183%   p90 +0.3575%
    EIGENUSDT     +0.0240% (n=36)      TIAUSDT      +0.1775% (n=17)
    REZUSDT       +0.0134% (n=12)      INJUSDT      +0.1690% (n=18)

Two properties of that data drive every test here:

  * NOT ONE of the 137 fills was better than signalled. The cost is one-sided, so
    ignoring it is a systematic overstatement, not noise that averages out.
  * It varies ~13x between symbols, so one global constant misprices most of them.

At 5x leverage +0.098% of notional is ~0.49% of margin per trade, against a measured
virtual edge of -0.052%/trade. That is why unadjusted virtual statistics cannot be used
to decide which symbols to trade for real.

See docs/specs/2026-09-13-slippage-modelling.md.
"""
import json

import pytest

from bot.slippage import (
    DEFAULT_PCT,
    WINDOW,
    adverse_pct,
    effective_entry,
    estimate,
    record,
    stats,
)


@pytest.fixture
def store(tmp_path):
    return tmp_path / 'slippage_test.json'


class TestAdversePct:
    def test_a_buy_filled_higher_is_adverse(self):
        # the measured EIGENUSDT order: signalled 0.2087, filled 0.2088
        assert adverse_pct(0.2087, 0.2088, 'BUY') == pytest.approx(0.0479, abs=0.001)

    def test_a_sell_filled_lower_is_adverse(self):
        assert adverse_pct(0.2088, 0.2087, 'SELL') == pytest.approx(0.0479, abs=0.001)

    def test_a_buy_filled_lower_is_favourable(self):
        """Kept signed. The clamp belongs on the estimate, not on the measurement."""
        assert adverse_pct(0.2088, 0.2087, 'BUY') < 0

    def test_an_exact_fill_costs_nothing(self):
        assert adverse_pct(100.0, 100.0, 'BUY') == 0.0

    def test_junk_prices_do_not_raise(self):
        assert adverse_pct(0.0, 1.0, 'BUY') == 0.0
        assert adverse_pct(1.0, 0.0, 'SELL') == 0.0
        assert adverse_pct(-1.0, 1.0, 'BUY') == 0.0


class TestRecord:
    def test_it_stores_a_sample(self, store):
        record(store, 'TIAUSDT', 1.0, 1.001, 'BUY')
        assert stats(store)['TIAUSDT']['n'] == 1

    def test_the_mean_is_the_running_mean(self, store):
        for filled in (1.001, 1.002, 1.003):
            record(store, 'TIAUSDT', 1.0, filled, 'BUY')
        assert stats(store)['TIAUSDT']['mean'] == pytest.approx(0.2, abs=0.01)

    def test_symbols_are_kept_apart(self, store):
        record(store, 'TIAUSDT', 1.0, 1.002, 'BUY')
        record(store, 'REZUSDT', 1.0, 1.0001, 'BUY')
        s = stats(store)
        assert s['TIAUSDT']['mean'] > s['REZUSDT']['mean']

    def test_the_window_is_capped(self, store):
        for _ in range(WINDOW + 25):
            record(store, 'TIAUSDT', 1.0, 1.001, 'BUY')
        assert stats(store)['TIAUSDT']['n'] == WINDOW

    def test_the_window_drops_the_oldest(self, store):
        """Liquidity drifts — an estimate anchored to old fills would never catch up."""
        for _ in range(WINDOW):
            record(store, 'TIAUSDT', 1.0, 1.005, 'BUY')      # 0.5% era
        for _ in range(WINDOW):
            record(store, 'TIAUSDT', 1.0, 1.0, 'BUY')        # 0.0% era
        assert stats(store)['TIAUSDT']['mean'] == pytest.approx(0.0, abs=1e-6)

    def test_a_bad_price_is_not_recorded(self, store):
        record(store, 'TIAUSDT', 0.0, 1.0, 'BUY')
        assert 'TIAUSDT' not in stats(store)

    def test_writing_never_raises(self, tmp_path):
        """Runs on the placement path — a disk problem must not stop trading."""
        record(tmp_path / 'no' / 'such' / 'dir' / 's.json', 'TIAUSDT', 1.0, 1.001, 'BUY')


class TestEstimate:
    def test_an_unknown_symbol_gets_the_default(self, store):
        """The case that matters: symbols with no real fills are the ones being judged."""
        assert estimate(store, 'LINKUSDT', {}) == pytest.approx(DEFAULT_PCT)

    def test_too_few_samples_still_gets_the_default(self, store):
        for _ in range(3):
            record(store, 'LINKUSDT', 1.0, 1.0, 'BUY')       # would read as 0% slippage
        assert estimate(store, 'LINKUSDT', {'slippage_min_samples': 5}) == pytest.approx(DEFAULT_PCT)

    def test_enough_samples_uses_the_measured_mean(self, store):
        for _ in range(8):
            record(store, 'TIAUSDT', 1.0, 1.0018, 'BUY')
        assert estimate(store, 'TIAUSDT', {'slippage_min_samples': 5}) == pytest.approx(0.18, abs=0.01)

    def test_an_explicit_override_wins(self, store):
        for _ in range(20):
            record(store, 'TIAUSDT', 1.0, 1.0018, 'BUY')
        got = estimate(store, 'TIAUSDT', {'slippage_per_symbol': {'TIAUSDT': 0.5}})
        assert got == pytest.approx(0.5)

    def test_the_model_can_be_switched_off(self, store):
        for _ in range(20):
            record(store, 'TIAUSDT', 1.0, 1.0018, 'BUY')
        assert estimate(store, 'TIAUSDT', {'slippage_model_enabled': False}) == 0.0

    def test_a_favourable_history_is_clamped_to_zero(self, store):
        """Never credit virtual for lucky fills — the charge floors at nothing."""
        for _ in range(10):
            record(store, 'TIAUSDT', 1.0, 0.999, 'BUY')      # favourable
        assert estimate(store, 'TIAUSDT', {'slippage_min_samples': 5}) == 0.0

    def test_a_missing_store_returns_the_default(self, tmp_path):
        assert estimate(tmp_path / 'absent.json', 'X', {}) == pytest.approx(DEFAULT_PCT)

    def test_a_corrupt_store_returns_the_default(self, store):
        store.write_text('{not json')
        assert estimate(store, 'X', {}) == pytest.approx(DEFAULT_PCT)

    def test_a_configured_default_is_honoured(self, store):
        assert estimate(store, 'X', {'slippage_default_pct': 0.25}) == pytest.approx(0.25)


class TestEffectiveEntry:
    def test_a_buy_pays_more(self):
        assert effective_entry(100.0, 'BUY', 0.1) == pytest.approx(100.1)

    def test_a_sell_receives_less(self):
        assert effective_entry(100.0, 'SELL', 0.1) == pytest.approx(99.9)

    def test_zero_slippage_is_the_signalled_price(self):
        assert effective_entry(100.0, 'BUY', 0.0) == 100.0

    def test_it_always_hurts_never_helps(self):
        """Both sides must move against the position, never for it."""
        assert effective_entry(100.0, 'BUY', 0.3) > 100.0
        assert effective_entry(100.0, 'SELL', 0.3) < 100.0

    def test_a_junk_entry_is_passed_through(self):
        assert effective_entry(0.0, 'BUY', 0.1) == 0.0


class TestTheArithmeticThisProtects:
    """The measured consequence, so a regression is recognisable."""

    @staticmethod
    def _pnl(entry, close, qty, side, fee=0.0004):
        raw = (close - entry) * qty if side == 'BUY' else (entry - close) * qty
        return raw - (entry + close) * qty * fee

    def test_the_charge_is_about_half_a_percent_of_margin_at_5x(self):
        """+0.098% of notional at 5x is ~0.49% of margin — 10x the virtual edge."""
        entry, qty, lev = 1.0, 1000.0, 5
        margin = entry * qty / lev
        eff = effective_entry(entry, 'BUY', 0.098)
        cost = self._pnl(entry, 1.02, qty, 'BUY') - self._pnl(eff, 1.02, qty, 'BUY')
        assert cost / margin * 100 == pytest.approx(0.49, abs=0.05)

    def test_charging_it_always_lowers_pnl(self):
        for side, close in (('BUY', 1.05), ('SELL', 0.95)):
            entry, qty = 1.0, 1000.0
            eff = effective_entry(entry, side, 0.1)
            assert self._pnl(eff, close, qty, side) < self._pnl(entry, close, qty, side)
