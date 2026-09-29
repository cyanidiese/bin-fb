"""Symbol weights: audit trail, rebalancer, shadow calculator / suggestions panel, drag reweight.

Sections (one per former test file):
  - weight change audit            (was test_weight_audit.py)
  - weight rebalancer              (was test_weight_rebalancer.py)
  - weight shadow + suggestions    (was test_weight_shadow.py; evaluates TS through node/jiti)
  - reweight after drag            (was test_reweight_after_drag.py; compiles TS once per module)
"""
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot.weight_audit import MAX_CHANGES, audit, history
from bot.weight_rebalancer import WeightRebalancer
from tests.factories import ROOT, src


# =========================================================================== #
# Weight change audit (was test_weight_audit.py)                              #
# =========================================================================== #
# `symbol_weights` changes must leave a trace.
#
# Weights are the biggest lever on real-order sizing — weight 0 is a hard gate on real
# orders, and under TATS the weights split the deployable budget. They can be changed from
# the dashboard, by hand over SSH, by `weight_rebalancer`, or by editing `risk_config.json`
# directly, and none of those left any record.
#
# What that cost, 2026-09-11: ETHFIUSDT went 9 -> 3 and SOLUSDT 8 -> 3. With nothing
# recorded, the change was attributed to `weight_rebalancer` — which was disabled
# (`enabled: false`, zero log lines). The edits had been made by hand. A whole line of
# investigation was spent on a question one log line answers.
#
# So this is a detector, not a hook on the writers: it diffs the live config against the
# last snapshot, which means a manual edit or a future code path cannot bypass it.

@pytest.fixture
def store(tmp_path):
    return tmp_path / 'weight_changes_test.json'


class TestFirstRun:
    def test_it_records_nothing_on_a_fresh_file(self, store):
        """No prior state means no change — inventing 0 -> 9 for every symbol would
        bury the real edits that follow."""
        assert audit(store, {'REZUSDT': 14, 'INJUSDT': 9}) == []
        assert history(store) == []

    def test_it_still_stores_the_snapshot(self, store):
        audit(store, {'REZUSDT': 14})
        assert json.loads(store.read_text())['snapshot'] == {'REZUSDT': 14.0}


class TestDetectingChanges:
    def test_the_measured_case(self, store):
        """ETHFIUSDT 9 -> 3 and SOLUSDT 8 -> 3, the edit that was mis-attributed."""
        audit(store, {'ETHFIUSDT': 9, 'SOLUSDT': 8, 'REZUSDT': 14})
        changes = audit(store, {'ETHFIUSDT': 3, 'SOLUSDT': 3, 'REZUSDT': 14})
        got = {c['symbol']: (c['old'], c['new']) for c in changes}
        assert got == {'ETHFIUSDT': (9.0, 3.0), 'SOLUSDT': (8.0, 3.0)}

    def test_an_unchanged_symbol_is_not_reported(self, store):
        audit(store, {'REZUSDT': 14, 'INJUSDT': 9})
        changes = audit(store, {'REZUSDT': 14, 'INJUSDT': 8})
        assert [c['symbol'] for c in changes] == ['INJUSDT']

    def test_no_change_writes_nothing(self, store):
        audit(store, {'REZUSDT': 14})
        assert audit(store, {'REZUSDT': 14}) == []
        assert history(store) == []

    def test_a_new_symbol_reports_old_as_none(self, store):
        audit(store, {'REZUSDT': 14})
        changes = audit(store, {'REZUSDT': 14, 'ENAUSDT': 5})
        assert changes[0]['symbol'] == 'ENAUSDT'
        assert changes[0]['old'] is None and changes[0]['new'] == 5.0

    def test_a_removed_symbol_is_reported(self, store):
        """Dropping a symbol from the config stops its real orders exactly as
        setting it to 0 does, so it must not vanish silently."""
        audit(store, {'REZUSDT': 14, 'AVAXUSDT': 3})
        changes = audit(store, {'REZUSDT': 14})
        assert changes[0]['symbol'] == 'AVAXUSDT'
        assert changes[0]['old'] == 3.0 and changes[0]['new'] is None

    def test_the_zero_gate_is_recorded(self, store):
        """weight 0 is a hard gate on real orders — the most important edit of all."""
        audit(store, {'EIGENUSDT': 8})
        changes = audit(store, {'EIGENUSDT': 0})
        assert changes[0]['old'] == 8.0 and changes[0]['new'] == 0.0

    def test_int_to_float_is_not_a_change(self, store):
        """The dashboard writes ints, hand edits write floats. 9 -> 9.0 is noise."""
        audit(store, {'INJUSDT': 9})
        assert audit(store, {'INJUSDT': 9.0}) == []

    def test_the_source_is_recorded(self, store):
        audit(store, {'INJUSDT': 9})
        changes = audit(store, {'INJUSDT': 3}, source='dashboard')
        assert changes[0]['source'] == 'dashboard'

    def test_every_change_is_timestamped(self, store):
        audit(store, {'INJUSDT': 9})
        changes = audit(store, {'INJUSDT': 3})
        assert 'timestamp' in changes[0]


