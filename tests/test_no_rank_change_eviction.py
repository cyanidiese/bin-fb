"""A practice position must not be killed because the rank table reshuffled.

Each rank is a slot holding whichever preset is currently Nth-best, and the rankings
shuffle constantly — so 60,266 of 163,668 closed virtual orders (36.8%) were closed at
whatever price happened to be current, not because anything happened in the market.

Real exits average -3.89 (stop), +4.63 (trail), +11.10 (target). Reshuffle exits average
+0.31. Since the ranking key is sum(recent_trades[-10:]), those near-zero rows dilute:
a preset with genuinely large wins and losses reads flatter than it is.

Rank 1 is the exception and keeps evicting — it stands in for the real-order slot and has
to be free the moment a real order is placed.

Spec: docs/specs/2026-09-07-stop-evicting-on-rank-change.md
"""
from pathlib import Path

SIM = (Path(__file__).resolve().parents[1] / 'bot/virtual_order_simulator.py').read_text()
MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()


class TestNoEvictionAtRanks2Plus:
    def test_a_rank_change_leaves_the_position_running(self):
        """The whole point: the slot is skipped, the trade continues."""
        i = SIM.index("preset_name, overrides = sorted_presets[rank_idx]")
        body = SIM[i:i + 1200]
        assert "'rank_change'" not in body, \
            'ranks >= 2 still evict on reshuffle'

    def test_the_reason_is_documented_where_the_check_was(self):
        i = SIM.index("preset_name, overrides = sorted_presets[rank_idx]")
        body = SIM[i:i + 1200].lower()
        assert 'reshuffl' in body or 'rank change' in body, \
            'a future reader must see why the slot is skipped rather than evicted'


class TestOnePositionPerPreset:
    """Eviction used to guarantee this for free; without it a preset could run twice."""

    def test_try_open_refuses_a_preset_already_open(self):
        i = SIM.index('async def _try_open')
        body = SIM[i:i + 2500]
        assert '_preset_is_open' in body or 'already open' in body.lower(), \
            'a preset could hold two concurrent positions and double-count its own PnL'

    def test_the_helper_checks_every_rank(self):
        assert '_preset_is_open' in SIM
        i = SIM.index('def _preset_is_open')
        # window past the docstring, which explains why the invariant is now needed
        body = SIM[i:i + 1200]
        assert 'self._rank_open' in body and 'for' in body, \
            'the check must scan all ranks, not just one'


class TestRank1StillEvicts:
    def test_a_real_order_still_takes_over(self):
        assert 'real_order_took_over' in SIM

    def test_rank_1_still_evicts_on_a_top_preset_change(self):
        i = SIM.index('if rank == 1:')
        body = SIM[i:i + 1800]
        assert "'rank_change'" in body, \
            'rank 1 must represent whatever would trade NOW'


class TestPromotionFreesThePreset:
    def test_promotion_evicts_the_stale_position(self):
        """Otherwise the duplicate guard leaves the about-to-trade preset stuck holding
        a practice position in another slot."""
        assert 'promoted_to_real' in SIM


class TestMaxAge:
    def test_there_is_a_maximum_position_age(self):
        assert 'max_age' in SIM
        from config.risk_config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG.get('virtual_max_age_candles', 0) > 0

    def test_the_default_is_a_day_or_less(self):
        """The longest practice position ran 11 days; a stuck trade must not block a
        slot indefinitely."""
        from config.risk_config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG['virtual_max_age_candles'] <= 96


class TestBookkeepingExitsAreNotScored:
    def test_main_excludes_the_bookkeeping_results(self):
        i = MAIN.index('virtual_tracker.record_closed_trade(symbol, vc[')
        near = MAIN[max(0, i - 900):i]
        for r in ('promoted_to_real', 'max_age'):
            assert r in near, f'{r} must not feed preset_efficiency'
