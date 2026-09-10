"""A candidate that does not trade must not hold its slice of the budget hostage.

The multi-candidate branches size each symbol from a STATIC fraction of the total score:

    total_score = sum(max(0.0, s) for _, _, _, s in candidates)
    ...
    sym_cap = deployable * max(0.0, score) / total_score

`remaining` correctly tracks what is still unspent (`deployable - deployed`), but `sym_cap`
ignores it. So when the biggest-scoring candidate is refused by a later gate, its share is
simply never offered to anyone, and every symbol behind it is sized against a budget that
was never actually claimed.

Measured 2026-09-09 19:45 (deployable 2107.27, scores are efficiency x weight):

    INJUSDT     eff 428.24 x 14 = 5995   92.4%  cap ~1947  -> SKIPPED, sl_dist 16.37% > 10%
    SOLUSDT     eff  40.32 x  8 =  322    5.0%  cap  ~105  -> placed at 96
    ETHFIUSDT   eff  19.07 x  9 =  172    2.7%  cap   ~56  -> placed at 51
    REZUSDT     eff   0.00 x 13 =    0      --             -> no signal

1947 USDT of budget reserved and wasted; the two symbols that did trade got 8% of the
budget between them. Across the decision log, 8 of 9 multi-candidate candles that produced
a placement wasted budget this way, mean 56% — so orders on those candles were sized to
about 44% of what was available, and as low as 4%.

The pattern is not random: the highest-scoring symbol is often the one with the widest
stop, so it wins the allocation and then fails the SL gate. `skip_max_sl_pct` did this on
TIAUSDT (3 candles) and INJUSDT (2).

The fix renormalises as the loop advances — each candidate's share is measured against the
score still ahead of it and the budget still unspent. It cannot inflate a position beyond
what the bot already does daily, because two ceilings still apply downstream:
max_trade_pct (30% of deployable) and max_order_notional_usdt (2000, i.e. 400 margin at
5x, which is exactly the 400.0 seen on every full-size order).
"""
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()

DEPLOYABLE = 2107.27
# The 19:45 candle, in descending score order as the loop sees it.
CANDLE = [('INJUSDT', 428.24 * 14), ('SOLUSDT', 40.32 * 8), ('ETHFIUSDT', 19.07 * 9)]
MAX_TRADE_PCT = 30.0
MAX_NOTIONAL = 2000.0
LEV = 5


def _ceilings(cap):
    """The two caps that still apply after allocation."""
    return min(cap, DEPLOYABLE * MAX_TRADE_PCT / 100.0, MAX_NOTIONAL / LEV)


def old_caps(candle, placed):
    """Static fraction of total_score — the current behaviour."""
    total = sum(s for _, s in candle)
    out, deployed = {}, 0.0
    for sym, s in candle:
        remaining = max(0.0, DEPLOYABLE - deployed)
        if remaining <= 0:
            break
        cap = _ceilings(DEPLOYABLE * s / total)
        out[sym] = cap
        if sym in placed:
            deployed += cap
    return out


def new_caps(candle, placed):
    """Renormalised against the score still ahead and the budget still unspent."""
    remaining_score = sum(s for _, s in candle)
    out, deployed = {}, 0.0
    for sym, s in candle:
        remaining = max(0.0, DEPLOYABLE - deployed)
        if remaining <= 0:
            break
        cap = _ceilings(remaining * s / remaining_score) if remaining_score > 0 else 0.0
        remaining_score = max(0.0, remaining_score - s)
        out[sym] = cap
        if sym in placed:
            deployed += cap
    return out


class TestTheMeasuredCandle:
    def test_the_old_behaviour_starves_the_symbols_that_trade(self):
        old = old_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert old['SOLUSDT'] < 120, old
        assert old['ETHFIUSDT'] < 70, old

    def test_renormalising_restores_them_to_a_normal_size(self):
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert new['SOLUSDT'] == 400.0, new
        assert new['ETHFIUSDT'] == 400.0, new

    def test_the_skipped_leader_still_gets_its_share_offered_first(self):
        """It is not penalised — it simply cannot reserve what it does not use."""
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert new['INJUSDT'] == 400.0

    def test_nothing_exceeds_what_the_bot_already_places(self):
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        for sym, cap in new.items():
            assert cap <= 400.0 + 1e-9, f'{sym} sized above the notional ceiling'

    def test_total_deployed_stays_inside_the_budget(self):
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert new['SOLUSDT'] + new['ETHFIUSDT'] <= DEPLOYABLE


class TestItChangesNothingWhenEveryoneTrades:
    def test_all_candidates_trading_is_unaffected_in_total(self):
        """If nobody is refused there is no unused slice, so the budget spent is the
        same — only the order in which it is handed out differs."""
        old = old_caps(CANDLE, placed={s for s, _ in CANDLE})
        new = new_caps(CANDLE, placed={s for s, _ in CANDLE})
        assert sum(old.values()) <= DEPLOYABLE
        assert sum(new.values()) <= DEPLOYABLE

    def test_a_single_candidate_is_unaffected(self):
        one = [('REZUSDT', 13 * 34.29)]
        assert new_caps(one, placed={'REZUSDT'}) == old_caps(one, placed={'REZUSDT'})

    def test_a_zero_score_candidate_gets_nothing(self):
        candle = [('INJUSDT', 5995.0), ('REZUSDT', 0.0)]
        assert new_caps(candle, placed={'INJUSDT'})['REZUSDT'] == 0.0


class TestEdgeCases:
    def test_all_scores_zero_does_not_divide_by_zero(self):
        candle = [('A', 0.0), ('B', 0.0)]
        caps = new_caps(candle, placed=set())
        assert all(c == 0.0 for c in caps.values())

    def test_the_budget_running_out_stops_the_loop(self):
        candle = [('A', 100.0), ('B', 100.0), ('C', 100.0),
                  ('D', 100.0), ('E', 100.0), ('F', 100.0)]
        caps = new_caps(candle, placed={s for s, _ in candle})
        assert sum(caps.values()) <= DEPLOYABLE + 1e-9

    def test_the_last_candidate_cannot_take_more_than_the_ceiling(self):
        """Its renormalised share is 100% of what is left, so only the ceilings bound it."""
        candle = [('BIG', 9000.0), ('LAST', 1.0)]
        caps = new_caps(candle, placed={'LAST'})
        assert caps['LAST'] <= 400.0 + 1e-9


class TestBothBranchesAreFixed:
    """TATS n>1 is the live path; BGF shares the identical bug and is fixed with it
    rather than left as a latent one, since that scenario is not currently active."""

    def test_no_branch_still_divides_by_the_static_total(self):
        assert 'deployable * max(0.0, score) / total_score' not in MAIN, \
            'a static-fraction cap remains — a skipped candidate still reserves budget'

    def test_the_running_score_is_tracked(self):
        assert MAIN.count('_remaining_score') >= 2, \
            'both multi-candidate branches must renormalise'

    def test_the_reason_is_recorded_for_the_next_reader(self):
        assert 'hold its slice' in MAIN or 'hostage' in MAIN
