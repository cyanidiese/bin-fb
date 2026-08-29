# tests/test_virtual_order_simulator.py
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from bot.virtual_tracker import VirtualTracker


# ── VirtualTracker tests (unchanged) ──────────────────────────────────────


def _seed_factor() -> float:
    """The live backtest_seed_leverage_factor seed_from_backtest will apply."""
    from config.risk_config import load_risk_config
    return float(load_risk_config().get("backtest_seed_leverage_factor", 1.0))


def make_tracker(tmp_path):
    return VirtualTracker(
        mode='test',
        orders_path=tmp_path / 'virtual_orders_test.json',
        efficiency_path=tmp_path / 'preset_efficiency_test.json',
    )


def make_backtest_file(tmp_path, symbol='BTCUSDT'):
    data = {
        'presets': {
            'preset_a': {
                'balance_start': 1000.0,
                'total_trades': 3,
                'trades': [
                    {'profit_pct': 1.0},
                    {'profit_pct': -0.5},
                    {'profit_pct': 2.0},
                ],
            },
            'preset_b': {
                'balance_start': 1000.0,
                'total_trades': 2,
                'trades': [
                    {'profit_pct': -1.0},
                    {'profit_pct': -0.5},
                ],
            },
        }
    }
    p = tmp_path / f'backtest_results_{symbol}.json'
    p.write_text(json.dumps(data))
    return p


def test_seed_from_backtest_populates_efficiency(tmp_path):
    tracker = make_tracker(tmp_path)
    bt_path = make_backtest_file(tmp_path)
    tracker.seed_from_backtest('BTCUSDT', bt_path)
    eff = tracker.get_efficiency('BTCUSDT', 'preset_a')
    # seed_from_backtest stores backtest score in seeded_winning_usdt;
    # trade_count stays 0 so UI won't show backtest history as live trades.
    assert eff['trade_count'] == 0
    assert eff['seeded_winning_usdt'] == pytest.approx(25.0 * _seed_factor())


def test_seed_from_backtest_skips_if_symbol_already_seeded(tmp_path):
    tracker = make_tracker(tmp_path)
    bt_path = make_backtest_file(tmp_path)
    tracker.seed_from_backtest('BTCUSDT', bt_path)
    bt_path.write_text('{"presets": {}}')  # empty presets — nothing to overwrite
    tracker.seed_from_backtest('BTCUSDT', bt_path)
    eff = tracker.get_efficiency('BTCUSDT', 'preset_a')
    assert eff['seeded_winning_usdt'] == pytest.approx(25.0 * _seed_factor())  # original preserved


def test_seed_from_backtest_seeds_new_symbol_even_if_other_exists(tmp_path):
    tracker = make_tracker(tmp_path)
    bt_path_btc = make_backtest_file(tmp_path, 'BTCUSDT')
    bt_path_eth = make_backtest_file(tmp_path, 'ETHUSDT')
    tracker.seed_from_backtest('BTCUSDT', bt_path_btc)
    tracker.seed_from_backtest('ETHUSDT', bt_path_eth)
    assert tracker.get_efficiency('ETHUSDT', 'preset_a')['seeded_winning_usdt'] == pytest.approx(25.0 * _seed_factor())


# ── VirtualOrderSimulator rank-based tests ────────────────────────────────

from bot.virtual_order_simulator import VirtualOrderSimulator


def make_vt_with_scores(scores: dict):
    """Return a VirtualTracker mock that returns per-preset efficiency scores."""
    vt = MagicMock()
    vt.get_preset_efficiency.side_effect = lambda symbol, name: scores.get(name, 0.0)
    return vt


def make_simulator(tmp_path, initial_balance=1000.0, rank_max=4, scores=None):
    """
    3 presets ranked preset_a > preset_b > preset_c by default.
    rank_max=4 means we track ranks 2, 3, 4 (rank 1 = real, not tracked here).
    """
    if scores is None:
        scores = {'preset_a': 3.0, 'preset_b': 2.0, 'preset_c': 1.0}
    vt = make_vt_with_scores(scores)
    return VirtualOrderSimulator(
        mode='test',
        all_presets={'preset_a': {}, 'preset_b': {}, 'preset_c': {}},
        project_root=tmp_path,
        get_leverage=lambda sym: 1,
        initial_balance=initial_balance,
        virtual_tracker=vt,
        min_notionals={'BTCUSDT': 5.0},
        rank_max=rank_max,
    )


def make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0):
    rec = MagicMock()
    rec.getSide.return_value = side
    rec.getEntryPrice.return_value = entry
    rec.getTarget.return_value = tp
    rec.getStop.return_value = sl
    rec.getLevel.return_value = 1
    rec.getType.return_value = MagicMock(value='test_signal')
    return rec


def make_analyzer(price=50000.0):
    a = MagicMock()
    a.get_current_price.return_value = price
    a.get_trend.return_value = MagicMock()
    a.get_klines.return_value = []
    return a


