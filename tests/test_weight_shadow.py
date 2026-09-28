"""Weight shadow calculator — records proposed weights, never applies them.
Spec: docs/specs/2026-09-28-weight-shadow-calculator.md"""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'dashboard/app/api/trades/_weight-shadow.ts').read_text()



def test_shadow_never_writes_risk_config():
    assert 'updateRiskConfig' not in SRC and 'saveRiskPatch' not in SRC and 'writeFileSync' not in SRC
    assert 'appendFileSync' in SRC


def test_worker_records_once_per_day():
    inst = (ROOT / 'dashboard/instrumentation.ts').read_text()
    assert 'recordShadow(mode)' in inst
    assert 'if (lastDay(file) === snap.day) return null' in SRC


def _eval_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('ev', ROOT / 'scripts/eval_weight_shadow.py')
    ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
    monkeypatch.setattr(ev, 'DATA', tmp_path)
    return ev


def test_eval_scores_policies_against_forward_results(tmp_path, monkeypatch, capsys):
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


# ── Weight suggestions panel: see and approve ────────────────────────────────

APPLY = (ROOT / 'dashboard/app/api/weight-suggestions/apply/route.ts').read_text()
PANEL = (ROOT / 'dashboard/components/risk/WeightSuggestions.tsx').read_text()


def test_apply_merges_onto_a_fresh_read_of_that_mode_only():
    assert 'updateRiskConfig(body.mode' in APPLY
    assert 'symbol_weights: w' in APPLY


def test_apply_allows_on_off_but_only_for_known_symbols():
    """Every symbol can be (re)weighted from the panel, including 0 <-> >0, which switches
    real orders on/off — the confirmation must say so. Negative weights are rejected."""
    assert 'w < 0' in APPLY
    assert "has no weight entry in this mode" in APPLY
    assert 'turns REAL orders ON' in PANEL and 'turns REAL orders OFF' in PANEL


def test_panel_lists_every_symbol_not_only_funded_ones():
    assert 'filter(([, r]) => r.w > 0).sort' not in PANEL
    assert 'isCandidate' in PANEL and 'disabled.has(sym)' in PANEL


def test_panel_asks_before_applying_and_warns_on_tats_threshold():
    assert 'window.confirm(' in PANEL
    assert 'crosses tats_min_weight' in PANEL


def test_panel_is_on_the_risk_page_per_mode():
    page = (ROOT / 'dashboard/app/risk/page.tsx').read_text()
    assert '<WeightSuggestions key={configMode ?? dataMode} mode={configMode ?? dataMode}' in page


# ── Backfill: reconstruct past snapshots from order history ─────────────────

def _bws():
    spec = importlib.util.spec_from_file_location('bws', ROOT / 'scripts/backfill_weight_shadow.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_backfill_policies_match_the_dashboard_formulas():
    m = _bws()
    assert m.tilt(10, (100.0, 25, 'p')) == 10 * min(1.3, 1 + 0.3 * __import__('math').tanh(2))
    assert m.tilt(10, (100.0, 19, 'p')) == 10          # < 20 trades: unchanged
    assert m.brake(10, (-30.0, 30, 'p')) == 5          # sustained loss: halved
    assert m.brake(10, (-29.9, 30, 'p')) == 10
    assert m.brake(10, (-80.0, 29, 'p')) == 10         # < 30 trades: unchanged


def test_backfill_uses_only_trades_before_the_day():
    m = _bws()
    s = m.Series([(100.0, 5.0), (200.0, -2.0), (300.0, 7.0)])
    assert s.window(0, 300) == (3.0, 2)                # the 300 trade is not "before"
    top = m.top_row({'a': s, 'b': m.Series([(150.0, 1.0)])}, None, 0, 250)
    assert top[2] == 'a' and top[1] == 2
    assert m.top_row({'a': s}, 'locked', 0, 250) == (None, 0, 'locked')



# ── Every symbol gets a tilt/brake value; doubts are flagged, columns sortable ──

import json as _json
import shutil
import subprocess


def _ts(expr: str):
    """Evaluate a JS expression against the real dashboard module (`m`) through node."""
    node = shutil.which('node')
    jiti = ROOT / 'dashboard/node_modules/jiti'
    if not node or not jiti.exists():
        import pytest
        pytest.skip('node / dashboard node_modules not available')
    js = f"""
const jiti=require({_json.dumps(str(jiti))})({_json.dumps(str(ROOT/'dashboard'/'jiti-entry.js'))},{{alias:{{'@':{_json.dumps(str(ROOT/'dashboard'))}}}}})
const m=jiti({_json.dumps(str(ROOT/'dashboard/app/api/trades/_weight-shadow.ts'))})
console.log(JSON.stringify({expr}))"""
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, cwd=ROOT / 'dashboard', timeout=60)
    assert out.returncode == 0, out.stderr
    return _json.loads(out.stdout.strip().splitlines()[-1])


