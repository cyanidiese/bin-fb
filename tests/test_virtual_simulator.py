"""VirtualOrderSimulator: rank pools, pricing, rank-1 stand-in, persistence and parity.

Sections (one per former file):
- TestRankSimulator                              (test_virtual_order_simulator.py)
- TestTheChargeIsApplied .. TestPerSymbolEstimates (test_virtual_pnl_charges_slippage.py)
- TestPool .. TestRank1Closes                    (test_rank1_virtual_pool.py)
- TestTheGateItself / TestBehaviour              (test_rank1_not_open_while_real_position_open.py)
- TestNoEvictionAtRanks2Plus .. TestBookkeeping… (test_no_rank_change_eviction.py)
- TestVirtualPersistence                         (test_virtual_persistence.py)
- TestVirtualRealParityPart2                     (test_parity_part2.py)
"""
import inspect
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.fake_order import FakeOrder
from bot.order_executor import OpenOrder
from bot.order_sizing import real_quantity
from bot.rate_limit_guard import guard as rl_guard
from bot.virtual_order_simulator import VirtualOrderSimulator
from tests.factories import (
    make_analyzer, make_executor, make_preset_settings, make_rec, make_simulator,
    make_vt_with_scores, src,
)

SIM = src('bot/virtual_order_simulator.py')
MAIN = src('main.py')
SYM = 'BTCUSDT'


def _now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ══════════════════════ VirtualOrderSimulator rank-based tests ══════════════════════
# (was test_virtual_order_simulator.py; its VirtualTracker seeding tests moved to
# test_virtual_tracker.py)


