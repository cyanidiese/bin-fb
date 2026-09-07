"""The preset chosen for real orders must still record a data point when blocked.

The preset selected for real trading — rank 1, or a manually locked one — was excluded
from the virtual pool ("idx 0 = best, used for real orders"). So when its real order was
blocked by any filter, nothing was recorded anywhere. TIAUSDT: 12 real orders placed
against 115 blocked signals, none of which produced a record.

That made the ranking key compare a *filtered* rank-1 sample against the *unfiltered*
population of ranks 2-87, and froze a preset's evidence the moment it became rank 1.

Rank 1 now has its own pool that opens only when no real order was placed. Its orders are
recorded but EXCLUDED from preset ranking in this phase, so there is a baseline before it
influences where money goes. See docs/specs/2026-09-07-rank1-statistics-gap.md.
"""
import inspect
from pathlib import Path

from bot.virtual_order_simulator import VirtualOrderSimulator

ROOT = Path(__file__).resolve().parents[1]
SIM = (ROOT / 'bot/virtual_order_simulator.py').read_text()
MAIN = (ROOT / 'main.py').read_text()


class TestPool:
    def test_rank_1_has_a_pool(self):
        assert 'range(1, self._rank_max + 1)' in SIM, \
            'rank 1 needs its own open/fake/balance pool'

    def test_rank_1_has_its_own_balance(self):
        """Sharing the real balance would let a virtual fill move real allocation."""
        i = SIM.index('def sync_real_balance_on_start')
        assert 'range(1,' in SIM[i:i + 900], 'rank 1 balance is never seeded'


class TestOpening:
    def test_on_candle_close_takes_real_order_placed(self):
        sig = inspect.signature(VirtualOrderSimulator.on_candle_close)
        assert 'real_order_placed' in sig.parameters
        assert sig.parameters['real_order_placed'].default is False, \
            'defaulting to True would silently disable the pool'

    def test_rank_1_is_skipped_when_a_real_order_was_placed(self):
        i = SIM.index('for rank in range(1,')
        body = SIM[i:i + 1600]
        assert 'real_order_placed' in body, 'rank 1 opens regardless of the real order'

    def test_a_real_order_evicts_an_open_rank_1_position(self):
        """Otherwise the same signal is counted twice — once virtual, once real."""
        assert 'real_order_took_over' in SIM

    def test_virtual_only_symbols_keep_rank_1_empty(self):
        """A disabled symbol already puts index 0 at rank 2; opening rank 1 too would
        double-count it."""
        i = SIM.index('for rank in range(1,')
        assert 'virtual_only' in SIM[i:i + 1600] or 'is_locked' in SIM[i:i + 1600]


class TestRankingExclusion:
    """Phase one: recorded, not scored."""

    def test_main_skips_rank_1_when_updating_efficiency(self):
        i = MAIN.index('virtual_tracker.record_closed_trade(symbol, vc[')
        near = MAIN[max(0, i - 500):i]
        assert "vc.get('rank')" in near or "vc['rank']" in near, \
            'rank 1 must not feed preset_efficiency yet'

    def test_the_exclusion_is_documented_where_it_happens(self):
        i = MAIN.index('virtual_tracker.record_closed_trade(symbol, vc[')
        assert 'rank 1' in MAIN[max(0, i - 700):i].lower(), \
            'a future reader must see why rank 1 is skipped'


def test_ranks_2_and_up_are_untouched():
    """163k historical orders are keyed by rank; changing what rank N means would
    invalidate every one of them."""
    assert 'rank_idx = rank - (2 if is_locked else 1)' in SIM, \
        'the existing rank-to-preset mapping must not change'


class TestRank1Closes:
    """The opening side alone is worse than nothing: a rank-1 position that never
    closes produces no statistics while looking like the feature works. Three loops
    still iterated from rank 2 after the first implementation pass — check_prices,
    close_all_open and _save_all_rank_balances."""

    def test_every_rank_loop_includes_rank_1(self):
        assert 'range(2, self._rank_max + 1)' not in SIM, \
            'a loop still skips rank 1 — its positions would never close'

    def test_check_prices_covers_rank_1(self):
        i = SIM.index('async def check_prices')
        body = SIM[i:i + 900]
        assert 'range(1,' in body, 'rank 1 never checked for TP/SL/trail'

    def test_close_all_open_covers_rank_1(self):
        i = SIM.index('async def close_all_open')
        assert 'range(1,' in SIM[i:i + 900], 'rank 1 left open on shutdown'

    def test_balances_are_saved_for_rank_1(self):
        i = SIM.index('def _save_all_rank_balances')
        assert 'range(1,' in SIM[i:i + 500], 'rank 1 balance never persisted'