class TestHistory:
    def test_it_accumulates_across_edits(self, store):
        audit(store, {'INJUSDT': 9})
        audit(store, {'INJUSDT': 5})
        audit(store, {'INJUSDT': 2})
        assert [(c['old'], c['new']) for c in history(store)] == [(9.0, 5.0), (5.0, 2.0)]

    def test_it_can_be_scoped_to_one_symbol(self, store):
        audit(store, {'INJUSDT': 9, 'REZUSDT': 14})
        audit(store, {'INJUSDT': 5, 'REZUSDT': 10})
        assert [c['symbol'] for c in history(store, 'INJUSDT')] == ['INJUSDT']

    @staticmethod
    def _full(store):
        # A store already at the cap, written once; audit() then pushes it over. Driving
        # all MAX_CHANGES through audit() rewrote the file each time (~3.6 s per test).
        store.write_text(json.dumps({
            'snapshot': {'X': float(MAX_CHANGES - 1)},
            'changes': [{'timestamp': '2026-01-01T00:00:00+00:00', 'symbol': 'X',
                         'old': float(i - 1), 'new': float(i), 'source': 'seed'}
                        for i in range(MAX_CHANGES)]}))
        for i in range(MAX_CHANGES, MAX_CHANGES + 50):
            audit(store, {'X': i})

    def test_it_is_capped(self, store):
        self._full(store)
        assert len(history(store)) == MAX_CHANGES

    def test_the_cap_keeps_the_newest(self, store):
        self._full(store)
        assert history(store)[-1]['new'] == float(MAX_CHANGES + 49)


class TestItNeverBreaksTrading:
    def test_a_corrupt_store_does_not_raise(self, store):
        store.write_text('{not json')
        assert audit(store, {'INJUSDT': 9}) == []

    def test_an_unwritable_path_does_not_raise(self, tmp_path):
        audit(tmp_path / 'no' / 'such' / 'dir' / 'w.json', {'INJUSDT': 9})

    def test_junk_weights_are_skipped_not_fatal(self, store):
        audit(store, {'INJUSDT': 9})
        changes = audit(store, {'INJUSDT': 9, 'BAD': 'not-a-number'})
        assert changes == []

    def test_empty_weights_are_handled(self, store):
        assert audit(store, {}) == []

    def test_history_of_a_missing_file_is_empty(self, tmp_path):
        assert history(tmp_path / 'absent.json') == []


class TestItIsWiredIntoTheCandlePath:
    """A detector only works if it actually runs on every config reload."""

    def test_main_audits_the_weights(self):
        assert 'weight_audit' in src('main.py'), \
            'weight changes are not audited — a manual edit leaves no trace again'


# =========================================================================== #
# Weight rebalancer (was test_weight_rebalancer.py)                           #
# =========================================================================== #

def _make_rebalancer(**cfg_overrides):
    """Build a WeightRebalancer with all deps mocked."""
    cfg = {
        "enabled": True,
        "rebalance_candles": 4,
        "backtest_window_candles": 4,
        "real_pnl_alpha": 0.5,
        "blend_rate": 0.2,
        "weight_floor_ratio": 0.3,
        **cfg_overrides,
    }
    sym_reg = MagicMock()
    risk_mgr = MagicMock()
    settings = MagicMock()
    return WeightRebalancer(
        symbol_registry=sym_reg,
        risk_manager=risk_mgr,
        settings=settings,
        get_klines_fn=lambda s: [],
        candle_duration_ms=900_000,
        mode="test",
        risk_config_path=Path("/tmp/rc.json"),
        data_dir=Path("/tmp/data"),
        cfg=cfg,
    )


class TestRankNormalize:
    def test_three_values_ranked(self):
        r = _make_rebalancer()
        values = {"A": 10.0, "B": 5.0, "C": 1.0}
        result = r._rank_normalize(values)
        assert result["A"] == pytest.approx(1.0)
        assert result["C"] == pytest.approx(0.0)
        assert result["B"] == pytest.approx(0.5)

    def test_all_equal_scores_midpoint(self):
        r = _make_rebalancer()
        values = {"A": 3.0, "B": 3.0, "C": 3.0}
        result = r._rank_normalize(values)
        for v in result.values():
            assert v == pytest.approx(0.5)

    def test_single_symbol_returns_one(self):
        r = _make_rebalancer()
        result = r._rank_normalize({"X": 7.5})
        assert result["X"] == pytest.approx(1.0)

    def test_empty_returns_empty(self):
        r = _make_rebalancer()
        assert r._rank_normalize({}) == {}