class TestRankSimulator:
    # ── balance initialisation ─────────────────────────────────────────────────

    def test_rank_balances_initialised(self, tmp_path):
        """Rank 1 gained a pool on 2026-09-07.

        It is the real-order slot, and it now holds a virtual position whenever the real
        order did NOT happen, so a blocked signal still records what the preset would have
        done. It needs its own balance like every other rank — sharing the real one would
        let a virtual fill move real allocation.
        See docs/specs/2026-09-07-rank1-statistics-gap.md.
        """
        sim = make_simulator(tmp_path, initial_balance=500.0, rank_max=3)
        balances = sim.get_rank_balances()
        assert set(balances.keys()) == {1, 2, 3}
        assert all(v == 500.0 for v in balances.values())

    def test_sync_real_balance_writes_every_rank_file(self, tmp_path):
        sim = make_simulator(tmp_path, initial_balance=0.0, rank_max=3)
        sim.sync_real_balance_on_start(777.0)
        for rank in (2, 3):
            path = tmp_path / 'data' / f'virtual_balance_rank{rank}_test.json'
            assert path.exists()
            assert json.loads(path.read_text())['balance'] == 777.0

    def test_sync_real_balance_overwrites_existing_pool(self, tmp_path):
        """Deliberate behaviour change: the old apply_real_balance_if_fresh() seeded a
        rank only when its file was absent, so pools drifted from the real account
        across restarts. sync_real_balance_on_start() re-baselines every pool on every
        start, so virtual and real begin each session from the same number."""
        sim = make_simulator(tmp_path, initial_balance=100.0, rank_max=3)
        (tmp_path / 'data').mkdir(parents=True, exist_ok=True)
        (tmp_path / 'data' / 'virtual_balance_rank2_test.json').write_text('{"balance": 999.0}')
        sim2 = make_simulator(tmp_path, initial_balance=100.0, rank_max=3)
        sim2.sync_real_balance_on_start(500.0)
        assert sim2._rank_balance[2] == 500.0

    def test_sync_real_balance_ignores_non_positive(self, tmp_path):
        """A failed balance read returns 0.0 — it must not zero out the virtual pools."""
        sim = make_simulator(tmp_path, initial_balance=250.0, rank_max=3)
        sim.sync_real_balance_on_start(0.0)
        assert sim._rank_balance[2] == 250.0

    def test_rank_balance_persists_to_disk(self, tmp_path):
        sim = make_simulator(tmp_path, initial_balance=200.0, rank_max=3)
        sim._rank_balance[2] = 250.0
        sim._save_rank_balance(2)
        path = tmp_path / 'data' / 'virtual_balance_rank2_test.json'
        data = json.loads(path.read_text())
        assert data['balance'] == 250.0

    def test_rank_balance_loads_from_disk(self, tmp_path):
        # First instance persists rank-2 balance
        sim1 = make_simulator(tmp_path, initial_balance=200.0, rank_max=3)
        sim1._rank_balance[2] = 350.0
        sim1._save_rank_balance(2)
        # Second instance should load that value
        sim2 = make_simulator(tmp_path, initial_balance=999.0, rank_max=3)
        assert sim2._rank_balance[2] == 350.0

    # ── candle close — opening positions ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_on_candle_close_opens_rank2_and_rank3(self, tmp_path):
        """Rank-2 slot gets preset_b, rank-3 gets preset_c (preset_a is best/real)."""
        sim = make_simulator(tmp_path, rank_max=4)
        rec = make_rec()

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()

            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        assert 'BTCUSDT' in sim._rank_open[2]
        assert sim._rank_open[2]['BTCUSDT']['preset_name'] == 'preset_b'
        assert 'BTCUSDT' in sim._rank_open[3]
        assert sim._rank_open[3]['BTCUSDT']['preset_name'] == 'preset_c'

    @pytest.mark.asyncio
    async def test_on_candle_close_does_not_double_open(self, tmp_path):
        """Calling on_candle_close twice does not open a second position at the same rank."""
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec()

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()

            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())
            after_first = dict(sim._rank_open[2])

            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())
            after_second = dict(sim._rank_open[2])

        assert after_first.keys() == after_second.keys()

    @pytest.mark.asyncio
    async def test_no_signal_leaves_slot_empty(self, tmp_path):
        """If RecommendationEngine returns None, the rank slot stays empty."""
        sim = make_simulator(tmp_path, rank_max=3)

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = None
            mock_dc.replace.return_value = make_preset_settings()

            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        assert 'BTCUSDT' not in sim._rank_open[2]

    # ── price checks — TP / SL ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_check_prices_closes_on_tp(self, tmp_path):
        """Rank-2 position closes as 'win' when price crosses TP."""
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        closed = await sim.check_prices('BTCUSDT', 55001.0)
        results = [c['result'] for c in closed]
        assert any(r in ('win', 'trail', 'partial') for r in results)
        assert 'BTCUSDT' not in sim._rank_open[2]

    @pytest.mark.asyncio
    async def test_check_prices_closes_on_sl(self, tmp_path):
        """Rank-2 position closes as 'loss' when price crosses SL."""
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        closed = await sim.check_prices('BTCUSDT', 47999.0)
        assert any(c['result'] == 'loss' for c in closed)
        assert 'BTCUSDT' not in sim._rank_open[2]

    @pytest.mark.asyncio
    async def test_check_prices_updates_rank_balance(self, tmp_path):
        """Winning trade increases the rank-2 pool balance."""
        sim = make_simulator(tmp_path, initial_balance=1000.0, rank_max=3)
        rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        balance_before = sim._rank_balance[2]
        await sim.check_prices('BTCUSDT', 55001.0)  # TP hit → win
        assert sim._rank_balance[2] > balance_before

    @pytest.mark.asyncio
    async def test_check_prices_returns_rank_in_closed_dict(self, tmp_path):
        """check_prices return dicts include a 'rank' key."""
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        closed = await sim.check_prices('BTCUSDT', 55001.0)
        for c in closed:
            assert 'rank' in c
            # rank 1 is valid since 2026-09-07: it is the real-order slot's virtual
            # stand-in, opened when no real order was placed. main.py filters it out of the
            # ranking feed on this same key, so the key must be present for every rank.
            assert c['rank'] in (1, 2, 3, 4, 5, 6)

    # ── rank eviction ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rank_change_leaves_the_open_position_running(self, tmp_path):
        """A reshuffle must NOT close an open practice position (changed 2026-09-07).

        Ranks are slots holding whichever preset is currently Nth-best, and the rankings
        move constantly — closing on that produced 60,266 of 163,668 records at whatever
        price happened to be current, averaging +0.31 against real exits of
        -3.89/+4.63/+11.10, diluting every preset's score toward zero.

        The slot is skipped instead, and picks up the then-correct preset once the trade
        reaches its own target or stop. Rank 1 is the exception and still evicts.
        See docs/specs/2026-09-07-stop-evicting-on-rank-change.md.
        """
        # Start: preset_a best, preset_b rank-2
        scores = {'preset_a': 3.0, 'preset_b': 2.0, 'preset_c': 1.0}
        vt = make_vt_with_scores(scores)
        sim = VirtualOrderSimulator(
            mode='test',
            all_presets={'preset_a': {}, 'preset_b': {}, 'preset_c': {}},
            project_root=tmp_path,
            get_leverage=lambda sym: 1,
            initial_balance=1000.0,
            virtual_tracker=vt,
            min_notionals={'BTCUSDT': 5.0},
            rank_max=3,
        )

        rec = make_rec()
        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        assert sim._rank_open[2]['BTCUSDT']['preset_name'] == 'preset_b'

        # Rankings shift: preset_c now rank-2
        vt.get_preset_efficiency.side_effect = lambda s, n: {'preset_a': 3.0, 'preset_b': 1.0, 'preset_c': 2.0}.get(n, 0.0)
        vt.get_preset_rank_key.side_effect = lambda s, n: (1, {'preset_a': 3.0, 'preset_b': 1.0, 'preset_c': 2.0}.get(n, 0.0))

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(price=51000.0), 'preset_a', MagicMock())

        # preset_b's trade is still running; the slot was skipped, not reassigned
        assert sim._rank_open[2]['BTCUSDT']['preset_name'] == 'preset_b', \
            'the reshuffle killed a live position'

        # and nothing was recorded as a rank_change exit at this rank
        rank_file = tmp_path / 'data' / 'virtual_orders_rank2_BTCUSDT_test.json'
        if rank_file.exists():
            recs = json.loads(rank_file.read_text())
            assert not [r for r in recs if r.get('result') == 'rank_change'], \
                'a bookkeeping exit was still written'

    @pytest.mark.asyncio
    async def test_no_rank_change_record_is_written_at_rank_2(self, tmp_path):
        """The counterpart of the above: no rank_change record appears at ranks >= 2.

        Positions are persisted when they close for a real reason (target, stop, trail) or
        for a labelled bookkeeping reason at rank 1; a reshuffle is no longer one of them.
        """
        scores = {'preset_a': 3.0, 'preset_b': 2.0, 'preset_c': 1.0}
        vt = make_vt_with_scores(scores)
        sim = VirtualOrderSimulator(
            mode='test',
            all_presets={'preset_a': {}, 'preset_b': {}, 'preset_c': {}},
            project_root=tmp_path,
            get_leverage=lambda sym: 1,
            initial_balance=1000.0,
            virtual_tracker=vt,
            min_notionals={'BTCUSDT': 5.0},
            rank_max=3,
        )

        rec = make_rec()
        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        # Shift rankings to trigger eviction
        vt.get_preset_efficiency.side_effect = lambda s, n: {'preset_a': 3.0, 'preset_b': 1.0, 'preset_c': 2.0}.get(n, 0.0)
        vt.get_preset_rank_key.side_effect = lambda s, n: (1, {'preset_a': 3.0, 'preset_b': 1.0, 'preset_c': 2.0}.get(n, 0.0))
        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(price=51000.0), 'preset_a', MagicMock())

        rank_file = tmp_path / 'data' / 'virtual_orders_rank2_BTCUSDT_test.json'
        records = json.loads(rank_file.read_text()) if rank_file.exists() else []
        assert not [r for r in records if r.get('result') == 'rank_change'], \
            'a reshuffle still produced a bookkeeping record at rank 2'
        # the position is still open, which is the point
        assert sim._rank_open[2]['BTCUSDT']['preset_name'] == 'preset_b'

    # ── close_all_open ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_close_all_open_clears_all_ranks(self, tmp_path):
        """close_all_open removes positions from all rank slots for all symbols."""
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec()

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        feed = MagicMock()
        feed.client.futures_symbol_ticker = MagicMock(return_value={'price': '51000.0'})

        await sim.close_all_open(['BTCUSDT'], feed)

        for rank in (2, 3):
            assert 'BTCUSDT' not in sim._rank_open[rank]

    @pytest.mark.asyncio
    async def test_close_all_open_writes_closed_early_to_file(self, tmp_path):
        """close_all_open records closed_early result in the rank order file."""
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec()

        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            MockEng.return_value.generate.return_value = rec
            mock_dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())

        feed = MagicMock()
        feed.client.futures_symbol_ticker = MagicMock(return_value={'price': '51000.0'})
        await sim.close_all_open(['BTCUSDT'], feed)

        rank_file = tmp_path / 'data' / 'virtual_orders_rank2_BTCUSDT_test.json'
        assert rank_file.exists()
        records = json.loads(rank_file.read_text())
        assert any(r.get('result') == 'closed_early' for r in records)

    # ── per-candle outcome summary (diagnostics, 2026-09-27) ───────────────────

    @pytest.mark.asyncio
    async def test_candle_summary_reports_why_ranks_did_not_open(self, tmp_path):
        """A symbol with signals but no practice orders was only diagnosable by replaying the
        simulator offline. Each candle now records how every rank ended."""
        sim = make_simulator(tmp_path, rank_max=4)
        with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
             patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = make_preset_settings()
            MockEng.return_value.generate.return_value = None
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())
            first = dict(sim.last_candle_summary['BTCUSDT'])
            MockEng.return_value.generate.return_value = make_rec()
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())
            second = dict(sim.last_candle_summary['BTCUSDT'])
            await sim.on_candle_close('BTCUSDT', make_analyzer(), 'preset_a', MagicMock())
            third = dict(sim.last_candle_summary['BTCUSDT'])
        assert first.get('no_signal', 0) >= 2 and 'opened' not in first
        assert second.get('opened', 0) >= 2
        assert third.get('slot_held', 0) >= 2 and 'opened' not in third


