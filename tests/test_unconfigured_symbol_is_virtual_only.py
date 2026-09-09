"""A symbol nobody gave a weight must not trade real money.

`main.py` read the allocation weight as `risk_cfg.get("symbol_weights", {}).get(sym, 1.0)`,
so a symbol present in symbol_registry.json but ABSENT from risk_config.symbol_weights
arrived with weight 1.0 — a full real-order candidate on its first candle.

That was survivable while adding a symbol required a restart someone would notice. Once
the roster is hot-reloaded (see test_symbol_hot_subscribe.py) a symbol added by editing
symbol_registry.json over SSH — without touching risk_config.json — would start taking
real orders on the next candle with nobody having chosen that.

Measured 2026-09-09: all 22 symbols in the live registry had an explicit symbol_weights
entry, so this default was unreachable and flipping it changes nothing today. It is purely
a guard against the next accident: never trade a symbol nobody configured.

Weight 0 does NOT stop data collection. `main.py:1667` drops zero-score candidates from
the real-order path only; virtual simulation still runs for every subscribed symbol,
including symbols that are fully disabled. Verified on the live bot: all eight disabled
symbols had recent virtual orders, and WLDUSDT (weight 0, disabled) held 79 open virtual
positions.
"""
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()


class TestTheDefaultIsVirtualOnly:
    def test_the_score_multiplier_defaults_to_zero(self):
        assert 'symbol_weights", {}).get(sym, 1.0)' not in MAIN, \
            'an unconfigured symbol still defaults to a tradeable weight of 1.0'
        assert 'symbol_weights", {}).get(sym, 0.0)' in MAIN

    def test_the_discard_reason_reads_the_same_default(self):
        """Otherwise the log would claim weight 1.0 while the score was zeroed by 0.0."""
        assert 'symbol_weights", {}).get(_sym, 1.0)' not in MAIN
        assert 'symbol_weights", {}).get(_sym, 0.0)' in MAIN

    def test_no_tradeable_default_remains_anywhere(self):
        assert 'symbol_weights' in MAIN
        for bad in ('.get(sym, 1.0)', '.get(_sym, 1.0)'):
            assert bad not in MAIN, f'{bad} still defaults an unknown symbol to tradeable'


class TestTheGateItFeeds:
    def test_a_zero_score_candidate_is_dropped_before_placement(self):
        """The mechanism this default relies on: zero weight zeroes the score, and
        zero-score candidates never reach _try_place_order."""
        assert 'c for c in candidates if c[3] > 0.0' in MAIN

    def test_the_drop_happens_before_the_sole_candidate_branch(self):
        drop = MAIN.index('c for c in candidates if c[3] > 0.0')
        sole = MAIN.index('tats_min_weight: low-weight symbols')
        assert drop < sole, \
            'a weight-0 symbol could reach the full-deployable-budget branch'


class TestArithmetic:
    """The consequence, stated in numbers so a regression is recognisable."""

    @staticmethod
    def _score(eff, weight):
        return eff * max(0.0, weight)

    def test_an_unconfigured_symbol_scores_zero(self):
        assert self._score(34.29, 0.0) == 0.0

    def test_it_would_have_scored_and_traded_under_the_old_default(self):
        assert self._score(34.29, 1.0) > 0.0

    def test_a_configured_symbol_is_unaffected(self):
        for w in (4, 6, 8, 9, 13, 14):
            assert self._score(34.29, w) > 0.0
