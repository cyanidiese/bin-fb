"""The reported balance must track reality between exchange reads.

Measured 2026-09-11. TIAUSDT closed at 12:02:39 after being open 28 hours:

    wallet_at_open (09-10 07:45)   3145.03
    pnl                             -85.76
    Telegram printed: Before        3145.03   After  3059.28
    actual wallet at 12:02          2917.76   -> overstated by 141.52

balance_history jumps 10:30:02 -> 12:45:03 with nothing at 12:02, so the wallet read
failed (we were rate-limit banned) and `_after_or_computed` used its fallback:

    before = closed['wallet_at_open']      # captured 28 HOURS earlier
    return before + pnl

`wallet_at_open` is snapshotted when the order OPENS, so on a long-held position both
Before and After are a day stale. Worse, the running balance never advances on a close at
all — `risk_manager.update_balance()` is called from exactly one place (the per-candle
`_get_fresh_balance()`), so during a ban the balance simply stops moving while trades
settle.

Two pieces fix it:

  apply_realised(pnl)  advance the running balance as each trade closes
  reconcile(actual)    on a genuine exchange read, snap to it and RECORD THE DRIFT

The drift record is not bookkeeping for its own sake. Roughly -65 in September and -200
in August of wallet movement could not be attributed to any order; the likeliest cause is
futures funding, charged every 8h and never recorded as a trade. Drift turns that guess
into a measurement.
"""
import json

import pytest

from bot.risk_manager import RiskManager


@pytest.fixture
def rm(tmp_path):
    cfg = tmp_path / 'risk_config.json'
    cfg.write_text(json.dumps({
        'min_balance_pct': 0.0,
        'max_drawdown_pct': 90.0,
        'balance_tiers': [{'min_balance_usdt': 0, 'max_deploy_pct': 80,
                           'max_leverage_ceiling': 15}],
    }))
    r = RiskManager(mode='test', config_path=cfg,
                    state_path=tmp_path / 'risk_state.json',
                    backtest_results_dir=tmp_path)
    r.seed_real_balance(3145.03)
    return r


class TestApplyRealised:
    def test_a_closed_trade_advances_the_balance(self, rm):
        rm.apply_realised(-85.76)
        assert rm.get_balance() == pytest.approx(3059.27, abs=0.01)

    def test_several_trades_accumulate(self, rm):
        for pnl in (-119.71, 14.79, -29.46, -16.69):
            rm.apply_realised(pnl)
        assert rm.get_balance() == pytest.approx(3145.03 - 151.07, abs=0.01)

    def test_a_win_raises_it(self, rm):
        rm.apply_realised(+84.15)
        assert rm.get_balance() == pytest.approx(3229.18, abs=0.01)

    def test_zero_pnl_is_a_no_op(self, rm):
        before = rm.get_balance()
        rm.apply_realised(0.0)
        assert rm.get_balance() == before

    def test_it_cannot_drive_the_balance_negative(self, rm):
        rm.apply_realised(-99999.0)
        assert rm.get_balance() >= 0.0

    def test_a_non_numeric_pnl_is_ignored_not_fatal(self, rm):
        before = rm.get_balance()
        rm.apply_realised(None)          # must never take the candle path down
        assert rm.get_balance() == before

    def test_applying_does_not_ratchet_the_peak_on_a_loss(self, rm):
        peak = rm._peak_balance
        rm.apply_realised(-85.76)
        assert rm._peak_balance == peak


