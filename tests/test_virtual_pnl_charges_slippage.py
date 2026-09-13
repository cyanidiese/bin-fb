"""Virtual PnL must be charged the same execution cost real orders pay.

The simulator used to compute PnL straight off the signalled entry:

    raw  = (close_price - entry) * qty        # BUY
    fees = (entry + close_price) * qty * 0.0004

Real orders are MARKET orders. Across 137 real fills (2026-09-13, 60 days) the entry came
in worse than signalled by +0.098% on average and *never once better*. At 5x that is about
0.49% of margin per trade, against a measured virtual edge of -0.052%/trade — so the
uncharged simulation overstates results by roughly ten times the edge it is meant to
measure, and symbol-selection decisions taken off it are unsafe.

The charge goes on the MONEY ONLY. The FakeOrder that decided the outcome keeps the
signalled entry and its TP/SL fire at the signalled levels, mirroring the real path where
the reconciled fill "deliberately does NOT feed the FakeOrder or the SL/TP geometry"
(bot/order_executor.py:301) and reaches PnL solely through _effective_entry(). Moving the
virtual entry instead would shift it relative to fixed TP/SL and flip which orders win —
real slippage does not do that.

See docs/specs/2026-09-13-slippage-modelling.md.
"""
import pytest

from tests.test_virtual_order_simulator import make_simulator

FEE = 0.0004


def _unslipped(entry, close, qty, side):
    """What the simulator computed before this change."""
    raw = (close - entry) * qty if side == 'BUY' else (entry - close) * qty
    return raw - (entry + close) * qty * FEE


@pytest.fixture
def sim(tmp_path, monkeypatch):
    s = make_simulator(tmp_path)
    monkeypatch.setattr(
        'bot.virtual_order_simulator.load_risk_config',
        lambda: {'slippage_model_enabled': True, 'slippage_default_pct': 0.10,
                 'slippage_min_samples': 5, 'slippage_per_symbol': {}},
    )
    return s


def _rec(side='BUY', entry=100.0, qty=10.0):
    return {'entry_price': entry, 'quantity': qty, 'side': side}


class TestTheChargeIsApplied:
    def test_a_buy_earns_less_than_the_unslipped_figure(self, sim):
        got = sim._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT')
        assert got < _unslipped(100.0, 105.0, 10.0, 'BUY')

    def test_a_sell_earns_less_too(self, sim):
        got = sim._calc_pnl(_rec('SELL'), 95.0, 'BTCUSDT')
        assert got < _unslipped(100.0, 95.0, 10.0, 'SELL')

    def test_a_loss_is_made_worse_not_better(self, sim):
        """One-sided: slippage can never rescue a losing trade."""
        got = sim._calc_pnl(_rec('BUY'), 95.0, 'BTCUSDT')
        assert got < _unslipped(100.0, 95.0, 10.0, 'BUY')

    def test_the_size_matches_the_configured_default(self, sim):
        """0.10% of a 1000 notional is ~1.0 USDT, plus a hair of extra fee."""
        got = sim._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT')
        assert _unslipped(100.0, 105.0, 10.0, 'BUY') - got == pytest.approx(1.0, abs=0.01)


class TestItCanBeSwitchedOff:
    def test_disabled_reproduces_the_old_number_exactly(self, tmp_path, monkeypatch):
        s = make_simulator(tmp_path)
        monkeypatch.setattr(
            'bot.virtual_order_simulator.load_risk_config',
            lambda: {'slippage_model_enabled': False},
        )
        assert s._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT') == pytest.approx(
            _unslipped(100.0, 105.0, 10.0, 'BUY'))

    def test_no_symbol_means_no_charge(self, sim):
        """Callers that cannot name the symbol must not be charged a guess."""
        assert sim._calc_pnl(_rec('BUY'), 105.0, '') == pytest.approx(
            _unslipped(100.0, 105.0, 10.0, 'BUY'))


class TestGeometryIsUntouched:
    def test_the_record_is_not_mutated(self, sim):
        """TP/SL and the entry that drove them must survive the PnL call."""
        r = _rec('BUY')
        before = dict(r)
        sim._calc_pnl(r, 105.0, 'BTCUSDT')
        assert r == before, 'PnL calculation rewrote the position record'


class TestPerSymbolEstimates:
    def test_an_override_is_used(self, tmp_path, monkeypatch):
        s = make_simulator(tmp_path)
        monkeypatch.setattr(
            'bot.virtual_order_simulator.load_risk_config',
            lambda: {'slippage_model_enabled': True, 'slippage_default_pct': 0.10,
                     'slippage_per_symbol': {'BTCUSDT': 1.0}},
        )
        # 1.0% of a 1000 notional is ~10 USDT
        assert _unslipped(100.0, 105.0, 10.0, 'BUY') - s._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT') \
            == pytest.approx(10.0, abs=0.05)

    def test_the_estimate_is_cached_not_reread_per_tick(self, sim, monkeypatch):
        """check_prices() runs per tick across every rank — an uncached read would hit
        the filesystem thousands of times a candle."""
        calls = []
        monkeypatch.setattr(
            'bot.virtual_order_simulator.load_risk_config',
            lambda: (calls.append(1) or {'slippage_model_enabled': True,
                                         'slippage_default_pct': 0.10}),
        )
        for _ in range(50):
            sim._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT')
        assert len(calls) == 1, f'config re-read {len(calls)} times instead of once'