# ══════════════════════ Virtual PnL is charged slippage ══════════════════════
# (was test_virtual_pnl_charges_slippage.py)
# Virtual PnL must be charged the same execution cost real orders pay.
#
# The simulator used to compute PnL straight off the signalled entry:
#
#     raw  = (close_price - entry) * qty        # BUY
#     fees = (entry + close_price) * qty * 0.0004
#
# Real orders are MARKET orders. Across 137 real fills (2026-09-13, 60 days) the entry came
# in worse than signalled by +0.098% on average and *never once better*. At 5x that is about
# 0.49% of margin per trade, against a measured virtual edge of -0.052%/trade — so the
# uncharged simulation overstates results by roughly ten times the edge it is meant to
# measure, and symbol-selection decisions taken off it are unsafe.
#
# The charge goes on the MONEY ONLY. The FakeOrder that decided the outcome keeps the
# signalled entry and its TP/SL fire at the signalled levels, mirroring the real path where
# the reconciled fill "deliberately does NOT feed the FakeOrder or the SL/TP geometry"
# (bot/order_executor.py:301) and reaches PnL solely through _effective_entry(). Moving the
# virtual entry instead would shift it relative to fixed TP/SL and flip which orders win —
# real slippage does not do that.
#
# See docs/specs/2026-09-13-slippage-modelling.md.


FEE = 0.0004


def _unslipped(entry, close, qty, side):
    """What the simulator computed before this change."""
    raw = (close - entry) * qty if side == 'BUY' else (entry - close) * qty
    return raw - (entry + close) * qty * FEE


@pytest.fixture
def sim(tmp_path, monkeypatch):
    s = make_simulator(tmp_path)
    monkeypatch.setattr(
        'bot.virtual_order_simulator.load_risk_config',
        lambda: {'slippage_model_enabled': True, 'slippage_default_pct': 0.10,
                 'slippage_min_samples': 5, 'slippage_per_symbol': {}},
    )
    return s


def _rec(side='BUY', entry=100.0, qty=10.0):
    return {'entry_price': entry, 'quantity': qty, 'side': side}


class TestTheChargeIsApplied:
    def test_a_buy_earns_less_than_the_unslipped_figure(self, sim):
        got = sim._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT')
        assert got < _unslipped(100.0, 105.0, 10.0, 'BUY')

    def test_a_sell_earns_less_too(self, sim):
        got = sim._calc_pnl(_rec('SELL'), 95.0, 'BTCUSDT')
        assert got < _unslipped(100.0, 95.0, 10.0, 'SELL')

    def test_a_loss_is_made_worse_not_better(self, sim):
        """One-sided: slippage can never rescue a losing trade."""
        got = sim._calc_pnl(_rec('BUY'), 95.0, 'BTCUSDT')
        assert got < _unslipped(100.0, 95.0, 10.0, 'BUY')

    def test_the_size_matches_the_configured_default(self, sim):
        """0.10% of a 1000 notional is ~1.0 USDT, plus a hair of extra fee."""
        got = sim._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT')
        assert _unslipped(100.0, 105.0, 10.0, 'BUY') - got == pytest.approx(1.0, abs=0.01)


class TestItCanBeSwitchedOff:
    def test_disabled_reproduces_the_old_number_exactly(self, tmp_path, monkeypatch):
        s = make_simulator(tmp_path)
        monkeypatch.setattr(
            'bot.virtual_order_simulator.load_risk_config',
            lambda: {'slippage_model_enabled': False},
        )
        assert s._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT') == pytest.approx(
            _unslipped(100.0, 105.0, 10.0, 'BUY'))

    def test_no_symbol_means_no_charge(self, sim):
        """Callers that cannot name the symbol must not be charged a guess."""
        assert sim._calc_pnl(_rec('BUY'), 105.0, '') == pytest.approx(
            _unslipped(100.0, 105.0, 10.0, 'BUY'))


class TestGeometryIsUntouched:
    def test_the_record_is_not_mutated(self, sim):
        """TP/SL and the entry that drove them must survive the PnL call."""
        r = _rec('BUY')
        before = dict(r)
        sim._calc_pnl(r, 105.0, 'BTCUSDT')
        assert r == before, 'PnL calculation rewrote the position record'


class TestPerSymbolEstimates:
    def test_an_override_is_used(self, tmp_path, monkeypatch):
        s = make_simulator(tmp_path)
        monkeypatch.setattr(
            'bot.virtual_order_simulator.load_risk_config',
            lambda: {'slippage_model_enabled': True, 'slippage_default_pct': 0.10,
                     'slippage_per_symbol': {'BTCUSDT': 1.0}},
        )
        # 1.0% of a 1000 notional is ~10 USDT
        assert _unslipped(100.0, 105.0, 10.0, 'BUY') - s._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT') \
            == pytest.approx(10.0, abs=0.05)

    def test_the_estimate_is_cached_not_reread_per_tick(self, sim, monkeypatch):
        """check_prices() runs per tick across every rank — an uncached read would hit
        the filesystem thousands of times a candle."""
        calls = []
        monkeypatch.setattr(
            'bot.virtual_order_simulator.load_risk_config',
            lambda: (calls.append(1) or {'slippage_model_enabled': True,
                                         'slippage_default_pct': 0.10}),
        )
        for _ in range(50):
            sim._calc_pnl(_rec('BUY'), 105.0, 'BTCUSDT')
        assert len(calls) == 1, f'config re-read {len(calls)} times instead of once'


# ══════════════════════ Rank 1 has its own virtual pool ══════════════════════
# (was test_rank1_virtual_pool.py)
# The preset chosen for real orders must still record a data point when blocked.
#
# The preset selected for real trading — rank 1, or a manually locked one — was excluded
# from the virtual pool ("idx 0 = best, used for real orders"). So when its real order was
# blocked by any filter, nothing was recorded anywhere. TIAUSDT: 12 real orders placed
# against 115 blocked signals, none of which produced a record.
#
# That made the ranking key compare a *filtered* rank-1 sample against the *unfiltered*
# population of ranks 2-87, and froze a preset's evidence the moment it became rank 1.
#
# Rank 1 now has its own pool that opens only when no real order was placed. Its orders are
# recorded but EXCLUDED from preset ranking in this phase, so there is a baseline before it
# influences where money goes. See docs/specs/2026-09-07-rank1-statistics-gap.md.


class TestPool:
    def test_rank_1_has_a_pool(self):
        assert 'range(1, self._rank_max + 1)' in SIM, \
            'rank 1 needs its own open/fake/balance pool'

    def test_rank_1_has_its_own_balance(self):
        """Sharing the real balance would let a virtual fill move real allocation."""
        i = SIM.index('def sync_real_balance_on_start')
        assert 'range(1,' in SIM[i:i + 900], 'rank 1 balance is never seeded'


class TestOpening:
    def test_on_candle_close_takes_real_slot_busy(self):
        sig = inspect.signature(VirtualOrderSimulator.on_candle_close)
        assert 'real_slot_busy' in sig.parameters
        assert sig.parameters['real_slot_busy'].default is False, \
            'defaulting to True would silently disable the pool'

    def test_rank_1_is_skipped_when_the_real_slot_is_busy(self):
        i = SIM.index('for rank in range(1,')
        body = SIM[i:i + 2400]
        assert 'real_slot_busy' in body, 'rank 1 opens regardless of the real slot'

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
        near = MAIN[max(0, i - 1400):i]
        assert "vc.get('rank')" in near or "vc['rank']" in near, \
            'rank 1 must not feed preset_efficiency yet'

    def test_the_exclusion_is_documented_where_it_happens(self):
        i = MAIN.index('virtual_tracker.record_closed_trade(symbol, vc[')
        assert 'rank 1' in MAIN[max(0, i - 1600):i].lower(), \
            'a future reader must see why rank 1 is skipped'