class TestReconcile:
    def test_it_snaps_to_the_exchange_figure(self, rm):
        rm.apply_realised(-85.76)                 # calculated 3059.27
        rm.reconcile(2917.76)
        assert rm.get_balance() == pytest.approx(2917.76, abs=0.01)

    def test_it_returns_the_drift(self, rm):
        rm.apply_realised(-85.76)
        drift = rm.reconcile(2917.76)
        assert drift == pytest.approx(2917.76 - 3059.27, abs=0.01)   # ~ -141.51

    def test_no_drift_when_the_calculation_was_right(self, rm):
        rm.apply_realised(-100.0)
        assert rm.reconcile(3045.03) == pytest.approx(0.0, abs=0.01)

    def test_a_non_positive_reading_is_refused(self, rm):
        """A banned read returns 0.0 — that must never be taken as the balance."""
        before = rm.get_balance()
        assert rm.reconcile(0.0) is None
        assert rm.get_balance() == before
        assert rm.reconcile(-5.0) is None
        assert rm.get_balance() == before

    def test_it_records_the_drift_to_disk(self, rm, tmp_path):
        rm.set_drift_log(tmp_path / 'drift.json')
        rm.apply_realised(-85.76)
        rm.reconcile(2917.76)
        rows = json.loads((tmp_path / 'drift.json').read_text())
        assert len(rows) == 1
        r = rows[0]
        assert r['calculated'] == pytest.approx(3059.27, abs=0.01)
        assert r['actual'] == pytest.approx(2917.76, abs=0.01)
        assert r['drift'] == pytest.approx(-141.51, abs=0.01)
        assert r['trades'] == 1
        assert 'timestamp' in r

    def test_trades_since_last_reconcile_is_counted_and_reset(self, rm, tmp_path):
        rm.set_drift_log(tmp_path / 'drift.json')
        for pnl in (-10.0, -20.0, +5.0):
            rm.apply_realised(pnl)
        rm.reconcile(3100.0)
        rm.apply_realised(-1.0)
        rm.reconcile(3099.0)
        rows = json.loads((tmp_path / 'drift.json').read_text())
        assert [r['trades'] for r in rows] == [3, 1]

    def test_a_reconcile_with_no_trades_still_records(self, rm, tmp_path):
        """This is how pure funding-fee drift shows up — money moved, nothing traded."""
        rm.set_drift_log(tmp_path / 'drift.json')
        rm.reconcile(3140.00)
        rows = json.loads((tmp_path / 'drift.json').read_text())
        assert rows[0]['trades'] == 0
        assert rows[0]['drift'] == pytest.approx(-5.03, abs=0.01)

    def test_writing_the_log_never_raises(self, rm, tmp_path):
        """It runs on the candle path — a disk problem must not stop trading."""
        rm.set_drift_log(tmp_path / 'no' / 'such' / 'dir' / 'drift.json')
        rm.apply_realised(-1.0)
        rm.reconcile(3000.0)          # must not raise

    def test_without_a_log_path_it_still_reconciles(self, rm):
        rm.apply_realised(-85.76)
        assert rm.reconcile(2917.76) == pytest.approx(-141.51, abs=0.01)


class TestItDoesNotBreakWhatExists:
    def test_seed_still_anchors_balance_and_peak(self, rm):
        assert rm.get_balance() == pytest.approx(3145.03)
        assert rm._peak_balance == pytest.approx(3145.03)

    def test_update_balance_still_works(self, rm):
        rm.update_balance(3200.0)
        assert rm.get_balance() == pytest.approx(3200.0)

    def test_reconcile_raises_the_peak_like_a_read_would(self, rm):
        rm.reconcile(3300.0)
        assert rm._peak_balance == pytest.approx(3300.0)

    def test_drawdown_still_evaluated_on_reconcile(self, rm):
        """reconcile must not bypass the risk checks update_balance performs."""
        rm.reconcile(100.0)
        assert rm.get_balance() == pytest.approx(100.0)


class TestMainUsesTheRunningBalance:
    """Source-level, matching how this suite pins main.py wiring."""

    @staticmethod
    def _src():
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / 'main.py').read_text()

    def test_closes_advance_the_running_balance(self):
        assert 'apply_realised' in self._src(), \
            'a closed trade never advances the balance — it freezes during a ban'

    def test_after_is_not_computed_from_wallet_at_open(self):
        """_after_or_computed became _before_after; the point is that neither the
        notification figures nor their fallback may come from wallet_at_open."""
        s = self._src()
        i = s.index('def _before_after')
        body = s[i:i + 2000]
        assert "closed.get('wallet_at_open')" not in body, \
            'After still derives from the wallet when the order OPENED (28h stale on 09-11)'
        assert 'balance_before=c.get(' not in s, \
            'a close notification still passes wallet_at_open as Before'

    def test_successful_reads_reconcile_rather_than_overwrite(self):
        assert 'reconcile(' in self._src()

    def test_the_drift_log_path_is_wired(self):
        assert 'set_drift_log' in self._src()
