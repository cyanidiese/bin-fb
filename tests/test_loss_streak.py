"""
Unit tests for the loss-streak directional cooldown logic.

The state management lives inside main.run() closures, so we test the logic
directly by replicating the exact dict operations used in _update_loss_streak
and the gate check in _try_place_order.
"""

import pytest

_TF_MS = 15 * 60 * 1000  # 15-minute candle in ms


def _update(
    c: dict,
    ts: int,
    loss_streak: dict,
    streak_blocked: dict,
    global_pause_until: dict,
    last_loss_ts: dict,
    loss_streak_max: int,
    cooldown_candles: int,
    global_pause_trigger: int,
    global_pause_candles: int,
) -> None:
    """Mirror of main._update_loss_streak for isolated testing."""
    sym = c['symbol']
    pname = c.get('preset_name', 'default')
    side = c.get('side', '')
    if loss_streak_max <= 0:
        return
    sk = f"{sym}:{pname}:{side}"
    other_sk = f"{sym}:{pname}:{'SELL' if side == 'BUY' else 'BUY'}"
    if c.get('result') == 'loss':
        cnt = loss_streak.get(sk, 0) + 1
        last_loss_ts[sk] = ts
        if cnt >= loss_streak_max:
            streak_blocked[sk] = ts + cooldown_candles * _TF_MS
            loss_streak[sk] = 0
        else:
            loss_streak[sk] = cnt
        if global_pause_trigger > 0:
            other_ts = last_loss_ts.get(other_sk, 0)
            if other_ts > 0 and (ts - other_ts) <= global_pause_trigger * _TF_MS:
                pk = f"{sym}:{pname}"
                global_pause_until[pk] = ts + global_pause_candles * _TF_MS
    else:
        loss_streak[sk] = 0


def _blocked(
    sym: str,
    pname: str,
    side: str,
    candle_ts: int,
    streak_blocked: dict,
    global_pause_until: dict,
    loss_streak_max: int,
) -> bool:
    """Mirror of the gate check in _try_place_order."""
    if loss_streak_max <= 0:
        return False
    pk = f"{sym}:{pname}"
    if global_pause_until.get(pk, 0) >= candle_ts:
        return True
    sk = f"{sym}:{pname}:{side}"
    if streak_blocked.get(sk, 0) >= candle_ts:
        return True
    return False


def make_close(sym, pname, side, result):
    return {'symbol': sym, 'preset_name': pname, 'side': side, 'result': result}


# ── Basic streak counting ─────────────────────────────────────────────────────

def test_first_loss_does_not_block():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
    assert not _blocked('BTCUSDT', 'p', 'BUY', ts + _TF_MS, sb, gp, 2)


def test_second_loss_blocks():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
    # blocked for 5 candles after the 2nd loss
    assert _blocked('BTCUSDT', 'p', 'BUY', ts + _TF_MS, sb, gp, 2)
    assert _blocked('BTCUSDT', 'p', 'BUY', ts + 4 * _TF_MS, sb, gp, 2)


def test_cooldown_expires_after_n_candles():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
    # 5 candles after last loss = block expires
    expire_ts = ts + _TF_MS + 5 * _TF_MS
    assert not _blocked('BTCUSDT', 'p', 'BUY', expire_ts + 1, sb, gp, 2)


def test_win_resets_streak():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
    _update(make_close('BTCUSDT', 'p', 'BUY', 'trail'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
    # Win resets streak; another loss needed before block
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + 2 * _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
    assert not _blocked('BTCUSDT', 'p', 'BUY', ts + 3 * _TF_MS, sb, gp, 2)


def test_sell_side_not_blocked_by_buy_streak():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    # BUY loses twice → BUY blocked
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
    # SELL side should still be free
    assert not _blocked('BTCUSDT', 'p', 'SELL', ts + _TF_MS, sb, gp, 2)


# ── Global pause ──────────────────────────────────────────────────────────────

def test_global_pause_triggers_when_both_sides_lose_close_together():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    # BUY loss, then SELL loss 2 candles later (trigger=3)
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 3, 10)
    _update(make_close('BTCUSDT', 'p', 'SELL', 'loss'), ts + 2 * _TF_MS, ls, sb, gp, lt, 2, 5, 3, 10)
    # Both sides should be globally paused for 10 candles
    assert _blocked('BTCUSDT', 'p', 'BUY', ts + 3 * _TF_MS, sb, gp, 2)
    assert _blocked('BTCUSDT', 'p', 'SELL', ts + 3 * _TF_MS, sb, gp, 2)


def test_global_pause_does_not_trigger_if_sides_too_far_apart():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    # BUY loss, then SELL loss 5 candles later (trigger=3 → too far)
    _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 3, 10)
    _update(make_close('BTCUSDT', 'p', 'SELL', 'loss'), ts + 5 * _TF_MS, ls, sb, gp, lt, 2, 5, 3, 10)
    assert not gp  # no global pause triggered


def test_zero_loss_streak_max_disables_feature():
    ls, sb, gp, lt = {}, {}, {}, {}
    ts = 1_000_000
    for _ in range(10):
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 0, 5, 0, 0)
        ts += _TF_MS
    assert not _blocked('BTCUSDT', 'p', 'BUY', ts, sb, gp, 0)