class TestRankMapping:
    def test_ranks_2_and_up_are_untouched(self):
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


# ══════════════════════ Rank 1 stays empty while the real slot is busy ══════════════════════
# (was test_rank1_not_open_while_real_position_open.py)
# The rank-1 stand-in must stay empty while the real slot is occupied.
#
# The gate was candle-scoped -- `_placed_this_candle.get(symbol) == candle_ts` -- so it only
# suppressed rank 1 on the candle the order was placed. From the next candle onwards the
# stand-in opened alongside a live real trade on the same preset. Observed on the server:
#
#     SOLUSDT  l2_trend_buy  REAL     entry 102.97  (08:45 UTC, still open)
#     SOLUSDT  l2_trend_buy  Rank #1  entry 103.63  (10:15 UTC, also open)
#
# Two correlated samples of one move, feeding one preset's statistics -- and on a trade we
# could never have taken, because only one position per symbol is possible. The same
# `get_state(sym) != IDLE` condition already excludes the symbol from real-order candidates.
#
# Renamed to `real_slot_busy`: the old name described the narrow condition and is what made
# the wider one easy to miss.


class TestTheGateItself:
    def test_the_parameter_is_named_for_the_slot_not_the_placement(self):
        sig = inspect.signature(VirtualOrderSimulator.on_candle_close)
        assert 'real_slot_busy' in sig.parameters
        assert 'real_order_placed' not in sig.parameters

    def test_main_considers_an_open_position_not_just_this_candle(self):
        i = MAIN.index('_real_slot_busy = (')
        expr = MAIN[i:i + 400]
        assert '_placed_this_candle' in expr, 'lost the same-candle half'
        assert 'OrderState.IDLE' in expr, 'an open position no longer blocks rank 1'
        assert ' or ' in expr, 'the two conditions must be OR-ed'

    def test_it_is_passed_through(self):
        assert 'real_slot_busy=_real_slot_busy' in MAIN


async def _r1_candle(sim, symbol='BTCUSDT', **kw):
    """One candle close, driven the way the existing simulator tests do it.

    Awaited rather than asyncio.run(): run() closes the loop and leaves no current one,
    which made test_telegram_menu fail later in the same process.
    """
    rec = make_rec()
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
         patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
        MockEng.return_value.generate.return_value = rec
        mock_dc.replace.return_value = make_preset_settings()
        await sim.on_candle_close(symbol, make_analyzer(), 'preset_a', MagicMock(), **kw)


@pytest.mark.asyncio
class TestBehaviour:
    async def test_rank_1_opens_when_the_slot_is_free(self, tmp_path):
        """Baseline — without this the suppression tests below prove nothing."""
        sim = make_simulator(tmp_path)
        await _r1_candle(sim, real_slot_busy=False)
        assert 'BTCUSDT' in sim._rank_open[1], 'rank 1 never opens; the rest is vacuous'

    async def test_rank_1_does_not_open_while_the_slot_is_busy(self, tmp_path):
        sim = make_simulator(tmp_path)
        await _r1_candle(sim, real_slot_busy=True)
        assert 'BTCUSDT' not in sim._rank_open[1]

    async def test_an_open_rank_1_position_is_evicted_when_real_trades_its_preset(self, tmp_path):
        """The observed bug: the stand-in ran beside a real trade on the SAME preset."""
        sim = make_simulator(tmp_path)
        await _r1_candle(sim, real_slot_busy=False)
        assert sim._rank_open[1]['BTCUSDT']['preset_name'] == 'preset_a'
        await _r1_candle(sim, real_slot_busy=True, real_preset='preset_a')
        assert 'BTCUSDT' not in sim._rank_open[1], 'stand-in ran alongside its own real trade'

    async def test_a_different_preset_at_rank_1_runs_to_its_own_exit(self, tmp_path):
        """2026-09-29 (parity V5): cutting it as 'real_order_took_over' recorded an exit
        at a random price. A different preset is not a duplicate sample; it keeps running
        and nothing new opens at rank 1 while real is busy."""
        sim = make_simulator(tmp_path)
        await _r1_candle(sim, real_slot_busy=False)
        await _r1_candle(sim, real_slot_busy=True, real_preset='preset_c')
        assert sim._rank_open[1]['BTCUSDT']['preset_name'] == 'preset_a'

    async def test_the_real_orders_preset_is_not_opened_at_any_rank(self, tmp_path):
        """The rule is symbol+preset. preset_b sits at rank 2 in this harness."""
        sim = make_simulator(tmp_path, rank_max=4)
        await _r1_candle(sim, real_slot_busy=True, real_preset='preset_b')
        held = {r: sim._rank_open[r].get('BTCUSDT', {}).get('preset_name')
                for r in range(1, 5)}
        assert 'preset_b' not in held.values(), \
            f'preset_b opened beside its own real order: {held}'

    async def test_other_presets_keep_collecting(self, tmp_path):
        """Scoped to the preset, not the symbol -- otherwise an actively-trading symbol
        stops producing any comparison data at all."""
        sim = make_simulator(tmp_path, rank_max=4)
        await _r1_candle(sim, real_slot_busy=True, real_preset='preset_b')
        held = {sim._rank_open[r].get('BTCUSDT', {}).get('preset_name')
                for r in range(1, 5)}
        assert 'preset_c' in held, f'other presets were blocked too: {held}'

    async def test_an_open_position_on_that_preset_is_released(self, tmp_path):
        """Blocking new opens is not enough -- one already running must be evicted."""
        sim = make_simulator(tmp_path, rank_max=4)
        await _r1_candle(sim, real_slot_busy=False)
        opened = {r: sim._rank_open[r].get('BTCUSDT', {}).get('preset_name')
                  for r in range(1, 5)}
        assert 'preset_b' in opened.values(), \
            f'preset_b never opened; the eviction assertion would be vacuous: {opened}'
        await _r1_candle(sim, real_slot_busy=True, real_preset='preset_b')
        held = {sim._rank_open[r].get('BTCUSDT', {}).get('preset_name')
                for r in range(1, 5)}
        assert 'preset_b' not in held, 'preset_b survived beside its own real order'

    async def test_other_symbols_are_untouched(self, tmp_path):
        """The rule is per symbol -- a real order on one must not clear another's pools."""
        sim = make_simulator(tmp_path, rank_max=4)
        sim._min_notionals['ETHUSDT'] = 5.0
        await _r1_candle(sim, symbol='ETHUSDT', real_slot_busy=False)
        assert any('ETHUSDT' in sim._rank_open[r] for r in range(1, 5))
        await _r1_candle(sim, symbol='BTCUSDT', real_slot_busy=True, real_preset='preset_b')
        assert any('ETHUSDT' in sim._rank_open[r] for r in range(1, 5)), \
            "another symbol's virtual positions were cleared"

    async def test_a_disabled_symbol_still_leaves_rank_1_empty(self, tmp_path):
        """virtual_only symbols put index 0 at rank 2; rank 1 must stay unused."""
        sim = make_simulator(tmp_path)
        await _r1_candle(sim, virtual_only=True)
        assert 'BTCUSDT' not in sim._rank_open[1]


