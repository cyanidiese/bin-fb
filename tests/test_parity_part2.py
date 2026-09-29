# tests/test_parity_part2.py
"""Virtual/real parity, part 2 (spec docs/specs/2026-09-29-virtual-real-parity.md)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.fake_order import FakeOrder
from bot.order_executor import OpenOrder
from bot.order_sizing import real_quantity
from bot.rate_limit_guard import guard as rl_guard

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_order_executor import make_executor  # noqa: E402
from test_virtual_order_simulator import (  # noqa: E402
    make_analyzer, make_preset_settings, make_rec, make_simulator,
)

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'main.py').read_text()
SYM = 'BTCUSDT'
LOT = {'step_size': '0.01', 'min_qty': 0.01, 'max_qty': 0.0, 'min_notional': 5.0}


@pytest.fixture(autouse=True)
def _clean_guard():
    rl_guard.reset()
    yield
    rl_guard.reset()


# ── V3 sizing ────────────────────────────────────────────────────────────────

def test_real_quantity_applies_the_buffer_and_floors_to_the_step():
    # 100 margin x 5 lev x 1.02 / 33 = 15.4545... -> 15.45
    assert real_quantity(100, 5, 33.0, LOT) == 15.45


def test_real_quantity_bumps_one_step_to_reach_min_notional():
    lot = {**LOT, 'min_notional': 10.0}
    # 1.9 x 1 x 1.02 / 2 = 0.969 -> 0.96 (notional 1.92 < 10) ... one step is not enough
    assert real_quantity(1.9, 1, 2.0, lot) == 0.0
    # 4.85 x 1.02 / 1 = 4.947 -> 4.94 -> notional 4.94 < 5 -> bump to 4.95
    assert real_quantity(4.85, 1, 1.0, {**LOT, 'min_notional': 4.95}) == 4.95


def test_real_quantity_caps_notional_floored_to_the_step():
    assert real_quantity(1000, 5, 0.83, LOT, max_notional=1000) == 1204.81


def test_real_quantity_refuses_below_min_qty():
    assert real_quantity(0.001, 1, 100.0, {**LOT, 'min_notional': 0.0}) == 0.0


# ── V1 candle check ──────────────────────────────────────────────────────────

def _open(sim, rank=2, opened=None, side='BUY', entry=100.0, tp=110.0, sl=95.0, **fake_kw):
    opened = opened or datetime.now(timezone.utc) - timedelta(hours=2)
    sim._rank_open[rank][SYM] = {
        'preset_name': 'preset_b', 'rank': rank, 'side': side, 'entry_price': entry,
        'tp': tp, 'sl': sl, 'quantity': 1.0, 'leverage': 1, 'open_time': opened.isoformat(),
        'status': 'open', 'close_price': None, 'close_time': None, 'pnl_usdt': None,
        'result': None, 'candles_seen': 0,
    }
    sim._rank_fake[rank][SYM] = FakeOrder(side=side, entry_price=entry, tp=tp, sl=sl, level=2,
                                          signal_type='x', candle_index=0, **fake_kw)


def _now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@pytest.mark.asyncio
async def test_a_wick_through_the_sl_closes_the_virtual_position(tmp_path):
    sim = make_simulator(tmp_path)
    _open(sim)
    closed = await sim.check_candle(SYM, 100.0, 101.0, 94.0, 99.0, _now_ms() - 1000)
    assert [c['result'] for c in closed] == ['loss'] and closed[0]['close_price'] == 95.0
    assert SYM not in sim._rank_open[2]


@pytest.mark.asyncio
async def test_a_candle_that_ended_before_the_position_is_skipped(tmp_path):
    sim = make_simulator(tmp_path)
    _open(sim, opened=datetime.now(timezone.utc))
    closed = await sim.check_candle(SYM, 100.0, 101.0, 90.0, 99.0, _now_ms() - 60_000)
    assert closed == [] and sim._rank_open[2][SYM]['candles_seen'] == 0


@pytest.mark.asyncio
async def test_max_losing_candles_now_fires_for_virtual(tmp_path):
    sim = make_simulator(tmp_path)
    _open(sim, max_losing_candles=2)
    past = _now_ms() - 1000
    assert await sim.check_candle(SYM, 100.0, 100.5, 98.0, 99.0, past) == []
    closed = await sim.check_candle(SYM, 99.0, 99.5, 97.0, 98.0, past)
    assert [c['result'] for c in closed] == ['loss']


def test_main_runs_the_virtual_candle_check_on_both_candle_paths():
    assert MAIN.count('await _virtual_candle_check(symbol,') == 2


# ── V4 inherited gates ───────────────────────────────────────────────────────

async def _candle(sim, **kw):
    ps = make_preset_settings()
    for k, v in kw.pop('settings', {}).items():
        setattr(ps, k, v)
    with patch('bot.virtual_order_simulator.RecommendationEngine') as eng, \
         patch('bot.virtual_order_simulator.dataclasses') as dc:
        eng.return_value.generate.return_value = make_rec()
        dc.replace.return_value = ps
        await sim.on_candle_close(SYM, make_analyzer(), 'preset_a', MagicMock(), **kw)


@pytest.mark.asyncio
async def test_blackout_hours_block_ranks_2_and_up_but_not_rank_1(tmp_path):
    sim = make_simulator(tmp_path)
    hour = datetime.now(timezone.utc).hour
    with patch('bot.virtual_order_simulator.load_risk_config',
               return_value={'trading_blackout_hours': [hour]}):
        await _candle(sim)
    assert SYM in sim._rank_open[1], 'rank 1 records refused signals; it must not inherit gates'
    assert SYM not in sim._rank_open[2]
    assert sim.last_candle_summary[SYM]['blackout_hour'] >= 1


@pytest.mark.asyncio
async def test_a_loss_streak_blocks_that_preset_and_side(tmp_path):
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
async def test_gate_state_survives_a_restart(tmp_path):
    a = make_simulator(tmp_path)
    a._gate['streak_until'][f'{SYM}:preset_b:BUY'] = _now_ms() + 3_600_000
    path = tmp_path / 's.json'
    a.save_open_state(path)
    b = make_simulator(tmp_path)
    await b.restore_open_state(path, lambda s: [], [SYM])
    assert b._gate_block(SYM, 'preset_b', 'BUY', MagicMock(loss_streak_max=0)) == 'loss_streak_cooldown'


# ── V2 leverage ──────────────────────────────────────────────────────────────

def test_virtual_leverage_uses_the_exchange_bracket():
    fn = MAIN.split('def _virtual_lev', 1)[1].split('\n    def ', 1)[0]
    assert 'order_executor.get_bracket_max(sym)' in fn and '125' not in fn.split('"""')[-1].replace('was a best-case 125', '')
    assert 'get_bracket_max=order_executor.get_bracket_max' in MAIN


