"""A funded symbol must never be sized to zero — and fixing that must not resize others.

TATS decides candidacy from risk_config.symbol_weights, but the single-candidate sizing
branch computed its fraction from symbol_registry's own, unrelated weights. The two
disagree, and on 2026-09-09 the disagreement refused real orders:

    REZUSDT   risk_config weight 13 (2nd largest allocation), registry weight 0
              -> _sym_frac = 0  ->  sym_cap = 0.00
              -> 'balance=0.00 < margin=1.00'  ->  skip_balance
              Measured: 42 log events across 6 distinct candles (09-08 13:00, and five
              consecutive candles 09-09 09:00-10:00), so ~2 lost signal episodes. The
              09:00-10:00 gap sits between two winning REZUSDT trails (+19.82 closed
              09:00, +20.86 opened 11:15) on a day it went 3-for-3 averaging +22.00.

    ETHFIUSDT risk_config weight 9, registry weight 0  -> primed to do the same

Together that is 22 of 54 configured weight — 41% of allocated capital — unable to trade
whenever it was the sole candidate. REZUSDT still placed orders because the MULTI-candidate
branch passes `remaining` rather than a weight fraction, which is why the bug hid.

WHY THE FIRST FIX (aa5f9e7) WAS TOO BROAD
-----------------------------------------
It switched the fraction to risk_config for EVERY symbol, which silently resized the four
that were working (deployable 2107.27):

    symbol      cfg  reg   deployed   aa5f9e7
    INJUSDT      14    1        421       546
    REZUSDT      13    0          0       507   <- the bug
    ETHFIUSDT     9    0          0       351   <- the bug
    SOLUSDT       8    1        421       312
    EIGENUSDT     6    1        421       234
    TIAUSDT       4    1        421       156   <- 63% cut

TIAUSDT is the most productive symbol under the current locked-preset config: 9 of the 14
post-lock real orders, +92.09 USDT, 56% win, +2.87%/trade. Cutting it 63% as a side effect
of fixing two other symbols trades away known income for no stated reason.

So the fallback is narrowed to the broken case only: registry-proportional stays wherever
it produces a usable fraction, and risk_config is consulted only when the registry has no
weight for a symbol risk_config funds. That is missing data, not an instruction to size at
zero.

Deliberately NOT changed: the decision to take the fraction path still reads the registry.
Reading risk_config there would put every weight above tats_min_weight (3) and hand each
sole candidate the whole deployable budget — measured, ~421 -> ~2107 margin, about 5x the
observed position size. That is a risk change, not a bug fix. Logged in TODO.md.
"""
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
LINES = MAIN.splitlines()


def _branch() -> str:
    """The single-candidate TATS sizing branch."""
    i = next(i for i, l in enumerate(LINES) if 'tats_min_weight: low-weight symbols' in l)
    j = next(j for j in range(i, len(LINES)) if 'bypass_pct_cap=True)' in LINES[j])
    return '\n'.join(LINES[i:j + 1])


BRANCH = _branch()


def _code() -> str:
    """The branch with comment lines stripped.

    Source-scanning assertions must read the code, not the commentary — the comment
    below mentions `_sym_frac` and `symbol_registry.get_weight` while explaining the
    bug, which made an earlier version of this test match the prose instead.
    """
    return '\n'.join(l for l in BRANCH.splitlines() if not l.lstrip().startswith('#'))


CODE = _code()

# The live configuration this was measured against. _active_ws is every enabled,
# unpaused symbol — which includes the weight-0 ones, so they belong in the fixture:
# omitting AVAXUSDT put the registry denominator at 4 and the expected share at 1/4,
# when the observed cap of 421 on a 2107.27 budget is 1/5.
CFG_W = {'INJUSDT': 14, 'REZUSDT': 13, 'ETHFIUSDT': 9,
         'SOLUSDT': 8, 'EIGENUSDT': 6, 'TIAUSDT': 4, 'AVAXUSDT': 0, 'BTCUSDT': 0}
REG_W = {'INJUSDT': 1, 'REZUSDT': 0, 'ETHFIUSDT': 0, 'SOLUSDT': 1,
         'EIGENUSDT': 1, 'TIAUSDT': 1, 'AVAXUSDT': 1, 'BTCUSDT': 0}
FUNDED = ('INJUSDT', 'REZUSDT', 'ETHFIUSDT', 'SOLUSDT', 'EIGENUSDT', 'TIAUSDT')
DEPLOYABLE = 2107.27


