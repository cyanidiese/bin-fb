"""A funded symbol must never be sized to zero.

TATS decides candidacy from risk_config.symbol_weights, but the single-candidate sizing
branch computed its fraction from symbol_registry's own, unrelated weights. The two
disagree, and on 2026-09-09 the disagreement refused real orders:

    REZUSDT   risk_config weight 13 (2nd largest allocation), registry weight 0
              -> _sym_frac = 0  ->  sym_cap = 0.00
              -> 'balance=0.00 < margin=1.00'  ->  skip_balance, 42 times

    ETHFIUSDT risk_config weight 9, registry weight 0  -> primed to do the same

Together that is ~40% of allocated capital unable to trade whenever it was the sole
candidate. REZUSDT still placed 4 orders in the same window, because the MULTI-candidate
branch passes `remaining` rather than a weight fraction — which is why the bug hid.

Deliberately NOT changed: the decision to take the fraction path still reads the registry.
Reading risk_config there would put every weight above tats_min_weight (3) and hand each
sole candidate the whole deployable budget — measured, ~421 -> ~2107 margin, about 5x the
observed position size. That is a risk change, not a bug fix. Logged in TODO.md.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
LINES = MAIN.splitlines()


def _branch() -> str:
    """The single-candidate TATS sizing branch."""
    i = next(i for i, l in enumerate(LINES) if 'tats_min_weight: low-weight symbols' in l)
    j = next(j for j in range(i, len(LINES)) if 'bypass_pct_cap=True)' in LINES[j])
    return '\n'.join(LINES[i:j + 1])


BRANCH = _branch()


class TestTheFractionUsesTheFundingWeights:
    def test_the_fraction_comes_from_risk_config(self):
        assert '_cfg_ws' in BRANCH
        assert '_sym_w = float(_cfg_ws.get(sym' in BRANCH

    def test_the_total_comes_from_the_same_source(self):
        """Mixing sources would give a fraction that does not sum to 1 across symbols."""
        assert '_total_w = sum(float(_cfg_ws.get(s' in BRANCH

    def test_the_registry_no_longer_supplies_the_fraction(self):
        """symbol_registry.get_weight() returns 0 for symbols funded in risk_config."""
        i = BRANCH.index('_sym_frac')
        before = BRANCH[:i]
        assert 'sum(symbol_registry.get_weight' not in before

    def test_a_zero_total_does_not_divide_by_zero(self):
        assert 'if _total_w > 0 else 1.0' in BRANCH


class TestTheRiskPostureIsUnchanged:
    def test_the_path_decision_still_reads_the_registry(self):
        """Switching this too would 5x position sizes — a separate decision."""
        assert 'symbol_registry.get_weight(sym) < _tats_min_w' in BRANCH

    def test_the_reason_is_recorded_for_the_next_reader(self):
        assert 'risk change, not a bug fix' in BRANCH


class TestTheArithmetic:
    """Executed, not asserted on source: the fraction must be proportional and non-zero
    for every funded symbol."""

    LIVE = {'INJUSDT': 14, 'REZUSDT': 13, 'ETHFIUSDT': 9,
            'SOLUSDT': 8, 'EIGENUSDT': 6, 'TIAUSDT': 4}

    @staticmethod
    def _frac(sym, weights):
        total = sum(float(w) for w in weights.values())
        return (float(weights.get(sym, 0.0)) / total) if total > 0 else 1.0

    def test_no_funded_symbol_gets_a_zero_fraction(self):
        for sym in self.LIVE:
            assert self._frac(sym, self.LIVE) > 0, f'{sym} sized to zero'

    def test_the_fractions_sum_to_one(self):
        assert abs(sum(self._frac(s, self.LIVE) for s in self.LIVE) - 1.0) < 1e-9

    def test_a_bigger_weight_gets_a_bigger_share(self):
        """The old flat 1/5 ignored the configured weight entirely."""
        assert self._frac('INJUSDT', self.LIVE) > self._frac('TIAUSDT', self.LIVE)

    def test_the_caps_stay_in_the_observed_range(self):
        """Real orders have used 340-400 margin. The new caps must not be wildly larger,
        or this stops being a bug fix."""
        deployable = 2107.27
        caps = [deployable * self._frac(s, self.LIVE) for s in self.LIVE]
        assert max(caps) < 700, f'largest cap {max(caps):.0f} is far above what was observed'
        assert min(caps) > 50, f'smallest cap {min(caps):.0f} cannot meet min notional'

    def test_an_unfunded_symbol_still_gets_nothing(self):
        """Weight 0 means "do not trade this" and must stay that way."""
        w = dict(self.LIVE, DOGEUSDT=0)
        assert self._frac('DOGEUSDT', w) == 0.0