# ══════════════════════ No eviction on a rank reshuffle ══════════════════════
# (was test_no_rank_change_eviction.py)
# A practice position must not be killed because the rank table reshuffled.
#
# Each rank is a slot holding whichever preset is currently Nth-best, and the rankings
# shuffle constantly — so 60,266 of 163,668 closed virtual orders (36.8%) were closed at
# whatever price happened to be current, not because anything happened in the market.
#
# Real exits average -3.89 (stop), +4.63 (trail), +11.10 (target). Reshuffle exits average
# +0.31. Since the ranking key is sum(recent_trades[-10:]), those near-zero rows dilute:
# a preset with genuinely large wins and losses reads flatter than it is.
#
# Rank 1 is the exception and keeps evicting — it stands in for the real-order slot and has
# to be free the moment a real order is placed.
#
# Spec: docs/specs/2026-09-07-stop-evicting-on-rank-change.md


def _rank1_branch() -> str:
    """The rank-1 branch itself, not a fixed number of characters after it.

    This was `SIM[i:i + 1800]`, which broke the moment a comment was added inside the
    branch -- the code was correct and the test failed anyway. Slicing to where the
    ranks>=2 handling starts keeps it honest as the branch grows.
    """
    start = SIM.index('if rank == 1:')
    end = SIM.index('existing = self._rank_open[rank].get(symbol)', start)
    return SIM[start:end]


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


class TestRank1:
    def test_a_real_order_still_takes_over(self):
        assert 'real_order_took_over' in SIM

    def test_rank_1_no_longer_evicts_on_a_top_preset_change(self):
        """2026-09-29: the eviction closed the trade at a random price (~0 %) and hid its
        real outcome. The position now runs to its own exit; the slot is reported held."""
        body = _rank1_branch()
        assert "'rank_change'" not in body
        assert "r1:slot_held_by_other_preset" in body


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


# ══════════════════════ Virtual positions survive restarts ══════════════════════
# (was test_virtual_persistence.py)

M15 = 15 * 60 * 1000


def _persist_open(sim, rank=2, opened=None, side='BUY', entry=100.0, tp=110.0, sl=95.0):
    opened = opened or datetime.now(timezone.utc) - timedelta(hours=3)
    sim._rank_open[rank][SYM] = {
        'preset_name': 'preset_b', 'rank': rank, 'side': side, 'entry_price': entry,
        'tp': tp, 'sl': sl, 'quantity': 1.0, 'leverage': 1, 'open_time': opened.isoformat(),
        'status': 'open', 'close_price': None, 'close_time': None, 'pnl_usdt': None, 'result': None,
    }
    sim._rank_fake[rank][SYM] = FakeOrder(side=side, entry_price=entry, tp=tp, sl=sl,
                                          level=2, signal_type='x', candle_index=0)


def _kline(close_ms, o, h, l, c):
    return [close_ms - M15 + 1, str(o), str(h), str(l), str(c), '0', close_ms]


class TestVirtualPersistence:
    """Virtual/real parity (spec docs/specs/2026-09-29-virtual-real-parity.md):
    virtual positions survive restarts instead of closing as 'closed_early', rank 1 no
    longer evicts on a best-preset change, and real positions get an age limit knob."""

    @pytest.mark.asyncio
    async def test_positions_round_trip_and_stay_open(self, tmp_path):
        a = make_simulator(tmp_path, rank_max=3)
        _persist_open(a)
        path = tmp_path / 'state.json'
        assert a.save_open_state(path) == 1

        b = make_simulator(tmp_path, rank_max=3)
        still_open, closed = await b.restore_open_state(path, lambda s: [], [SYM])
        assert (still_open, closed) == (1, [])
        assert b._rank_open[2][SYM]['preset_name'] == 'preset_b'
        assert b._rank_fake[2][SYM].sl == 95.0

    @pytest.mark.asyncio
    async def test_an_sl_hit_during_downtime_closes_at_that_candle(self, tmp_path):
        a = make_simulator(tmp_path, rank_max=3)
        _persist_open(a)
        path = tmp_path / 'state.json'
        a.save_open_state(path)
        saved = json.loads(path.read_text())['saved_at_ms']

        hit_close = saved + M15            # first candle after the save: low goes through SL
        klines = [_kline(saved - M15, 100, 101, 90, 100),   # before the save — ignored
                  _kline(hit_close, 100, 101, 94, 96),
                  _kline(hit_close + M15, 96, 120, 96, 118)]  # would be a TP later — never reached
        b = make_simulator(tmp_path, rank_max=3)
        with patch('bot.virtual_order_simulator.datetime', wraps=datetime) as dt:
            dt.now.return_value = datetime.fromtimestamp((hit_close + 2 * M15) / 1000, tz=timezone.utc)
            dt.fromtimestamp = datetime.fromtimestamp
            dt.fromisoformat = datetime.fromisoformat
            still_open, closed = await b.restore_open_state(path, lambda s: klines, [SYM])

        assert still_open == 0 and len(closed) == 1
        sym, info = closed[0]
        assert sym == SYM and info['result'] == 'loss' and info['close_price'] == 95.0
        assert SYM not in b._rank_open[2]
        rec = json.loads((tmp_path / 'data' / f'virtual_orders_rank2_{SYM}_test.json').read_text())[-1]
        assert rec['result'] == 'loss'
        assert rec['close_time'] == datetime.fromtimestamp(hit_close / 1000, tz=timezone.utc).isoformat(), \
            'the close must land on the candle it happened, not the restart'

    @pytest.mark.asyncio
    async def test_candles_before_the_save_or_the_open_are_not_replayed(self, tmp_path):
        a = make_simulator(tmp_path, rank_max=3)
        _persist_open(a)
        path = tmp_path / 'state.json'
        a.save_open_state(path)
        saved = json.loads(path.read_text())['saved_at_ms']
        b = make_simulator(tmp_path, rank_max=3)
        klines = [_kline(saved - 5 * M15, 100, 101, 80, 100), _kline(saved, 100, 101, 80, 100)]
        still_open, closed = await b.restore_open_state(path, lambda s: klines, [SYM])
        assert (still_open, closed) == (1, [])

    @pytest.mark.asyncio
    async def test_the_forming_candle_is_left_to_ticks(self, tmp_path):
        a = make_simulator(tmp_path, rank_max=3)
        _persist_open(a)
        path = tmp_path / 'state.json'
        a.save_open_state(path)
        b = make_simulator(tmp_path, rank_max=3)
        future = _now_ms() + 10 * M15
        still_open, closed = await b.restore_open_state(
            path, lambda s: [_kline(future, 100, 101, 80, 90)], [SYM])
        assert (still_open, closed) == (1, [])

    @pytest.mark.asyncio
    async def test_positions_of_unsubscribed_symbols_are_dropped(self, tmp_path):
        a = make_simulator(tmp_path, rank_max=3)
        _persist_open(a)
        path = tmp_path / 'state.json'
        a.save_open_state(path)
        b = make_simulator(tmp_path, rank_max=3)
        still_open, closed = await b.restore_open_state(path, lambda s: [], ['ETHUSDT'])
        assert (still_open, closed) == (0, [])
        assert SYM not in b._rank_open[2]

    @pytest.mark.asyncio
    async def test_missing_or_corrupt_state_is_harmless(self, tmp_path):
        b = make_simulator(tmp_path, rank_max=3)
        assert await b.restore_open_state(tmp_path / 'nope.json', lambda s: [], [SYM]) == (0, [])
        bad = tmp_path / 'bad.json'
        bad.write_text('{not json')
        assert await b.restore_open_state(bad, lambda s: [], [SYM]) == (0, [])

    @pytest.mark.asyncio
    async def test_rank_1_keeps_its_position_when_the_best_preset_changes(self, tmp_path):
        sim = make_simulator(tmp_path, rank_max=3)
        rec = make_rec()
        with patch('bot.virtual_order_simulator.RecommendationEngine') as eng, \
             patch('bot.virtual_order_simulator.dataclasses') as dc:
            eng.return_value.generate.return_value = rec
            dc.replace.return_value = make_preset_settings()
            await sim.on_candle_close(SYM, make_analyzer(), 'preset_a', MagicMock())
            assert sim._rank_open[1][SYM]['preset_name'] == 'preset_a'

            # preset_b becomes the best
            vt = sim._virtual_tracker
            vt.get_preset_rank_key.side_effect = lambda s, n: (1, {'preset_a': 1.0, 'preset_b': 3.0, 'preset_c': 2.0}.get(n, 0.0))
            vt.get_preset_efficiency.side_effect = lambda s, n: {'preset_a': 1.0, 'preset_b': 3.0, 'preset_c': 2.0}.get(n, 0.0)
            await sim.on_candle_close(SYM, make_analyzer(price=50500.0), 'preset_b', MagicMock())

        assert sim._rank_open[1][SYM]['preset_name'] == 'preset_a', 'rank 1 evicted on a ranking change'
        assert sim.last_candle_summary[SYM]['r1:slot_held_by_other_preset'] == 1
        f = tmp_path / 'data' / f'virtual_orders_rank1_{SYM}_test.json'
        assert not f.exists() or not [r for r in json.loads(f.read_text()) if r.get('result') == 'rank_change']

    def test_stop_saves_virtual_positions_instead_of_closing_them(self):
        stop = MAIN.split('async def on_stop_bot', 1)[1].split('async def _close_virtual_for_switch', 1)[0]
        assert 'save_open_state(_vstate_path)' in stop
        # close_all_open survives only as the fallback when the save itself fails
        assert stop.count('close_all_open') == 1 and 'save failed' in stop

    def test_state_is_saved_every_candle_and_restored_at_startup(self):
        assert MAIN.count('virtual_order_simulator.save_open_state(_vstate_path)') >= 3
        assert 'restore_open_state(' in MAIN
        assert MAIN.index('restore_open_state(') < MAIN.index('async def on_price_update')

    def test_a_mode_switch_empties_the_saved_state(self):
        fn = MAIN.split('async def _close_virtual_for_switch', 1)[1].split('async def ', 1)[0]
        assert 'close_all_open' in fn and 'save_open_state' in fn
        assert 'close_virtual=_close_virtual_for_switch' in MAIN

    def test_real_max_age_is_off_by_default_and_closes_as_max_age(self):
        from config.risk_config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG['real_max_age_candles'] == 0
        assert "risk_cfg.get('real_max_age_candles', 0)" in MAIN
        assert "close_order(symbol, reason='max_age')" in MAIN