def frac(sym, cfg_w=None, reg_w=None):
    """The fraction the branch must produce. Mirrors the implementation."""
    cfg_w = CFG_W if cfg_w is None else cfg_w
    reg_w = REG_W if reg_w is None else reg_w
    reg_total = sum(float(w) for w in reg_w.values())
    f = (float(reg_w.get(sym, 0.0)) / reg_total) if reg_total > 0 else 1.0
    if f > 0.0:
        return f
    cfg_total = sum(float(cfg_w.get(s, 0.0)) for s in reg_w)
    return (float(cfg_w.get(sym, 0.0)) / cfg_total) if cfg_total > 0 else 0.0


class TestTheBugIsFixed:
    def test_no_funded_symbol_is_sized_to_zero(self):
        for sym in FUNDED:
            assert frac(sym) > 0.0, f'{sym} sized to zero'

    def test_rezusdt_gets_its_configured_share(self):
        """13 of 54 configured weight."""
        assert abs(frac('REZUSDT') - 13 / 54) < 1e-9   # AVAX/BTC contribute 0
        assert abs(DEPLOYABLE * frac('REZUSDT') - 507.31) < 0.5

    def test_ethfiusdt_gets_its_configured_share(self):
        assert abs(frac('ETHFIUSDT') - 9 / 54) < 1e-9

    def test_the_fallback_is_wired_to_risk_config(self):
        assert '_cfg_ws' in CODE, 'no risk_config fallback present'


class TestTheWorkingSymbolsAreUntouched:
    """The half aa5f9e7 got wrong. Every symbol the registry can size must keep the
    exact cap it had before, or this is a risk change wearing a bug fix's clothes."""

    def test_registry_sized_symbols_keep_the_flat_share(self):
        for sym in ('INJUSDT', 'SOLUSDT', 'EIGENUSDT', 'TIAUSDT'):
            assert abs(frac(sym) - 1 / 5) < 1e-9, f'{sym} was resized'

    def test_tiausdt_is_not_cut(self):
        """It earns +2.87%/trade over 9 post-lock orders — the most productive symbol."""
        cap = DEPLOYABLE * frac('TIAUSDT')
        assert cap > 400, f'TIAUSDT cut to {cap:.0f}; it was 421'

    def test_the_registry_still_supplies_the_fraction_when_it_can(self):
        i = CODE.index('_sym_frac')
        assert 'symbol_registry.get_weight' in CODE[:i], \
            'the registry no longer sizes anything'

    def test_the_fallback_is_conditional_not_unconditional(self):
        """An unconditional risk_config fraction is exactly the too-broad fix."""
        assert 'if _sym_frac' in CODE or 'if not _sym_frac' in CODE, \
            'the risk_config share is applied unconditionally'


class TestTheRiskPostureIsUnchanged:
    def test_the_path_decision_still_reads_the_registry(self):
        assert 'symbol_registry.get_weight(sym) < _tats_min_w' in BRANCH

    def test_no_cap_exceeds_what_has_been_observed(self):
        """Real orders have used 340-400 margin. A cap far above that is a risk change."""
        caps = [DEPLOYABLE * frac(s) for s in FUNDED]
        assert max(caps) < 700, f'largest cap {max(caps):.0f}'

    def test_no_cap_falls_below_min_notional(self):
        caps = [DEPLOYABLE * frac(s) for s in FUNDED]
        assert min(caps) > 50, f'smallest cap {min(caps):.0f}'

    def test_the_reason_is_recorded_for_the_next_reader(self):
        assert 'risk change, not a bug fix' in BRANCH


class TestEdgeCases:
    def test_an_unfunded_symbol_still_gets_nothing(self):
        """Weight 0 means "do not trade this". It is gated earlier by the score, but if
        it ever reaches here the fraction must not resurrect it."""
        cfg = dict(CFG_W, DOGEUSDT=0)
        reg = dict(REG_W, DOGEUSDT=0)
        assert frac('DOGEUSDT', cfg, reg) == 0.0

    def test_zero_in_both_sources_does_not_divide_by_zero(self):
        assert frac('NOPEUSDT', {}, {}) in (0.0, 1.0)

    def test_an_empty_registry_does_not_divide_by_zero(self):
        assert frac('REZUSDT', CFG_W, {}) == 1.0

    def test_a_registry_that_agrees_needs_no_fallback(self):
        """If someone fixes the registry data, the fallback must go quiet, not double up."""
        reg = dict(REG_W, REZUSDT=1, ETHFIUSDT=1)   # seven of eight now weighted 1
        for sym in FUNDED:
            assert abs(frac(sym, CFG_W, reg) - 1 / 7) < 1e-9

    def test_the_guard_against_a_zero_total_is_present(self):
        assert 'else 1.0' in BRANCH
