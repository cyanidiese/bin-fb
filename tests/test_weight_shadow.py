"""Weight shadow calculator — records proposed weights, never applies them.
Spec: docs/specs/2026-09-28-weight-shadow-calculator.md"""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'dashboard/app/api/trades/_weight-shadow.ts').read_text()


def test_policies_are_bounded_and_evidence_gated():
    assert 'Math.min(1.3, Math.max(0.7,' in SRC          # tilt at most +-30 %
    assert "n < 20) return w" in SRC                       # tilt needs >= 20 trades
    assert 'n >= 30 && pct <= -30 ? round(w * 0.5) : w' in SRC   # brake: sustained loss only


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


def test_panel_shows_the_reconstructed_history():
    route = (ROOT / 'dashboard/app/api/weight-suggestions/route.ts').read_text()
    assert "evaluateShadow(mode, 7, 'history')" in route
    assert 'History (reconstructed)' in PANEL


# ── Every symbol gets a tilt/brake value; doubts are flagged, columns sortable ──

import json as _json
import shutil
import subprocess


def _panel_suggestions(row: dict, disabled=False, base=3, history=None, anchor=None):
    node = shutil.which('node')
    jiti = ROOT / 'dashboard/node_modules/jiti'
    if not node or not jiti.exists():
        import pytest
        pytest.skip('node / dashboard node_modules not available')
    js = f"""
const jiti=require({_json.dumps(str(jiti))})({_json.dumps(str(ROOT/'dashboard'/'jiti-entry.js'))},{{alias:{{'@':{_json.dumps(str(ROOT/'dashboard'))}}}}})
const m=jiti({_json.dumps(str(ROOT/'dashboard/app/api/trades/_weight-shadow.ts'))})
console.log(JSON.stringify(m.panelSuggestions({_json.dumps(row)},{{disabled:{str(disabled).lower()},baseWeight:{base},history:{_json.dumps(history)},anchor:{_json.dumps(anchor)}}})))"""
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, cwd=ROOT / 'dashboard', timeout=60)
    assert out.returncode == 0, out.stderr
    return _json.loads(out.stdout.strip().splitlines()[-1])


def _row(w, pct, n):
    return {'w': w, 'lock': None, 'p7': [pct, n, 'p'], 'p14': [pct, n, 'p'],
            'policies': {'static': w, 'tilt': w, 'brake': w}}


def test_weight_zero_symbol_gets_a_funding_tilt_with_reasons():
    s = _panel_suggestions(_row(0, 50.0, 40))
    assert s['tilt']['value'] > 3                          # from tats_min_weight, tilted up
    assert any('turns REAL orders ON' in f for f in s['tilt']['flags'])
    assert s['brake']['value'] == 0 and s['brake']['flags'] == [] and 'already 0' in s['brake']['note']


def test_thin_evidence_is_flagged_not_hidden():
    s = _panel_suggestions(_row(5, 40.0, 4))
    assert s['tilt']['value'] != 5
    assert any('only 4 trade(s)' in f for f in s['tilt']['flags'])
    b = _panel_suggestions(_row(5, -60.0, 10))
    assert b['brake']['value'] == 2.5 and any('needs ≥ 30' in f for f in b['brake']['flags'])


def test_losing_unfunded_symbol_is_not_suggested_for_funding():
    s = _panel_suggestions(_row(0, -20.0, 50))
    assert s['tilt']['value'] == 0 and any('not positive' in f for f in s['tilt']['flags'])


def test_history_without_edge_is_a_doubt():
    h = {'snapshots': 112, 'evaluated': 105, 'days': 7,
         'scores': {'static': {'mean': 0, 'vsStatic': 0, 'betterDays': 0},
                    'tilt': {'mean': 0, 'vsStatic': 0.08, 'betterDays': 49},
                    'brake': {'mean': 0, 'vsStatic': 0.0, 'betterDays': 8}}}
    s = _panel_suggestions(_row(5, 40.0, 40), history=h)
    assert any('no real edge' in f for f in s['tilt']['flags'])


def test_panel_sorts_every_column_and_remembers_it():
    assert "'risk-weight-suggestions:sort'" in PANEL
    for col in ("'symbol'", "'weight'", "'p7'", "'p14'", "'tilt'", "'brake'"):
        assert f'[{col},' in PANEL, col
    assert 'toggleSort(col)' in PANEL


def test_bulk_apply_takes_only_unflagged_values():
    assert 'sug(s, pol).flags.length === 0' in PANEL


# ── Anchoring: applying a suggestion twice must not compound ─────────────────

def test_brake_does_not_halve_again_after_it_was_applied():
    """EIGENUSDT 2026-09-28: 3 -> 1.5 by brake at 14:52; the panel then offered 0.75."""
    anchor = {'base': 3, 'since': 1790520000000, 'policy': 'brake'}
    s = _panel_suggestions(_row(1.5, -31.79, 45), anchor=anchor)
    assert s['brake']['value'] == 1.5                        # = current: no further cut
    assert 'already braked' in s['brake']['note']


def test_tilt_is_computed_from_the_pre_suggestion_weight():
    anchor = {'base': 10, 'since': 1790520000000, 'policy': 'tilt'}
    s = _panel_suggestions(_row(13, 200.0, 40), anchor=anchor)
    assert s['tilt']['value'] <= 13                          # 10 x at most 1.3, not 13 x 1.3
    assert 'does not compound' in s['tilt']['note']


def test_apply_logs_the_policy_that_set_each_weight():
    assert 'logApplied(body.mode' in APPLY
    assert "apply({ [sym]: v }, pol)" in PANEL
    shadow = (ROOT / 'dashboard/app/api/trades/_weight-shadow.ts').read_text()
    assert "if (e.policy === 'custom') anchor = null" in shadow          # typed value = new base
    assert 'Math.abs((currentWeights[sym] ?? 0) - last.new) < 1e-9' in shadow  # changed elsewhere = new base