# ── R1 / R2 / R3 ─────────────────────────────────────────────────────────────

def test_duplicate_skip_counts_from_the_sl_hit_candle():
    assert MAIN.count("{**_sig, 'candle_ts': candle_ts}") == 1
    assert MAIN.count("{**_sig, 'candle_ts': _approx_candle_ts}") == 1


def test_real_candle_counter_survives_a_restart(tmp_path):
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


def test_real_close_records_the_software_exit_price(tmp_path):
    ex = make_executor(tmp_path)
    ex._project_root = tmp_path
    order = OpenOrder(symbol=SYM, preset_name='p', side='BUY', entry_price=100.0,
                      tp_price=110.0, sl_price=95.0, quantity=1.0, leverage=5)
    ex._record_real_order_close(SYM, order, 94.9, 'loss', -5.1, exit_trigger_price=95.0)
    rec = json.loads((tmp_path / 'data' / f'real_orders_{SYM}_test.json').read_text())[-1]
    assert rec['exit_trigger_price'] == 95.0 and rec['close_price'] == 94.9


# ── R4 exchange stop follows the software stop ───────────────────────────────

def _trailing_fake():
    f = FakeOrder(side='BUY', entry_price=100.0, tp=130.0, sl=95.0, level=2, signal_type='x',
                  candle_index=0)
    f._partial_armed, f._partial_price, f._trailing_stop_pct, f._max_favorable = True, 102.0, 0.5, 110.0
    return f


def test_protective_stop_follows_trail_partial_early_exit_and_sl():
    assert _trailing_fake().protective_stop() == pytest.approx(105.0)   # 110 - 0.5 x 10
    f = FakeOrder(side='BUY', entry_price=100.0, tp=130.0, sl=95.0, level=2, signal_type='x',
                  candle_index=0)
    assert f.protective_stop() == 95.0
    f._early_loss_sl = 97.0
    assert f.protective_stop() == 97.0
    s = FakeOrder(side='SELL', entry_price=100.0, tp=80.0, sl=105.0, level=2, signal_type='x',
                  candle_index=0)
    assert s.protective_stop() == 105.0


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


