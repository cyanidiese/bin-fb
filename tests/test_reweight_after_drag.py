"""Dragging a symbol must never activate one that is switched off.

`onDragEnd` assigned `newWeights[sym] = n - i` to every row, rewriting the whole table by
position. With 17 symbols the bottom row got weight 1, so a single drag activated all ten
zero-weight symbols for real orders and discarded the hand-picked weights above it.

Weight 0 is a deliberate off switch, so drag is asymmetric now: it can deactivate a
symbol (drop it among the zeros) but never activate one.

There is no JS test runner in this project, so the pure function is transpiled with the
project's own tsc and executed under node. That gives real behavioural coverage rather
than asserting on source strings -- this logic decides which symbols spend real money.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'dashboard/components/risk/reweight.ts'

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
    """Compile reweight.ts once, return a callable that invokes it under node."""
    if not shutil.which('node'):
        pytest.skip('node not available')
    tmp = Path(tempfile.mkdtemp())
    r = subprocess.run(
        ['npx', 'tsc', str(SRC), '--outDir', str(tmp), '--module', 'commonjs',
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
