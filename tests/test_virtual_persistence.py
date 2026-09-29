# tests/test_virtual_persistence.py
"""Virtual/real parity (spec docs/specs/2026-09-29-virtual-real-parity.md):
virtual positions survive restarts instead of closing as 'closed_early', rank 1 no
longer evicts on a best-preset change, and real positions get an age limit knob."""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot.fake_order import FakeOrder
from bot.virtual_order_simulator import VirtualOrderSimulator
from tests.test_virtual_order_simulator import (
    make_analyzer, make_preset_settings, make_rec, make_vt_with_scores,
)

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'main.py').read_text()
SYM = 'BTCUSDT'
M15 = 15 * 60 * 1000


def _sim(tmp_path, rank_max=3):
    return VirtualOrderSimulator(
        mode='test', all_presets={'preset_a': {}, 'preset_b': {}, 'preset_c': {}},
        project_root=tmp_path, get_leverage=lambda s: 1, initial_balance=1000.0,
        virtual_tracker=make_vt_with_scores({'preset_a': 3.0, 'preset_b': 2.0, 'preset_c': 1.0}),
        min_notionals={SYM: 5.0}, rank_max=rank_max,
    )


def _open(sim, rank=2, opened=None, side='BUY', entry=100.0, tp=110.0, sl=95.0):
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


def _now_ms():
    return int(time.time() * 1000)


@pytest.mark.asyncio
async def test_positions_round_trip_and_stay_open(tmp_path):
    a = _sim(tmp_path)
    _open(a)
    path = tmp_path / 'state.json'
    assert a.save_open_state(path) == 1

    b = _sim(tmp_path)
    still_open, closed = await b.restore_open_state(path, lambda s: [], [SYM])
    assert (still_open, closed) == (1, [])
    assert b._rank_open[2][SYM]['preset_name'] == 'preset_b'
    assert b._rank_fake[2][SYM].sl == 95.0


@pytest.mark.asyncio
async def test_an_sl_hit_during_downtime_closes_at_that_candle(tmp_path):
    a = _sim(tmp_path)
    _open(a)
    path = tmp_path / 'state.json'
    a.save_open_state(path)
    saved = json.loads(path.read_text())['saved_at_ms']

    hit_close = saved + M15            # first candle after the save: low goes through SL
    klines = [_kline(saved - M15, 100, 101, 90, 100),   # before the save — ignored
              _kline(hit_close, 100, 101, 94, 96),
              _kline(hit_close + M15, 96, 120, 96, 118)]  # would be a TP later — never reached
    b = _sim(tmp_path)
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
async def test_candles_before_the_save_or_the_open_are_not_replayed(tmp_path):
    a = _sim(tmp_path)
    _open(a)
    path = tmp_path / 'state.json'
    a.save_open_state(path)
    saved = json.loads(path.read_text())['saved_at_ms']
    b = _sim(tmp_path)
    klines = [_kline(saved - 5 * M15, 100, 101, 80, 100), _kline(saved, 100, 101, 80, 100)]
    still_open, closed = await b.restore_open_state(path, lambda s: klines, [SYM])
    assert (still_open, closed) == (1, [])


@pytest.mark.asyncio
async def test_the_forming_candle_is_left_to_ticks(tmp_path):
    a = _sim(tmp_path)
    _open(a)
    path = tmp_path / 'state.json'
    a.save_open_state(path)
    b = _sim(tmp_path)
    future = _now_ms() + 10 * M15
    still_open, closed = await b.restore_open_state(
        path, lambda s: [_kline(future, 100, 101, 80, 90)], [SYM])
    assert (still_open, closed) == (1, [])


@pytest.mark.asyncio
async def test_positions_of_unsubscribed_symbols_are_dropped(tmp_path):
    a = _sim(tmp_path)
    _open(a)
    path = tmp_path / 'state.json'
    a.save_open_state(path)
    b = _sim(tmp_path)
    still_open, closed = await b.restore_open_state(path, lambda s: [], ['ETHUSDT'])
    assert (still_open, closed) == (0, [])
    assert SYM not in b._rank_open[2]


@pytest.mark.asyncio
async def test_missing_or_corrupt_state_is_harmless(tmp_path):
    b = _sim(tmp_path)
    assert await b.restore_open_state(tmp_path / 'nope.json', lambda s: [], [SYM]) == (0, [])
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    assert await b.restore_open_state(bad, lambda s: [], [SYM]) == (0, [])


@pytest.mark.asyncio
async def test_rank_1_keeps_its_position_when_the_best_preset_changes(tmp_path):
    sim = _sim(tmp_path)
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


def test_stop_saves_virtual_positions_instead_of_closing_them():
    stop = MAIN.split('async def on_stop_bot', 1)[1].split('async def _close_virtual_for_switch', 1)[0]
    assert 'save_open_state(_vstate_path)' in stop
    # close_all_open survives only as the fallback when the save itself fails
    assert stop.count('close_all_open') == 1 and 'save failed' in stop


def test_state_is_saved_every_candle_and_restored_at_startup():
    assert MAIN.count('virtual_order_simulator.save_open_state(_vstate_path)') >= 3
    assert 'restore_open_state(' in MAIN
    assert MAIN.index('restore_open_state(') < MAIN.index('async def on_price_update')


def test_a_mode_switch_empties_the_saved_state():
    fn = MAIN.split('async def _close_virtual_for_switch', 1)[1].split('async def ', 1)[0]
    assert 'close_all_open' in fn and 'save_open_state' in fn
    assert 'close_virtual=_close_virtual_for_switch' in MAIN


def test_real_max_age_is_off_by_default_and_closes_as_max_age():
    from config.risk_config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG['real_max_age_candles'] == 0
    assert "risk_cfg.get('real_max_age_candles', 0)" in MAIN
    assert "close_order(symbol, reason='max_age')" in MAIN