def _row(w, pct, n):
    return {'w': w, 'lock': None, 'p7': [pct, n, 'p'], 'p14': [pct, n, 'p'],
            'policies': {'static': w, 'tilt': w, 'brake': w}}






def test_panel_sorts_every_column_and_remembers_it():
    assert "'risk-weight-suggestions:sort'" in PANEL
    for col in ("'symbol'", "'weight'", "'p7'", "'p14'", "'tilt'", "'brake'"):
        assert f'[{col},' in PANEL, col
    assert 'toggleSort(col)' in PANEL



# ── Anchoring: applying a suggestion twice must not compound ─────────────────



def test_apply_logs_the_policy_that_set_each_weight():
    assert 'logApplied(body.mode' in APPLY
    assert "apply({ [sym]: v }, pol)" in PANEL
    shadow = (ROOT / 'dashboard/app/api/trades/_weight-shadow.ts').read_text()
    assert "if (e.policy !== 'brake') anchor = null" in shadow          # typed value or tilt = new base
    assert 'Math.abs((currentWeights[sym] ?? 0) - last.new) < 1e-9' in shadow  # changed elsewhere = new base



# ── Score allocation (the Tilt column) ───────────────────────────────────────

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


def test_score_formula_matches_the_approved_table():
    sc = _ts(f"m.symbolScore({_json.dumps(_book()['EGLDUSDT'])})")
    assert abs(sc['s7'] - 62.16) < 0.05 and abs(sc['s14'] - 24.49) < 0.05 and abs(sc['base'] - 43.33) < 0.05
    sc = _ts(f"m.symbolScore({_json.dumps(_book()['WLDUSDT'])})")
    assert abs(sc['base'] - 38.25) < 0.1


def test_few_profitable_presets_in_7_days_is_a_bad_sign():
    """User rule 2026-09-28: 2 of 81 profitable presets in 7 days is a bad sign."""
    wld = {**_book()['WLDUSDT'], 'c7': [2, 81]}
    sc = _ts(f"m.symbolScore({_json.dumps(wld)})")
    assert abs(sc['breadth'] - (2 / 81) / 0.10) < 0.01          # x0.25
    assert sc['score'] < sc['base'] * 0.31
    broad = _ts(f"m.symbolScore({_json.dumps({**_book()['WLDUSDT'], 'c7': [30, 81]})})")
    assert broad['breadth'] == 1


def test_recent_profit_above_half_of_the_fortnight_is_a_good_sign():
    """User rule: 7d Profit% > half of 14d Profit% is a good sign (x1.25)."""
    egld = _ts(f"m.symbolScore({_json.dumps({**_book()['EGLDUSDT'], 'c7': [30, 81]})})")   # 77.7 > 33.2/2
    assert egld['momentum'] is True and abs(egld['score'] - egld['base'] * 1.25) < 0.01
    wld = _ts(f"m.symbolScore({_json.dumps({**_book()['WLDUSDT'], 'c7': [30, 81]})})")     # 38.7 < 111.1/2
    assert wld['momentum'] is False and abs(wld['score'] - wld['base']) < 0.01


def test_weights_are_whole_numbers_adding_up_to_the_budget():
    a = _ts(f"m.scoreAllocation({_json.dumps(_book())}, {{disabled:new Set(), n:10, budget:32.5}})")
    vals = [v['value'] for v in a.values()]
    assert all(float(x).is_integer() for x in vals)
    assert sum(vals) == 33                                        # Math.round(32.5)


def test_promising_symbols_get_more_and_losers_get_zero():
    a = _ts(f"m.scoreAllocation({_json.dumps(_book())}, {{disabled:new Set(['APTUSDT']), n:10, budget:32.5}})")
    # EGLD and WLD both hit the 30 % cap in this small book; the order still holds
    assert a['EGLDUSDT']['value'] >= a['WLDUSDT']['value'] > a['ARBUSDT']['value'] > a['LTCUSDT']['value'] > 0
    assert a['EGLDUSDT']['score'] > a['WLDUSDT']['score']
    assert a['INJUSDT']['value'] == 0 and a['SOLUSDT']['value'] == 0         # losers
    assert a['APTUSDT']['value'] == 0 and a['APTUSDT']['rank'] is None     # disabled
    assert sum(v['value'] for v in a.values()) == 33                        # the whole budget, whole numbers
    assert max(v['value'] for v in a.values()) <= 0.30 * 32.5 + 1          # 30 % cap (+ rounding)