# ══════════════════════ Virtual/real parity, part 2 ══════════════════════
# (was test_parity_part2.py; spec docs/specs/2026-09-29-virtual-real-parity.md)

LOT = {'step_size': '0.01', 'min_qty': 0.01, 'max_qty': 0.0, 'min_notional': 5.0}


def _parity_open(sim, rank=2, opened=None, side='BUY', entry=100.0, tp=110.0, sl=95.0, **fake_kw):
    opened = opened or datetime.now(timezone.utc) - timedelta(hours=2)
    sim._rank_open[rank][SYM] = {
        'preset_name': 'preset_b', 'rank': rank, 'side': side, 'entry_price': entry,
        'tp': tp, 'sl': sl, 'quantity': 1.0, 'leverage': 1, 'open_time': opened.isoformat(),
        'status': 'open', 'close_price': None, 'close_time': None, 'pnl_usdt': None,
        'result': None, 'candles_seen': 0,
    }
    sim._rank_fake[rank][SYM] = FakeOrder(side=side, entry_price=entry, tp=tp, sl=sl, level=2,
                                          signal_type='x', candle_index=0, **fake_kw)


async def _parity_candle(sim, **kw):
    ps = make_preset_settings()
    for k, v in kw.pop('settings', {}).items():
        setattr(ps, k, v)
    with patch('bot.virtual_order_simulator.RecommendationEngine') as eng, \
         patch('bot.virtual_order_simulator.dataclasses') as dc:
        eng.return_value.generate.return_value = make_rec()
        dc.replace.return_value = ps
        await sim.on_candle_close(SYM, make_analyzer(), 'preset_a', MagicMock(), **kw)


def _trailing_fake():
    f = FakeOrder(side='BUY', entry_price=100.0, tp=130.0, sl=95.0, level=2, signal_type='x',
                  candle_index=0)
    f._partial_armed, f._partial_price, f._trailing_stop_pct, f._max_favorable = True, 102.0, 0.5, 110.0
    return f


def _executor_with_position(tmp_path, fake):
    ex = make_executor(tmp_path)
    ex._open_orders[SYM] = OpenOrder(symbol=SYM, preset_name='p', side='BUY', entry_price=100.0,
                                     tp_price=130.0, sl_price=95.0, quantity=1.0, leverage=5,
                                     sl_order_id='old', exchange_sl_price=95.0)
    ex._fake_orders[SYM] = fake
    ex._last_tick[SYM] = 108.0
    ex._place_sl_on_exchange = AsyncMock(return_value='new')
    ex._cancel_exchange_order = AsyncMock(return_value=True)
    return ex


def _restored_executor(tmp_path, fake, saved_at, opened):
    ex = make_executor(tmp_path)
    ex._open_orders[SYM] = OpenOrder(symbol=SYM, preset_name='p', side='BUY', entry_price=100.0,
                                     tp_price=110.0, sl_price=95.0, quantity=1.0, leverage=5,
                                     open_time=opened.isoformat())
    ex._fake_orders[SYM] = fake
    ex._restored_saved_at[SYM] = saved_at
    ex._finalize_close = AsyncMock(return_value={'symbol': SYM, 'preset_name': 'p', 'result': 'win',
                                                 'pnl_usdt': 5.0, 'close_price': 111.0, 'entry_price': 100.0})
    return ex