class TestFilterRealOrders:
    def _write_orders(self, tmp_path: Path, symbol: str, orders: list) -> None:
        path = tmp_path / f"real_orders_{symbol}_test.json"
        path.write_text(json.dumps(orders))

    def test_returns_orders_in_window(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        recent_ct = datetime.fromtimestamp((now_ms - 1000) / 1000, tz=timezone.utc).isoformat()
        old_ct = datetime.fromtimestamp((now_ms - 99999999) / 1000, tz=timezone.utc).isoformat()
        self._write_orders(tmp_path, "BTCUSDT", [
            {"close_time": recent_ct, "pnl_usdt": 5.0},
            {"close_time": old_ct, "pnl_usdt": -2.0},
        ])
        result = r._filter_real_orders("BTCUSDT", window_start_ms=now_ms - 10000)
        assert len(result) == 1
        assert result[0]["pnl_usdt"] == 5.0

    def test_missing_file_returns_empty(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        result = r._filter_real_orders("XYZUSDT", window_start_ms=0)
        assert result == []

    def test_all_old_orders_excluded(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        old_ct = "2020-01-01T00:00:00+00:00"
        self._write_orders(tmp_path, "ETHUSDT", [
            {"close_time": old_ct, "pnl_usdt": 10.0},
        ])
        result = r._filter_real_orders("ETHUSDT", window_start_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        assert result == []


class TestCalcScores:
    def test_equal_signals_equal_scores(self):
        r = _make_rebalancer()
        bt = {"A": 2.0, "B": 2.0}
        pnl = {"A": 0.0, "B": 0.0}
        scores = r._calc_scores(bt, pnl, alpha=0.5)
        assert scores["A"] == pytest.approx(scores["B"])

    def test_better_backtest_gets_higher_score(self):
        r = _make_rebalancer()
        bt = {"A": 5.0, "B": 1.0}
        pnl = {"A": 0.0, "B": 0.0}
        scores = r._calc_scores(bt, pnl, alpha=0.0)  # backtest only
        assert scores["A"] > scores["B"]

    def test_better_pnl_gets_higher_score(self):
        r = _make_rebalancer()
        bt = {"A": 0.0, "B": 0.0}
        pnl = {"A": 10.0, "B": -3.0}
        scores = r._calc_scores(bt, pnl, alpha=1.0)  # real P&L only
        assert scores["A"] > scores["B"]


class TestBlendWeights:
    def test_blend_moves_toward_score(self):
        r = _make_rebalancer()
        current = {"A": 0.5, "B": 0.5}
        scores = {"A": 1.0, "B": 0.0}
        result = r._blend_weights(current, scores, blend_rate=0.2, floor_ratio=0.0)
        assert result["A"] > 0.5
        assert result["B"] < 0.5

    def test_floor_prevents_low_weight(self):
        r = _make_rebalancer()
        current = {"A": 0.9, "B": 0.1}
        scores = {"A": 1.0, "B": 0.0}
        # Without floor B would blend to ~0.05; floor=0.3/2=0.15 lifts it before renorm.
        # After the final renorm B lands at ~0.136, which must exceed the no-floor value.
        result_with_floor = r._blend_weights(current, scores, blend_rate=0.5, floor_ratio=0.3)
        result_no_floor = r._blend_weights(current, scores, blend_rate=0.5, floor_ratio=0.0)
        assert result_with_floor["B"] > result_no_floor["B"]

    def test_weights_sum_to_one(self):
        r = _make_rebalancer()
        current = {"A": 0.4, "B": 0.3, "C": 0.3}
        scores = {"A": 0.9, "B": 0.05, "C": 0.05}
        result = r._blend_weights(current, scores, blend_rate=0.15, floor_ratio=0.3)
        assert sum(result.values()) == pytest.approx(1.0)

    def test_empty_scores_returns_current(self):
        r = _make_rebalancer()
        current = {"A": 0.6, "B": 0.4}
        result = r._blend_weights(current, {}, blend_rate=0.15, floor_ratio=0.3)
        assert result == current

    def test_absent_symbol_gets_equal_share_default(self):
        r = _make_rebalancer()
        # "A" not in current_weights — should get 1/n = 0.5 as starting point
        current = {}
        scores = {"A": 1.0, "B": 0.0}
        result = r._blend_weights(current, scores, blend_rate=0.2, floor_ratio=0.0)
        # A should end up with more than B
        assert result["A"] > result["B"]


class TestTrigger:
    def test_fires_after_n_candles(self):
        r = _make_rebalancer(rebalance_candles=3)
        r.enabled = True
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            for i in range(2):
                r.on_candle_close(1000 + i * 900_000)
            MockThread.assert_not_called()
            r.on_candle_close(1000 + 2 * 900_000)
            MockThread.assert_called_once()

    def test_skips_when_already_running(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = True
        r._running.set()
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            r.on_candle_close(1000)
            MockThread.assert_not_called()

    def test_no_op_when_disabled(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = False
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            r.on_candle_close(1000)
            MockThread.assert_not_called()

    def test_same_candle_ts_counted_once(self):
        r = _make_rebalancer(rebalance_candles=2)
        r.enabled = True
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            # Call 3 times with same ts (simulating 3 symbols in same candle)
            for _ in range(3):
                r.on_candle_close(1000)
            MockThread.assert_not_called()  # still only 1 candle counted
            # Now a new candle ts — should trigger (counter hits 2)
            r.on_candle_close(1000 + 900_000)
            MockThread.assert_called_once()


# =========================================================================== #
# Weight shadow calculator + suggestions panel (was test_weight_shadow.py)    #
# =========================================================================== #

SHADOW_TS = 'dashboard/app/api/trades/_weight-shadow.ts'
SHADOW_SRC = src(SHADOW_TS)
APPLY = src('dashboard/app/api/weight-suggestions/apply/route.ts')
PANEL = src('dashboard/components/risk/WeightSuggestions.tsx')


def _eval_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('ev', ROOT / 'scripts/eval_weight_shadow.py')
    ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
    monkeypatch.setattr(ev, 'DATA', tmp_path)
    return ev


def _bws():
    spec = importlib.util.spec_from_file_location('bws', ROOT / 'scripts/backfill_weight_shadow.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _ts(expr: str):
    """Evaluate a JS expression against the real dashboard module (`m`) through node."""
    node = shutil.which('node')
    jiti = ROOT / 'dashboard/node_modules/jiti'
    if not node or not jiti.exists():
        pytest.skip('node / dashboard node_modules not available')
    js = f"""
const jiti=require({json.dumps(str(jiti))})({json.dumps(str(ROOT/'dashboard'/'jiti-entry.js'))},{{alias:{{'@':{json.dumps(str(ROOT/'dashboard'))}}}}})
const m=jiti({json.dumps(str(ROOT/SHADOW_TS))})
console.log(JSON.stringify({expr}))"""
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, cwd=ROOT / 'dashboard', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _row(w, pct, n):
    return {'w': w, 'lock': None, 'p7': [pct, n, 'p'], 'p14': [pct, n, 'p'],
            'policies': {'static': w, 'tilt': w, 'brake': w}}


def _top(p, n): return [p, n, 'preset']


def _book():
    """The user's case (2026-09-28): INJ losing with weight 9 must not out-rank WLD/ARB/EGLD."""
    return {
        'EGLDUSDT': {'p7': _top(77.7, 40), 'p14': _top(33.2, 56)},
        'WLDUSDT':  {'p7': _top(38.7, 7),  'p14': _top(111.1, 24)},
        'ARBUSDT':  {'p7': _top(30.4, 3),  'p14': _top(87.1, 19)},
        'APTUSDT':  {'p7': _top(33.4, 8),  'p14': _top(47.0, 21)},
        'LTCUSDT':  {'p7': _top(25.0, 11), 'p14': _top(34.3, 16)},
        'INJUSDT':  {'p7': _top(-22.9, 3), 'p14': _top(-15.3, 12)},
        'SOLUSDT':  {'p7': _top(-20.1, 40), 'p14': _top(-9.7, 47)},
    }


class TestWeightShadow:
    """Weight shadow calculator — records proposed weights, never applies them.
    Spec: docs/specs/2026-09-28-weight-shadow-calculator.md"""

    def test_shadow_never_writes_risk_config(self):
        assert 'updateRiskConfig' not in SHADOW_SRC and 'saveRiskPatch' not in SHADOW_SRC and 'writeFileSync' not in SHADOW_SRC
        assert 'appendFileSync' in SHADOW_SRC

    def test_worker_records_once_per_day(self):
        inst = src('dashboard/instrumentation.ts')
        assert 'recordShadow(mode)' in inst
        assert 'if (lastDay(file) === snap.day) return null' in SHADOW_SRC

    def test_eval_scores_policies_against_forward_results(self, tmp_path, monkeypatch, capsys):
        ev = _eval_module(tmp_path, monkeypatch)
        snap = {'day': '2026-09-01', 'mode': 'live', 'symbols': {
            'AAAUSDT': {'policies': {'static': 1, 'tilt': 1.3, 'brake': 1}},
            'BBBUSDT': {'policies': {'static': 1, 'tilt': 0.7, 'brake': 0.5}}}}
        (tmp_path / 'weight_shadow_live.jsonl').write_text(json.dumps(snap) + '\n')
        order = lambda pnl: {'entry_price': 1, 'quantity': 10, 'leverage': 5, 'pnl_usdt': pnl,
                             'result': 'win', 'open_time': '2026-09-02T10:00:00+00:00'}
        (tmp_path / 'virtual_orders_rank1_AAAUSDT_live.json').write_text(json.dumps([order(1.0)]))   # +50 %
        (tmp_path / 'virtual_orders_rank1_BBBUSDT_live.json').write_text(json.dumps([order(-1.0)]))  # -50 %
        ev.evaluate('live', 7)
        out = capsys.readouterr().out
        assert '1 with a complete 7-day forward window' in out
        static = float(re.search(r'static\s+mean\s+([+-][\d.]+)', out).group(1))
        tilt = float(re.search(r'tilt\s+mean\s+([+-][\d.]+)', out).group(1))
        assert static == 0.0 and tilt == 15.0      # (1.3*50 - 0.7*50) / 2.0

    # ── Weight suggestions panel: see and approve ────────────────────────────

    def test_apply_merges_onto_a_fresh_read_of_that_mode_only(self):
        assert 'updateRiskConfig(body.mode' in APPLY
        assert 'symbol_weights: w' in APPLY

    def test_apply_allows_on_off_but_only_for_known_symbols(self):
        """Every symbol can be (re)weighted from the panel, including 0 <-> >0, which switches
        real orders on/off — the confirmation must say so. Negative weights are rejected."""
        assert 'w < 0' in APPLY
        assert "has no weight entry in this mode" in APPLY
        assert 'turns REAL orders ON' in PANEL and 'turns REAL orders OFF' in PANEL

    def test_panel_lists_every_symbol_not_only_funded_ones(self):
        assert 'filter(([, r]) => r.w > 0).sort' not in PANEL
        assert 'isCandidate' in PANEL and 'disabled.has(sym)' in PANEL

    def test_panel_asks_before_applying_and_warns_on_tats_threshold(self):
        assert 'window.confirm(' in PANEL
        assert 'crosses tats_min_weight' in PANEL

    def test_panel_is_on_the_risk_page_per_mode(self):
        page = src('dashboard/app/risk/page.tsx')
        assert '<WeightSuggestions key={configMode ?? dataMode} mode={configMode ?? dataMode}' in page

    # ── Backfill: reconstruct past snapshots from order history ─────────────

    def test_backfill_policies_match_the_dashboard_formulas(self):
        m = _bws()
        assert m.tilt(10, (100.0, 25, 'p')) == 10 * min(1.3, 1 + 0.3 * __import__('math').tanh(2))
        assert m.tilt(10, (100.0, 19, 'p')) == 10          # < 20 trades: unchanged
        assert m.brake(10, (-30.0, 30, 'p')) == 5          # sustained loss: halved
        assert m.brake(10, (-29.9, 30, 'p')) == 10
        assert m.brake(10, (-80.0, 29, 'p')) == 10         # < 30 trades: unchanged

    def test_backfill_uses_only_trades_before_the_day(self):
        m = _bws()
        s = m.Series([(100.0, 5.0), (200.0, -2.0), (300.0, 7.0)])
        assert s.window(0, 300) == (3.0, 2)                # the 300 trade is not "before"
        top = m.top_row({'a': s, 'b': m.Series([(150.0, 1.0)])}, None, 0, 250)
        assert top[2] == 'a' and top[1] == 2
        assert m.top_row({'a': s}, 'locked', 0, 250) == (None, 0, 'locked')

    # ── Every symbol gets a tilt/brake value; doubts are flagged, columns sortable ──

    def test_panel_sorts_every_column_and_remembers_it(self):
        assert "'risk-weight-suggestions:sort'" in PANEL
        for col in ("'symbol'", "'weight'", "'p7'", "'p14'", "'tilt'", "'brake'"):
            assert f'[{col},' in PANEL, col
        assert 'toggleSort(col)' in PANEL

    # ── Anchoring: applying a suggestion twice must not compound ─────────────

    def test_apply_logs_the_policy_that_set_each_weight(self):
        assert 'logApplied(body.mode' in APPLY
        assert "apply({ [sym]: v }, pol)" in PANEL
        shadow = SHADOW_SRC
        assert "if (e.policy !== 'brake') anchor = null" in shadow          # typed value or tilt = new base
        assert 'Math.abs((currentWeights[sym] ?? 0) - last.new) < 1e-9' in shadow  # changed elsewhere = new base

    # ── Score allocation (the Tilt column) ───────────────────────────────────

    def test_score_formula_matches_the_approved_table(self):
        sc = _ts(f"m.symbolScore({json.dumps(_book()['EGLDUSDT'])})")
        assert abs(sc['s7'] - 62.16) < 0.05 and abs(sc['s14'] - 24.49) < 0.05 and abs(sc['base'] - 43.33) < 0.05
        sc = _ts(f"m.symbolScore({json.dumps(_book()['WLDUSDT'])})")
        assert abs(sc['base'] - 38.25) < 0.1

    def test_few_profitable_presets_in_7_days_is_a_bad_sign(self):
        """User rule 2026-09-28: 2 of 81 profitable presets in 7 days is a bad sign."""
        wld = {**_book()['WLDUSDT'], 'c7': [2, 81]}
        sc = _ts(f"m.symbolScore({json.dumps(wld)})")
        assert abs(sc['breadth'] - (2 / 81) / 0.10) < 0.01          # x0.25
        assert sc['score'] < sc['base'] * 0.31
        broad = _ts(f"m.symbolScore({json.dumps({**_book()['WLDUSDT'], 'c7': [30, 81]})})")
        assert broad['breadth'] == 1

    def test_recent_profit_above_half_of_the_fortnight_is_a_good_sign(self):
        """User rule: 7d Profit% > half of 14d Profit% is a good sign (x1.25)."""
        egld = _ts(f"m.symbolScore({json.dumps({**_book()['EGLDUSDT'], 'c7': [30, 81]})})")   # 77.7 > 33.2/2
        assert egld['momentum'] is True and abs(egld['score'] - egld['base'] * 1.25) < 0.01
        wld = _ts(f"m.symbolScore({json.dumps({**_book()['WLDUSDT'], 'c7': [30, 81]})})")     # 38.7 < 111.1/2
        assert wld['momentum'] is False and abs(wld['score'] - wld['base']) < 0.01

    def test_weights_are_whole_numbers_adding_up_to_the_budget(self):
        a = _ts(f"m.scoreAllocation({json.dumps(_book())}, {{disabled:new Set(), n:10, budget:32.5}})")
        vals = [v['value'] for v in a.values()]
        assert all(float(x).is_integer() for x in vals)
        assert sum(vals) == 33                                        # Math.round(32.5)

    def test_promising_symbols_get_more_and_losers_get_zero(self):
        a = _ts(f"m.scoreAllocation({json.dumps(_book())}, {{disabled:new Set(['APTUSDT']), n:10, budget:32.5}})")
        # EGLD and WLD both hit the 30 % cap in this small book; the order still holds
        assert a['EGLDUSDT']['value'] >= a['WLDUSDT']['value'] > a['ARBUSDT']['value'] > a['LTCUSDT']['value'] > 0
        assert a['EGLDUSDT']['score'] > a['WLDUSDT']['score']
        assert a['INJUSDT']['value'] == 0 and a['SOLUSDT']['value'] == 0         # losers
        assert a['APTUSDT']['value'] == 0 and a['APTUSDT']['rank'] is None     # disabled
        assert sum(v['value'] for v in a.values()) == 33                        # the whole budget, whole numbers
        assert max(v['value'] for v in a.values()) <= 0.30 * 32.5 + 1          # 30 % cap (+ rounding)

    def test_only_the_top_n_symbols_take_part(self):
        a = _ts(f"m.scoreAllocation({json.dumps(_book())}, {{disabled:new Set(), n:2, budget:30}})")
        funded = {s for s, v in a.items() if v['value'] > 0}
        assert funded == {'EGLDUSDT', 'WLDUSDT'}
        assert a['EGLDUSDT']['value'] + a['WLDUSDT']['value'] == 30            # cap relaxes to 1/N
        assert a['ARBUSDT']['inTopN'] is False and a['ARBUSDT']['rank'] == 3

    def test_tilt_flags_turning_orders_on_and_off(self):
        book = _book()
        alloc = _ts(f"m.scoreAllocation({json.dumps(book)}, {{disabled:new Set(), n:2, budget:30}})")
        inj = {'w': 9, 'lock': None, 'p7': book['INJUSDT']['p7'], 'p14': book['INJUSDT']['p14'],
               'policies': {'static': 9, 'tilt': 9, 'brake': 9}}
        s = _ts(f"m.panelSuggestions({json.dumps(inj)}, {{disabled:false, alloc:{json.dumps(alloc['INJUSDT'])}, n:2, eligible:5, budget:30}})")
        assert s['tilt']['value'] == 0 and any('turns REAL orders OFF' in f for f in s['tilt']['flags'])
        wld = {'w': 0, 'lock': None, 'p7': book['WLDUSDT']['p7'], 'p14': book['WLDUSDT']['p14'],
               'policies': {'static': 0, 'tilt': 0, 'brake': 0}}
        s = _ts(f"m.panelSuggestions({json.dumps(wld)}, {{disabled:false, alloc:{json.dumps(alloc['WLDUSDT'])}, n:2, eligible:5, budget:30}})")
        assert s['tilt']['value'] > 0 and any('turns REAL orders ON' in f for f in s['tilt']['flags'])
        assert 'score' in s['tilt']['note'] and 'rank 2' in s['tilt']['note']

    def test_tilt_history_flag_uses_this_formulas_record(self):
        alloc = {'score': 40, 's7': 60, 's14': 20, 'rank': 1, 'inTopN': True, 'value': 9.75}
        row = {'w': 0, 'lock': None, 'p7': _top(70, 40), 'p14': _top(30, 50), 'policies': {'static': 0, 'tilt': 0, 'brake': 0}}
        hist = {'top': {'days': 105, 'mean': -0.6, 'vs_equal': -1.7, 'vs_current': -6, 'better_equal': 45}}
        s = _ts(f"m.panelSuggestions({json.dumps(row)}, {{disabled:false, alloc:{json.dumps(alloc)}, n:5, eligible:12, budget:32.5, scoreHist:{json.dumps(hist)}}})")
        assert any('lost to simply equal-weighting' in f for f in s['tilt']['flags'])

    def test_brake_still_does_not_compound(self):
        alloc = {'score': -10, 's7': 0, 's14': -20, 'rank': None, 'inTopN': False, 'value': 0}
        row = {'w': 1.5, 'lock': None, 'p7': _top(0.9, 32), 'p14': _top(-31.79, 45), 'policies': {'static': 1.5, 'tilt': 0, 'brake': 1.5}}
        anchor = {'base': 3, 'since': 1790520000000, 'policy': 'brake'}
        s = _ts(f"m.panelSuggestions({json.dumps(row)}, {{disabled:false, alloc:{json.dumps(alloc)}, n:5, eligible:12, budget:32.5, anchor:{json.dumps(anchor)}}})")
        assert s['brake']['value'] == 1.5 and 'already braked' in s['brake']['note']

    def test_backfill_uses_the_same_score_allocation(self):
        m = _bws()
        rows = {k: {'w': 0, **v} for k, v in _book().items()}
        a = m.score_allocation(rows, 10, 32.5, disabled={'APTUSDT'})
        ts = _ts(f"m.scoreAllocation({json.dumps(_book())}, {{disabled:new Set(['APTUSDT']), n:10, budget:32.5}})")
        for sym in rows:
            assert a[sym] == ts[sym]['value'], sym                            # identical whole numbers

    # ── Panel: N field, recalculate, lock toggle, preset counts ──────────────

    def test_panel_has_symbols_involved_field_and_recalculate(self):
        assert 'Symbols involved' in PANEL and 'risk-weight-suggestions:n:' in PANEL
        assert '↻ Recalculate' in PANEL and 'onClick={recalculate}' in PANEL
        route = src('dashboard/app/api/weight-suggestions/route.ts')
        assert "q.get('n')" in route and 'scoreAllocation(symbols, { disabled: disabledSet, n, budget })' in route

    def test_lock_icon_locks_the_top_preset_or_unlocks_then_recalculates(self):
        assert "const target = r.lock ? null : (r.p14[2] ?? r.p7[2])" in PANEL
        assert "fetch('/api/risk/lock-preset'" in PANEL and 'body: JSON.stringify({ symbol: sym, preset: target, mode })' in PANEL
        assert 'onClick={() => toggleLock(sym, r)}' in PANEL
        lock_fn = PANEL[PANEL.index('async function toggleLock'):PANEL.index('if (!data) return null')]
        assert 'load(nApplied ?? nInput)' in lock_fn and 'window.confirm(' in lock_fn

    def test_7d_and_14d_cells_show_profitable_preset_counts(self):
        assert 'presets are profitable' in PANEL
        assert "title={countsTip('7 days', r.c7, r.p7)}" in PANEL and "title={countsTip('14 days', r.c14, r.p14)}" in PANEL
        shadow = SHADOW_SRC
        assert "c7: counts(s.ranges['7d']), c14: counts(s.ranges['14d'])" in shadow

    def test_full_tilt_allocation_can_be_applied_including_zeros(self):
        assert "const tiltAll = changed('tilt', false)" in PANEL          # zeros included
        assert "brakeAll = changed('brake', true)" in PANEL                 # brake: unflagged only
        assert 'Apply tilt allocation' in PANEL

    def test_tilt_then_brake_does_not_halve_twice(self, tmp_path):
        """Verifier finding: tilt 2 -> 3, then brake 3 -> 1.5 within 14 days kept the TILT
        entry as the anchor, so the brake offered 0.75. The brake entry must anchor."""
        import time
        now = int(time.time() * 1000)
        data = tmp_path / 'data'; data.mkdir()
        (data / 'weight_suggestion_applies_test.jsonl').write_text(
            json.dumps({'ts': now - 7200_000, 'symbol': 'EIGENUSDT', 'policy': 'tilt', 'old': 2, 'new': 3}) + '\n' +
            json.dumps({'ts': now - 3600_000, 'symbol': 'EIGENUSDT', 'policy': 'brake', 'old': 3, 'new': 1.5}) + '\n')
        node = shutil.which('node')
        jiti = ROOT / 'dashboard/node_modules/jiti'
        if not node or not jiti.exists():
            pytest.skip('node not available')
        (tmp_path / 'dashboard').mkdir()
        js = f"""
const jiti=require({json.dumps(str(jiti))})({json.dumps(str(ROOT/'dashboard'/'jiti-entry.js'))},{{alias:{{'@':{json.dumps(str(ROOT/'dashboard'))}}}}})
const m=jiti({json.dumps(str(ROOT/SHADOW_TS))})
const a=m.anchors('test', {{EIGENUSDT: 1.5}})
const row={{w:1.5,lock:null,p7:[0.9,32,'p'],p14:[-31.79,45,'p'],policies:{{static:1.5,tilt:0,brake:1.5}}}}
const alloc={{score:-10,s7:0,s14:-20,rank:null,inTopN:false,value:0}}
console.log(JSON.stringify({{a, s: m.panelSuggestions(row,{{disabled:false,alloc,n:5,eligible:12,budget:32.5,anchor:a.EIGENUSDT}})}}))"""
        out = subprocess.run([node, '-e', js], capture_output=True, text=True, cwd=tmp_path / 'dashboard', timeout=60)
        assert out.returncode == 0, out.stderr
        r = json.loads(out.stdout.strip().splitlines()[-1])
        assert r['a']['EIGENUSDT']['policy'] == 'brake' and r['a']['EIGENUSDT']['base'] == 3
        assert r['s']['brake']['value'] == 1.5 and 'already braked' in r['s']['brake']['note']

    def test_budget_is_a_fixed_whole_number_not_the_current_weight_sum(self):
        # a decimal apply left the weights summing to 1.26 — that must not become the budget
        assert _ts("m.weightBudget({symbol_weights:{A:0.36,B:0.9}})") == 33
        assert _ts("m.weightBudget({weight_budget:40.4})") == 40
        assert _ts("m.weightBudget({weight_budget:0})") == 33
        bws = _bws()
        assert bws.weight_budget({}) == 33 and bws.weight_budget({"weight_budget": 40.4}) == 40


# =========================================================================== #
# Reweight after drag (was test_reweight_after_drag.py)                       #
# =========================================================================== #
# Dragging a symbol must never activate one that is switched off.
#
# `onDragEnd` assigned `newWeights[sym] = n - i` to every row, rewriting the whole table by
# position. With 17 symbols the bottom row got weight 1, so a single drag activated all ten
# zero-weight symbols for real orders and discarded the hand-picked weights above it.
#
# Weight 0 is a deliberate off switch, so drag is asymmetric now: it can deactivate a
# symbol (drop it among the zeros) but never activate one.
#
# There is no JS test runner in this project, so the pure function is transpiled with the
# project's own tsc and executed under node. That gives real behavioural coverage rather
# than asserting on source strings -- this logic decides which symbols spend real money.

REWEIGHT_TS = ROOT / 'dashboard/components/risk/reweight.ts'

# The live table from the screenshot, in display order.
LIVE = [
    ('INJUSDT', 14), ('ETHFIUSDT', 13), ('AVAXUSDT', 10), ('SOLUSDT', 9),
    ('REZUSDT', 8), ('EIGENUSDT', 6), ('TIAUSDT', 4),
    ('DOGEUSDT', 0), ('JUPUSDT', 0), ('MEMEUSDT', 0), ('1000PEPEUSDT', 0),
    ('WLDUSDT', 0), ('1000SHIBUSDT', 0), ('BTCUSDT', 0), ('THETAUSDT', 0),
    ('APTUSDT', 0),
]
ORDER = [s for s, _ in LIVE]
WEIGHTS = {s: w for s, w in LIVE}


@pytest.fixture(scope='module')
def run():
    """Compile reweight.ts once per module, return a callable that invokes it under node.

    Requested only by the reweight-after-drag classes below (not autouse)."""
    if not shutil.which('node'):
        pytest.skip('node not available')
    tmp = Path(tempfile.mkdtemp())
    r = subprocess.run(
        ['npx', 'tsc', str(REWEIGHT_TS), '--outDir', str(tmp), '--module', 'commonjs',
         '--target', 'es2020'],
        cwd=ROOT / 'dashboard', capture_output=True, text=True,
    )
    js = tmp / 'reweight.js'
    if not js.exists():
        pytest.skip(f'tsc unavailable or failed: {r.stdout[-400:]}{r.stderr[-400:]}')

    def call(order, weights, moved):
        script = (
            f'const {{reweightAfterDrag}} = require({str(js)!r});'
            f'process.stdout.write(JSON.stringify(reweightAfterDrag('
            f'{json.dumps(order)},{json.dumps(weights)},{json.dumps(moved)})));'
        )
        out = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    return call


def _moved(order, sym, to):
    o = [s for s in order if s != sym]
    o.insert(to, sym)
    return o


class TestZeroStaysZero:
    def test_a_reorder_among_active_symbols_leaves_every_zero_at_zero(self, run):
        order = _moved(ORDER, 'TIAUSDT', 0)
        out = run(order, WEIGHTS, 'TIAUSDT')
        zeros = [s for s, w in LIVE if w == 0]
        assert all(out[s] == 0 for s in zeros), \
            {s: out[s] for s in zeros if out[s] != 0}

    def test_the_old_bug_is_gone(self, run):
        """n - i would have made the bottom row 1 and activated everything."""
        out = run(_moved(ORDER, 'TIAUSDT', 0), WEIGHTS, 'TIAUSDT')
        assert out['APTUSDT'] == 0
        assert sum(1 for v in out.values() if v > 0) == 7

    def test_dragging_a_zero_symbol_upward_does_not_activate_it(self, run):
        """The explicit ask: drag must not add weight to a zero-weight symbol."""
        order = _moved(ORDER, 'DOGEUSDT', 0)
        out = run(order, WEIGHTS, 'DOGEUSDT')
        assert out['DOGEUSDT'] == 0

    def test_dragging_a_zero_symbol_between_two_active_ones_keeps_it_off(self, run):
        order = _moved(ORDER, 'WLDUSDT', 3)
        out = run(order, WEIGHTS, 'WLDUSDT')
        assert out['WLDUSDT'] == 0

    def test_active_symbols_are_not_disturbed_by_a_zero_symbol_moving(self, run):
        out = run(_moved(ORDER, 'JUPUSDT', 2), WEIGHTS, 'JUPUSDT')
        for s, w in LIVE:
            if w > 0:
                assert out[s] == w, f'{s} changed from {w} to {out[s]}'


class TestDropAmongZerosDeactivates:
    def test_between_two_zeros_sets_zero(self, run):
        """The second explicit ask."""
        order = _moved(ORDER, 'SOLUSDT', 9)   # lands between JUPUSDT and MEMEUSDT
        out = run(order, WEIGHTS, 'SOLUSDT')
        assert out['SOLUSDT'] == 0

    def test_dropped_at_the_very_bottom_sets_zero(self, run):
        """Only one neighbour there, but it is the natural deactivate gesture."""
        order = _moved(ORDER, 'REZUSDT', len(ORDER) - 1)
        out = run(order, WEIGHTS, 'REZUSDT')
        assert out['REZUSDT'] == 0

    def test_the_others_keep_their_own_weights(self, run):
        out = run(_moved(ORDER, 'SOLUSDT', 9), WEIGHTS, 'SOLUSDT')
        assert out['INJUSDT'] == 14 and out['ETHFIUSDT'] == 13 and out['AVAXUSDT'] == 10

    def test_deactivating_leaves_every_other_weight_untouched(self, run):
        """Only the dragged symbol changes. Reassigning the pool by position would have
        shifted everyone below it up a notch -- switching SOLUSDT off would move TIAUSDT
        from 4 to 6, changing six allocations the user never dragged."""
        out = run(_moved(ORDER, 'SOLUSDT', 9), WEIGHTS, 'SOLUSDT')
        assert sorted((v for v in out.values() if v > 0), reverse=True) == [14, 13, 10, 8, 6, 4]
        for s, w in LIVE:
            if w > 0 and s != 'SOLUSDT':
                assert out[s] == w, f'{s} moved from {w} to {out[s]}'

    def test_a_zero_symbol_moved_among_zeros_is_a_no_op(self, run):
        out = run(_moved(ORDER, 'BTCUSDT', 8), WEIGHTS, 'BTCUSDT')
        assert out == WEIGHTS


class TestReorderingActiveSymbols:
    def test_priority_follows_the_new_position(self, run):
        """TIAUSDT to the top takes the largest weight; the rest shift down."""
        out = run(_moved(ORDER, 'TIAUSDT', 0), WEIGHTS, 'TIAUSDT')
        assert out['TIAUSDT'] == 14
        assert out['INJUSDT'] == 13
        assert out['ETHFIUSDT'] == 10

    def test_the_weight_values_are_preserved_not_invented(self, run):
        """Reordering permutes the chosen values; it must not renumber to 7,6,5..."""
        out = run(_moved(ORDER, 'TIAUSDT', 0), WEIGHTS, 'TIAUSDT')
        assert sorted((v for v in out.values() if v > 0), reverse=True) == [14, 13, 10, 9, 8, 6, 4]

    def test_the_active_count_is_unchanged(self, run):
        out = run(_moved(ORDER, 'EIGENUSDT', 1), WEIGHTS, 'EIGENUSDT')
        assert sum(1 for v in out.values() if v > 0) == 7

    def test_top_row_is_never_treated_as_among_zeros(self, run):
        out = run(_moved(ORDER, 'TIAUSDT', 0), WEIGHTS, 'TIAUSDT')
        assert out['TIAUSDT'] > 0


class TestEdges:
    def test_an_all_zero_table_stays_all_zero(self, run):
        w = {s: 0 for s in ORDER}
        out = run(ORDER, w, 'DOGEUSDT')
        assert all(v == 0 for v in out.values())

    def test_a_missing_weight_counts_as_zero(self, run):
        w = dict(WEIGHTS)
        del w['BTCUSDT']
        out = run(_moved(ORDER, 'BTCUSDT', 0), w, 'BTCUSDT')
        assert out['BTCUSDT'] == 0

    def test_a_negative_weight_counts_as_zero(self, run):
        w = dict(WEIGHTS, BTCUSDT=-5)
        out = run(_moved(ORDER, 'BTCUSDT', 1), w, 'BTCUSDT')
        assert out['BTCUSDT'] == 0

    def test_every_symbol_gets_a_weight(self, run):
        """A missing key would read as 1 downstream, not 0 — the default is `?? 1`."""
        out = run(_moved(ORDER, 'SOLUSDT', 9), WEIGHTS, 'SOLUSDT')
        assert set(out) == set(ORDER)