def make_preset_settings():
    """Return a MagicMock preset settings with all filter thresholds disabled (0 = off)."""
    s = MagicMock()
    s.tp_multiplier = 1.0
    s.max_profit_pct = 0.0
    s.min_sl_pct = 0.0
    s.max_sl_pct = 0.0
    s.min_sl_atr_mult = 0.0
    s.atr_lookback = 0
    s.min_profit_loss_ratio = 0.0
    s.sl_adjust_to_rr = False
    s.duplicate_skip_candles = 0
    s.partial_take_pct = 0.0
    s.trailing_stop_pct = 0.0
    s.max_losing_pct = 0.0
    s.max_losing_candles = 0
    s.max_losing_amount_usdt = 0.0
    return s


# ── balance initialisation ─────────────────────────────────────────────────

def test_rank_balances_initialised(tmp_path):
    sim = make_simulator(tmp_path, initial_balance=500.0, rank_max=3)
    balances = sim.get_rank_balances()
    assert set(balances.keys()) == {2, 3}
    assert all(v == 500.0 for v in balances.values())


def test_sync_real_balance_writes_every_rank_file(tmp_path):
    sim = make_simulator(tmp_path, initial_balance=0.0, rank_max=3)
    sim.sync_real_balance_on_start(777.0)
    for rank in (2, 3):
        path = tmp_path / 'data' / f'virtual_balance_rank{rank}_test.json'
        assert path.exists()
        assert json.loads(path.read_text())['balance'] == 777.0


def test_sync_real_balance_overwrites_existing_pool(tmp_path):
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


def test_sync_real_balance_ignores_non_positive(tmp_path):
    """A failed balance read returns 0.0 — it must not zero out the virtual pools."""
    sim = make_simulator(tmp_path, initial_balance=250.0, rank_max=3)
    sim.sync_real_balance_on_start(0.0)
    assert sim._rank_balance[2] == 250.0


def test_rank_balance_persists_to_disk(tmp_path):
    sim = make_simulator(tmp_path, initial_balance=200.0, rank_max=3)
    sim._rank_balance[2] = 250.0
    sim._save_rank_balance(2)
    path = tmp_path / 'data' / 'virtual_balance_rank2_test.json'
    data = json.loads(path.read_text())
    assert data['balance'] == 250.0


def test_rank_balance_loads_from_disk(tmp_path):
    # First instance persists rank-2 balance
    sim1 = make_simulator(tmp_path, initial_balance=200.0, rank_max=3)
    sim1._rank_balance[2] = 350.0
    sim1._save_rank_balance(2)
    # Second instance should load that value
    sim2 = make_simulator(tmp_path, initial_balance=999.0, rank_max=3)
    assert sim2._rank_balance[2] == 350.0


# ── candle close — opening positions ──────────────────────────────────────

@pytest.mark.asyncio
async def test_on_candle_close_opens_rank2_and_rank3(tmp_path):
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
async def test_on_candle_close_does_not_double_open(tmp_path):
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
async def test_no_signal_leaves_slot_empty(tmp_path):
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
async def test_check_prices_closes_on_tp(tmp_path):
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
async def test_check_prices_closes_on_sl(tmp_path):
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
async def test_check_prices_updates_rank_balance(tmp_path):
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
async def test_check_prices_returns_rank_in_closed_dict(tmp_path):
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
        assert c['rank'] in (2, 3, 4, 5, 6)


# ── rank eviction ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rank_change_evicts_old_preset(tmp_path):
    """When the rank-2 preset changes, the old position is evicted."""
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

    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
         patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
        MockEng.return_value.generate.return_value = rec
        mock_dc.replace.return_value = make_preset_settings()
        await sim.on_candle_close('BTCUSDT', make_analyzer(price=51000.0), 'preset_a', MagicMock())

    # preset_b evicted, preset_c now in rank-2 slot
    assert sim._rank_open[2]['BTCUSDT']['preset_name'] == 'preset_c'


@pytest.mark.asyncio
async def test_evicted_order_written_to_rank_file(tmp_path):
    """Evicted position is persisted to virtual_orders_rank2_{symbol}_{mode}.json."""
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
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
         patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
        MockEng.return_value.generate.return_value = rec
        mock_dc.replace.return_value = make_preset_settings()
        await sim.on_candle_close('BTCUSDT', make_analyzer(price=51000.0), 'preset_a', MagicMock())

    rank_file = tmp_path / 'data' / 'virtual_orders_rank2_BTCUSDT_test.json'
    assert rank_file.exists()
    records = json.loads(rank_file.read_text())
    evicted = [r for r in records if r.get('result') == 'rank_change']
    assert len(evicted) == 1
    assert evicted[0]['preset_name'] == 'preset_b'


# ── close_all_open ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_all_open_clears_all_ranks(tmp_path):
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
async def test_close_all_open_writes_closed_early_to_file(tmp_path):
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