class TestVirtualRealParityPart2:
    """Virtual/real parity, part 2 (spec docs/specs/2026-09-29-virtual-real-parity.md)."""

    @pytest.fixture(autouse=True)
    def _clean_guard(self):
        rl_guard.reset()
        yield
        rl_guard.reset()

    # ── V3 sizing ────────────────────────────────────────────────────────────────

    def test_real_quantity_applies_the_buffer_and_floors_to_the_step(self):
        # 100 margin x 5 lev x 1.02 / 33 = 15.4545... -> 15.45
        assert real_quantity(100, 5, 33.0, LOT) == 15.45

    def test_real_quantity_bumps_one_step_to_reach_min_notional(self):
        lot = {**LOT, 'min_notional': 10.0}
        # 1.9 x 1 x 1.02 / 2 = 0.969 -> 0.96 (notional 1.92 < 10) ... one step is not enough
        assert real_quantity(1.9, 1, 2.0, lot) == 0.0
        # 4.85 x 1.02 / 1 = 4.947 -> 4.94 -> notional 4.94 < 5 -> bump to 4.95
        assert real_quantity(4.85, 1, 1.0, {**LOT, 'min_notional': 4.95}) == 4.95

    def test_real_quantity_caps_notional_floored_to_the_step(self):
        assert real_quantity(1000, 5, 0.83, LOT, max_notional=1000) == 1204.81

    def test_real_quantity_refuses_below_min_qty(self):
        assert real_quantity(0.001, 1, 100.0, {**LOT, 'min_notional': 0.0}) == 0.0

    # ── V1 candle check ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_a_wick_through_the_sl_closes_the_virtual_position(self, tmp_path):
        sim = make_simulator(tmp_path)
        _parity_open(sim)
        closed = await sim.check_candle(SYM, 100.0, 101.0, 94.0, 99.0, _now_ms() - 1000)
        assert [c['result'] for c in closed] == ['loss'] and closed[0]['close_price'] == 95.0
        assert SYM not in sim._rank_open[2]

    @pytest.mark.asyncio
    async def test_a_candle_that_ended_before_the_position_is_skipped(self, tmp_path):
        sim = make_simulator(tmp_path)
        _parity_open(sim, opened=datetime.now(timezone.utc))
        closed = await sim.check_candle(SYM, 100.0, 101.0, 90.0, 99.0, _now_ms() - 60_000)
        assert closed == [] and sim._rank_open[2][SYM]['candles_seen'] == 0

    @pytest.mark.asyncio
    async def test_max_losing_candles_now_fires_for_virtual(self, tmp_path):
        sim = make_simulator(tmp_path)
        _parity_open(sim, max_losing_candles=2)
        past = _now_ms() - 1000
        assert await sim.check_candle(SYM, 100.0, 100.5, 98.0, 99.0, past) == []
        closed = await sim.check_candle(SYM, 99.0, 99.5, 97.0, 98.0, past)
        assert [c['result'] for c in closed] == ['loss']

    def test_main_runs_the_virtual_candle_check_on_both_candle_paths(self):
        assert MAIN.count('await _virtual_candle_check(symbol,') == 2

    # ── V4 inherited gates ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_blackout_hours_block_ranks_2_and_up_but_not_rank_1(self, tmp_path):
        sim = make_simulator(tmp_path)
        hour = datetime.now(timezone.utc).hour
        with patch('bot.virtual_order_simulator.load_risk_config',
                   return_value={'trading_blackout_hours': [hour]}):
            await _parity_candle(sim)
        assert SYM in sim._rank_open[1], 'rank 1 records refused signals; it must not inherit gates'
        assert SYM not in sim._rank_open[2]
        assert sim.last_candle_summary[SYM]['blackout_hour'] >= 1

    @pytest.mark.asyncio
    async def test_a_loss_streak_blocks_that_preset_and_side(self, tmp_path):
        sim = make_simulator(tmp_path)
        rec = {'preset_name': 'preset_b', 'side': 'BUY', 'sl': 95.0, 'gates': {
            'loss_streak_max': 2, 'loss_streak_cooldown_candles': 4, 'global_pause_trigger_candles': 0,
            'global_pause_candles': 0, 'zone_sl_max': 0, 'zone_sl_cooldown_candles': 0,
            'duplicate_skip_pct': 0.0, 'tf_ms': 900_000}}
        now = _now_ms()
        sim._gate_update(SYM, rec, 'loss', -1.0, now)
        ps = MagicMock(loss_streak_max=2, zone_sl_max=0)
        assert sim._gate_block(SYM, 'preset_b', 'BUY', ps) is None
        sim._gate_update(SYM, rec, 'loss', -1.0, now)
        assert sim._gate_block(SYM, 'preset_b', 'BUY', ps) == 'loss_streak_cooldown'
        assert sim._gate_block(SYM, 'preset_b', 'SELL', ps) is None
        assert sim._gate_block(SYM, 'preset_c', 'BUY', ps) is None

    @pytest.mark.asyncio
    async def test_gate_state_survives_a_restart(self, tmp_path):
        a = make_simulator(tmp_path)
        a._gate['streak_until'][f'{SYM}:preset_b:BUY'] = _now_ms() + 3_600_000
        path = tmp_path / 's.json'
        a.save_open_state(path)
        b = make_simulator(tmp_path)
        await b.restore_open_state(path, lambda s: [], [SYM])
        assert b._gate_block(SYM, 'preset_b', 'BUY', MagicMock(loss_streak_max=0)) == 'loss_streak_cooldown'

    # ── V2 leverage ──────────────────────────────────────────────────────────────

    def test_virtual_leverage_uses_the_exchange_bracket(self):
        fn = MAIN.split('def _virtual_lev', 1)[1].split('\n    def ', 1)[0]
        assert 'order_executor.get_bracket_max(sym)' in fn and '125' not in fn.split('"""')[-1].replace('was a best-case 125', '')
        assert 'get_bracket_max=order_executor.get_bracket_max' in MAIN

    # ── R1 / R2 / R3 ─────────────────────────────────────────────────────────────

    def test_duplicate_skip_counts_from_the_sl_hit_candle(self):
        assert MAIN.count("{**_sig, 'candle_ts': candle_ts}") == 1
        assert MAIN.count("{**_sig, 'candle_ts': _approx_candle_ts}") == 1

    def test_real_candle_counter_survives_a_restart(self, tmp_path):
        ex = make_executor(tmp_path)
        ex._open_orders[SYM] = OpenOrder(symbol=SYM, preset_name='p', side='BUY', entry_price=100.0,
                                         tp_price=110.0, sl_price=95.0, quantity=1.0, leverage=5)
        ex._fake_orders[SYM] = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=95.0, level=2,
                                         signal_type='x', candle_index=0)
        ex._symbol_candle_index[SYM] = 7
        path = tmp_path / 'restart.json'
        ex.save_open_positions(path)
        ex2 = make_executor(tmp_path)
        ex2.restore_open_positions(path)
        assert ex2._symbol_candle_index[SYM] == 7

    def test_real_close_records_the_software_exit_price(self, tmp_path):
        ex = make_executor(tmp_path)
        ex._project_root = tmp_path
        order = OpenOrder(symbol=SYM, preset_name='p', side='BUY', entry_price=100.0,
                          tp_price=110.0, sl_price=95.0, quantity=1.0, leverage=5)
        ex._record_real_order_close(SYM, order, 94.9, 'loss', -5.1, exit_trigger_price=95.0)
        rec = json.loads((tmp_path / 'data' / f'real_orders_{SYM}_test.json').read_text())[-1]
        assert rec['exit_trigger_price'] == 95.0 and rec['close_price'] == 94.9

    # ── R4 exchange stop follows the software stop ───────────────────────────────

    def test_protective_stop_follows_trail_partial_early_exit_and_sl(self):
        assert _trailing_fake().protective_stop() == pytest.approx(105.0)   # 110 - 0.5 x 10
        f = FakeOrder(side='BUY', entry_price=100.0, tp=130.0, sl=95.0, level=2, signal_type='x',
                      candle_index=0)
        assert f.protective_stop() == 95.0
        f._early_loss_sl = 97.0
        assert f.protective_stop() == 97.0
        s = FakeOrder(side='SELL', entry_price=100.0, tp=80.0, sl=105.0, level=2, signal_type='x',
                      candle_index=0)
        assert s.protective_stop() == 105.0

    @pytest.mark.asyncio
    async def test_the_exchange_stop_moves_new_first_then_cancels_old(self, tmp_path):
        ex = _executor_with_position(tmp_path, _trailing_fake())
        calls = []
        ex._place_sl_on_exchange.side_effect = lambda *a, **k: calls.append('place') or 'new'
        ex._cancel_exchange_order.side_effect = lambda *a, **k: calls.append('cancel') or True
        assert await ex.sync_exchange_stop(SYM, buffer_pct=0.1, min_move_pct=0.1) is True
        target = ex._place_sl_on_exchange.call_args.args[3]
        assert target == pytest.approx(105.0 * 0.999)       # 0.1 % beyond the software stop
        assert calls == ['place', 'cancel']
        assert ex._open_orders[SYM].sl_order_id == 'new'
        assert ex._open_orders[SYM].exchange_sl_price == pytest.approx(105.0 * 0.999)

    @pytest.mark.asyncio
    async def test_no_move_when_not_tighter_or_too_small(self, tmp_path):
        f = FakeOrder(side='BUY', entry_price=100.0, tp=130.0, sl=95.0, level=2, signal_type='x',
                      candle_index=0)
        ex = _executor_with_position(tmp_path, f)
        assert await ex.sync_exchange_stop(SYM) is False       # software stop == SL, buffer is looser
        ex._place_sl_on_exchange.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_move_while_banned_or_when_it_would_trigger(self, tmp_path):
        ex = _executor_with_position(tmp_path, _trailing_fake())
        ex._last_tick[SYM] = 104.0                              # stop would be above the market
        assert await ex.sync_exchange_stop(SYM) is False
        ex._last_tick[SYM] = 108.0
        with patch('bot.order_executor.rl_guard.blocked_for', return_value=30.0):
            assert await ex.sync_exchange_stop(SYM) is False
        ex._place_sl_on_exchange.assert_not_called()

    def test_main_syncs_the_exchange_stop_each_candle_unless_virtual_only(self):
        assert 'order_executor.sync_exchange_stop(' in MAIN
        i = MAIN.index('order_executor.sync_exchange_stop(')
        assert "not _virtual_only and risk_cfg.get('exchange_sl_follow_trail', True)" in MAIN[i - 400:i]

    # ── Part 3 ───────────────────────────────────────────────────────────────────

    def test_a_symbol_is_only_placed_once_its_own_candle_is_in(self):
        loop = MAIN.split('for sym in _placement_symbols:', 1)[1].split('best_sym = _sym_az.get_best_recommendation()', 1)[0]
        assert '_sym_az.last_candle_open() < candle_ts' in loop

    def test_analyzer_reports_its_newest_candle(self):
        from bot.analyzer import Analyzer
        a = Analyzer.__new__(Analyzer)
        a._klines = []
        assert a.last_candle_open() == 0
        a._klines = [[1000, '1', '2', '0.5', '1.5'], [2000, '1', '2', '0.5', '1.5']]
        assert a.last_candle_open() == 2000

    @pytest.mark.asyncio
    async def test_rank_1_uses_the_substituted_preset(self, tmp_path):
        sim = make_simulator(tmp_path)
        await _parity_candle(sim, substituted_preset='preset_c')
        assert sim._rank_open[1][SYM]['preset_name'] == 'preset_c'
        assert 'substituted_preset=_substituted_preset.get(symbol)' in MAIN

    @pytest.mark.asyncio
    async def test_a_tp_hit_during_the_restart_closes_the_real_position_now(self, tmp_path):
        fake = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=95.0, level=2, signal_type='x', candle_index=0)
        saved = _now_ms() - 3 * 900_000
        ex = _restored_executor(tmp_path, fake, saved, datetime.now(timezone.utc) - timedelta(hours=3))
        k = lambda close_ms, h, l: [close_ms - 900_000 + 1, '100', str(h), str(l), '100', '0', close_ms]
        out = await ex.replay_downtime(SYM, [k(saved - 1000, 120, 99),          # before the save
                                             k(saved + 900_000, 111, 99)])      # TP during downtime
        assert out and out[0]['result'] == 'win'
        assert ex._finalize_close.call_args.args[2] == 'win' and ex._finalize_close.call_args.args[3] == 110.0

    @pytest.mark.asyncio
    async def test_no_real_replay_without_a_saved_time(self, tmp_path):
        fake = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=95.0, level=2, signal_type='x', candle_index=0)
        ex = _restored_executor(tmp_path, fake, 0, datetime.now(timezone.utc) - timedelta(hours=3))
        assert await ex.replay_downtime(SYM, [[1, '100', '200', '1', '100', '0', 2]]) == []
        ex._finalize_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_virtual_tp_during_the_restart_closes_at_the_restart_price(self, tmp_path):
        a = make_simulator(tmp_path)
        _parity_open(a, tp=110.0)
        path = tmp_path / 's.json'
        a.save_open_state(path)
        saved = json.loads(path.read_text())['saved_at_ms']
        klines = [[saved + 1, '100', '111', '99', '108', '0', saved + 900_000],
                  [saved + 900_001, '108', '109', '107', '107.5', '0', _now_ms() + 900_000]]  # forming
        b = make_simulator(tmp_path)
        with patch('bot.virtual_order_simulator.datetime', wraps=datetime) as dt:
            dt.now.return_value = datetime.fromtimestamp((saved + 1_000_000) / 1000, tz=timezone.utc)
            dt.fromtimestamp = datetime.fromtimestamp
            dt.fromisoformat = datetime.fromisoformat
            _, closed = await b.restore_open_state(path, lambda s: klines, [SYM])
        assert closed[0][1]['result'] == 'win' and closed[0][1]['close_price'] == 107.5

    def test_main_replays_restored_real_positions_before_reconciling(self):
        assert MAIN.index('order_executor.replay_downtime(') < MAIN.index('await order_executor.reconcile_with_exchange()')

    @pytest.mark.asyncio
    async def test_no_stop_move_while_an_old_stop_awaits_cancel(self, tmp_path):
        ex = _executor_with_position(tmp_path, _trailing_fake())
        ex._pending_sl_cancels[SYM] = ['stale']
        assert await ex.sync_exchange_stop(SYM) is False
        ex._place_sl_on_exchange.assert_not_called()

    def test_real_max_age_uses_the_configured_timeframe(self):
        assert '_real_max_age * _tf_to_ms(timeframe) / 60_000.0' in MAIN
