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


def test_apply_never_turns_a_symbol_on_or_off():
    """0 is the real-order on/off switch; suggestions only rescale existing weights."""
    assert 'w <= 0' in APPLY
    assert "has no weight entry in this mode" in APPLY


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