def test_only_the_top_n_symbols_take_part():
    a = _ts(f"m.scoreAllocation({_json.dumps(_book())}, {{disabled:new Set(), n:2, budget:30}})")
    funded = {s for s, v in a.items() if v['value'] > 0}
    assert funded == {'EGLDUSDT', 'WLDUSDT'}
    assert a['EGLDUSDT']['value'] + a['WLDUSDT']['value'] == 30            # cap relaxes to 1/N
    assert a['ARBUSDT']['inTopN'] is False and a['ARBUSDT']['rank'] == 3


def test_tilt_flags_turning_orders_on_and_off():
    book = _book()
    alloc = _ts(f"m.scoreAllocation({_json.dumps(book)}, {{disabled:new Set(), n:2, budget:30}})")
    inj = {'w': 9, 'lock': None, 'p7': book['INJUSDT']['p7'], 'p14': book['INJUSDT']['p14'],
           'policies': {'static': 9, 'tilt': 9, 'brake': 9}}
    s = _ts(f"m.panelSuggestions({_json.dumps(inj)}, {{disabled:false, alloc:{_json.dumps(alloc['INJUSDT'])}, n:2, eligible:5, budget:30}})")
    assert s['tilt']['value'] == 0 and any('turns REAL orders OFF' in f for f in s['tilt']['flags'])
    wld = {'w': 0, 'lock': None, 'p7': book['WLDUSDT']['p7'], 'p14': book['WLDUSDT']['p14'],
           'policies': {'static': 0, 'tilt': 0, 'brake': 0}}
    s = _ts(f"m.panelSuggestions({_json.dumps(wld)}, {{disabled:false, alloc:{_json.dumps(alloc['WLDUSDT'])}, n:2, eligible:5, budget:30}})")
    assert s['tilt']['value'] > 0 and any('turns REAL orders ON' in f for f in s['tilt']['flags'])
    assert 'score' in s['tilt']['note'] and 'rank 2' in s['tilt']['note']


def test_tilt_history_flag_uses_this_formulas_record():
    alloc = {'score': 40, 's7': 60, 's14': 20, 'rank': 1, 'inTopN': True, 'value': 9.75}
    row = {'w': 0, 'lock': None, 'p7': _top(70, 40), 'p14': _top(30, 50), 'policies': {'static': 0, 'tilt': 0, 'brake': 0}}
    hist = {'top': {'days': 105, 'mean': -0.6, 'vs_equal': -1.7, 'vs_current': -6, 'better_equal': 45}}
    s = _ts(f"m.panelSuggestions({_json.dumps(row)}, {{disabled:false, alloc:{_json.dumps(alloc)}, n:5, eligible:12, budget:32.5, scoreHist:{_json.dumps(hist)}}})")
    assert any('lost to simply equal-weighting' in f for f in s['tilt']['flags'])


def test_brake_still_does_not_compound():
    alloc = {'score': -10, 's7': 0, 's14': -20, 'rank': None, 'inTopN': False, 'value': 0}
    row = {'w': 1.5, 'lock': None, 'p7': _top(0.9, 32), 'p14': _top(-31.79, 45), 'policies': {'static': 1.5, 'tilt': 0, 'brake': 1.5}}
    anchor = {'base': 3, 'since': 1790520000000, 'policy': 'brake'}
    s = _ts(f"m.panelSuggestions({_json.dumps(row)}, {{disabled:false, alloc:{_json.dumps(alloc)}, n:5, eligible:12, budget:32.5, anchor:{_json.dumps(anchor)}}})")
    assert s['brake']['value'] == 1.5 and 'already braked' in s['brake']['note']


def test_backfill_uses_the_same_score_allocation():
    m = _bws()
    rows = {k: {'w': 0, **v} for k, v in _book().items()}
    a = m.score_allocation(rows, 10, 32.5, disabled={'APTUSDT'})
    ts = _ts(f"m.scoreAllocation({_json.dumps(_book())}, {{disabled:new Set(['APTUSDT']), n:10, budget:32.5}})")
    for sym in rows:
        assert a[sym] == ts[sym]['value'], sym                            # identical whole numbers


# ── Panel: N field, recalculate, lock toggle, preset counts ──────────────────

def test_panel_has_symbols_involved_field_and_recalculate():
    assert 'Symbols involved' in PANEL and 'risk-weight-suggestions:n:' in PANEL
    assert '↻ Recalculate' in PANEL and 'onClick={recalculate}' in PANEL
    route = (ROOT / 'dashboard/app/api/weight-suggestions/route.ts').read_text()
    assert "q.get('n')" in route and 'scoreAllocation(symbols, { disabled: disabledSet, n, budget })' in route