@pytest.mark.asyncio
async def test_the_exchange_stop_moves_new_first_then_cancels_old(tmp_path):
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
async def test_no_move_when_not_tighter_or_too_small(tmp_path):
    f = FakeOrder(side='BUY', entry_price=100.0, tp=130.0, sl=95.0, level=2, signal_type='x',
                  candle_index=0)
    ex = _executor_with_position(tmp_path, f)
    assert await ex.sync_exchange_stop(SYM) is False       # software stop == SL, buffer is looser
    ex._place_sl_on_exchange.assert_not_called()


@pytest.mark.asyncio
async def test_no_move_while_banned_or_when_it_would_trigger(tmp_path):
    ex = _executor_with_position(tmp_path, _trailing_fake())
    ex._last_tick[SYM] = 104.0                              # stop would be above the market
    assert await ex.sync_exchange_stop(SYM) is False
    ex._last_tick[SYM] = 108.0
    with patch('bot.order_executor.rl_guard.blocked_for', return_value=30.0):
        assert await ex.sync_exchange_stop(SYM) is False
    ex._place_sl_on_exchange.assert_not_called()


def test_main_syncs_the_exchange_stop_each_candle_unless_virtual_only():
    assert 'order_executor.sync_exchange_stop(' in MAIN
    i = MAIN.index('order_executor.sync_exchange_stop(')
    assert "not _virtual_only and risk_cfg.get('exchange_sl_follow_trail', True)" in MAIN[i - 400:i]


# ── Part 3 ───────────────────────────────────────────────────────────────────

def test_a_symbol_is_only_placed_once_its_own_candle_is_in():
    loop = MAIN.split('for sym in _placement_symbols:', 1)[1].split('best_sym = _sym_az.get_best_recommendation()', 1)[0]
    assert '_sym_az.last_candle_open() < candle_ts' in loop


def test_analyzer_reports_its_newest_candle():
    from bot.analyzer import Analyzer
    a = Analyzer.__new__(Analyzer)
    a._klines = []
    assert a.last_candle_open() == 0
    a._klines = [[1000, '1', '2', '0.5', '1.5'], [2000, '1', '2', '0.5', '1.5']]
    assert a.last_candle_open() == 2000


@pytest.mark.asyncio
async def test_rank_1_uses_the_substituted_preset(tmp_path):
    sim = make_simulator(tmp_path)
    await _candle(sim, substituted_preset='preset_c')
    assert sim._rank_open[1][SYM]['preset_name'] == 'preset_c'
    assert 'substituted_preset=_substituted_preset.get(symbol)' in MAIN


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


@pytest.mark.asyncio
async def test_a_tp_hit_during_the_restart_closes_the_real_position_now(tmp_path):
    fake = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=95.0, level=2, signal_type='x', candle_index=0)
    saved = _now_ms() - 3 * 900_000
    ex = _restored_executor(tmp_path, fake, saved, datetime.now(timezone.utc) - timedelta(hours=3))
    k = lambda close_ms, h, l: [close_ms - 900_000 + 1, '100', str(h), str(l), '100', '0', close_ms]
    out = await ex.replay_downtime(SYM, [k(saved - 1000, 120, 99),          # before the save
                                         k(saved + 900_000, 111, 99)])      # TP during downtime
    assert out and out[0]['result'] == 'win'
    assert ex._finalize_close.call_args.args[2] == 'win' and ex._finalize_close.call_args.args[3] == 110.0


@pytest.mark.asyncio
async def test_no_real_replay_without_a_saved_time(tmp_path):
    fake = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=95.0, level=2, signal_type='x', candle_index=0)
    ex = _restored_executor(tmp_path, fake, 0, datetime.now(timezone.utc) - timedelta(hours=3))
    assert await ex.replay_downtime(SYM, [[1, '100', '200', '1', '100', '0', 2]]) == []
    ex._finalize_close.assert_not_called()


@pytest.mark.asyncio
async def test_a_virtual_tp_during_the_restart_closes_at_the_restart_price(tmp_path):
    a = make_simulator(tmp_path)
    _open(a, tp=110.0)
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


def test_main_replays_restored_real_positions_before_reconciling():
    assert MAIN.index('order_executor.replay_downtime(') < MAIN.index('await order_executor.reconcile_with_exchange()')


@pytest.mark.asyncio
async def test_no_stop_move_while_an_old_stop_awaits_cancel(tmp_path):
    ex = _executor_with_position(tmp_path, _trailing_fake())
    ex._pending_sl_cancels[SYM] = ['stale']
    assert await ex.sync_exchange_stop(SYM) is False
    ex._place_sl_on_exchange.assert_not_called()


def test_real_max_age_uses_the_configured_timeframe():
    assert '_real_max_age * _tf_to_ms(timeframe) / 60_000.0' in MAIN