def test_lock_icon_locks_the_top_preset_or_unlocks_then_recalculates():
    assert "const target = r.lock ? null : (r.p14[2] ?? r.p7[2])" in PANEL
    assert "fetch('/api/risk/lock-preset'" in PANEL and 'body: JSON.stringify({ symbol: sym, preset: target, mode })' in PANEL
    assert 'onClick={() => toggleLock(sym, r)}' in PANEL
    lock_fn = PANEL[PANEL.index('async function toggleLock'):PANEL.index('if (!data) return null')]
    assert 'load(nApplied ?? nInput)' in lock_fn and 'window.confirm(' in lock_fn


def test_7d_and_14d_cells_show_profitable_preset_counts():
    assert 'presets are profitable' in PANEL
    assert "title={countsTip('7 days', r.c7, r.p7)}" in PANEL and "title={countsTip('14 days', r.c14, r.p14)}" in PANEL
    shadow = (ROOT / 'dashboard/app/api/trades/_weight-shadow.ts').read_text()
    assert "c7: counts(s.ranges['7d']), c14: counts(s.ranges['14d'])" in shadow


def test_full_tilt_allocation_can_be_applied_including_zeros():
    assert "const tiltAll = changed('tilt', false)" in PANEL          # zeros included
    assert "brakeAll = changed('brake', true)" in PANEL                 # brake: unflagged only
    assert 'Apply tilt allocation' in PANEL


def test_tilt_then_brake_does_not_halve_twice(tmp_path):
    """Verifier finding: tilt 2 -> 3, then brake 3 -> 1.5 within 14 days kept the TILT
    entry as the anchor, so the brake offered 0.75. The brake entry must anchor."""
    import time
    now = int(time.time() * 1000)
    data = tmp_path / 'data'; data.mkdir()
    (data / 'weight_suggestion_applies_test.jsonl').write_text(
        _json.dumps({'ts': now - 7200_000, 'symbol': 'EIGENUSDT', 'policy': 'tilt', 'old': 2, 'new': 3}) + '\n' +
        _json.dumps({'ts': now - 3600_000, 'symbol': 'EIGENUSDT', 'policy': 'brake', 'old': 3, 'new': 1.5}) + '\n')
    node = shutil.which('node')
    jiti = ROOT / 'dashboard/node_modules/jiti'
    if not node or not jiti.exists():
        import pytest; pytest.skip('node not available')
    (tmp_path / 'dashboard').mkdir()
    js = f"""
const jiti=require({_json.dumps(str(jiti))})({_json.dumps(str(ROOT/'dashboard'/'jiti-entry.js'))},{{alias:{{'@':{_json.dumps(str(ROOT/'dashboard'))}}}}})
const m=jiti({_json.dumps(str(ROOT/'dashboard/app/api/trades/_weight-shadow.ts'))})
const a=m.anchors('test', {{EIGENUSDT: 1.5}})
const row={{w:1.5,lock:null,p7:[0.9,32,'p'],p14:[-31.79,45,'p'],policies:{{static:1.5,tilt:0,brake:1.5}}}}
const alloc={{score:-10,s7:0,s14:-20,rank:null,inTopN:false,value:0}}
console.log(JSON.stringify({{a, s: m.panelSuggestions(row,{{disabled:false,alloc,n:5,eligible:12,budget:32.5,anchor:a.EIGENUSDT}})}}))"""
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, cwd=tmp_path / 'dashboard', timeout=60)
    assert out.returncode == 0, out.stderr
    r = _json.loads(out.stdout.strip().splitlines()[-1])
    assert r['a']['EIGENUSDT']['policy'] == 'brake' and r['a']['EIGENUSDT']['base'] == 3
    assert r['s']['brake']['value'] == 1.5 and 'already braked' in r['s']['brake']['note']


def test_budget_is_a_fixed_whole_number_not_the_current_weight_sum():
    # a decimal apply left the weights summing to 1.26 — that must not become the budget
    assert _ts("m.weightBudget({symbol_weights:{A:0.36,B:0.9}})") == 33
    assert _ts("m.weightBudget({weight_budget:40.4})") == 40
    assert _ts("m.weightBudget({weight_budget:0})") == 33
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("bws", pathlib.Path(__file__).parents[1] / "scripts/backfill_weight_shadow.py")
    bws = importlib.util.module_from_spec(spec); spec.loader.exec_module(bws)
    assert bws.weight_budget({}) == 33 and bws.weight_budget({"weight_budget": 40.4}) == 40
