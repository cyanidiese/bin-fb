"""Coordinated mode switch: close everything, prove flat, restart; the mirror follows the
primary's RUNNING mode. Spec: docs/specs/2026-09-26-mode-switch-restart-and-per-mode-backtests.md
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

from bot.mode_manager import ModeManager, instance_mode, read_primary_running_mode
from bot.mode_switch import close_out

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


# --------------------------------------------------------------------------- #
# close_out                                                                   #
# --------------------------------------------------------------------------- #

class Exchange:
    """Fake: positions on the exchange; close_real clears only what the bot tracks."""

    def __init__(self, tracked, untracked=(), check_fails=False, stuck=()):
        self.tracked = set(tracked)
        self.untracked = set(untracked)
        self.stuck = set(stuck)            # never close
        self.check_fails = check_fails
        self.calls = []

    async def close_virtual(self):
        self.calls.append('virtual')

    async def close_real(self):
        self.calls.append('real')
        self.tracked -= (self.tracked - self.stuck)

    async def open_on_exchange(self):
        self.calls.append('check')
        if self.check_fails:
            return None
        return sorted(self.tracked | self.untracked)

    async def close_untracked(self):
        self.calls.append('untracked')
        self.untracked -= (self.untracked - self.stuck)

    async def sleep(self, _s):
        self.calls.append('sleep')


def run(ex, keys=True):
    return asyncio.run(close_out(
        'live', keys_present=lambda m: keys, close_virtual=ex.close_virtual,
        close_real=ex.close_real, open_on_exchange=ex.open_on_exchange,
        close_untracked=ex.close_untracked, sleep=ex.sleep))


def test_refuses_without_keys_and_closes_nothing():
    ex = Exchange(tracked={'SOLUSDT'})
    r = run(ex, keys=False)
    assert r.outcome == 'refused'
    assert ex.calls == [] and ex.tracked == {'SOLUSDT'}


def test_flat_after_closing_everything():
    ex = Exchange(tracked={'SOLUSDT', 'INJUSDT'})
    r = run(ex)
    assert r.outcome == 'flat'
    assert ex.calls[:3] == ['virtual', 'real', 'check']


def test_untracked_leftovers_are_closed_before_declaring_flat():
    """close_all_orders_at_market forgets an order even when its close failed."""
    ex = Exchange(tracked={'SOLUSDT'}, untracked={'INJUSDT'})
    r = run(ex)
    assert r.outcome == 'flat'
    assert 'untracked' in ex.calls


def test_postponed_when_a_position_will_not_close():
    ex = Exchange(tracked={'SOLUSDT'}, stuck={'SOLUSDT'})
    r = run(ex)
    assert r.outcome == 'postponed' and r.still_open == ['SOLUSDT']
    assert ex.calls.count('real') == 3


def test_postponed_when_the_exchange_cannot_be_asked():
    """No proof of flat → never exit (a rate-limit ban answers None)."""
    ex = Exchange(tracked=set(), check_fails=True)
    r = run(ex)
    assert r.outcome == 'postponed' and r.still_open is None


def test_a_failing_market_close_does_not_crash_the_sequence():
    ex = Exchange(tracked={'SOLUSDT'})

    async def boom():
        raise RuntimeError('-1003 banned')
    r = asyncio.run(close_out(
        'live', keys_present=lambda m: True, close_virtual=ex.close_virtual,
        close_real=boom, open_on_exchange=ex.open_on_exchange,
        close_untracked=ex.close_untracked, sleep=ex.sleep))
    assert r.outcome in ('flat', 'postponed')     # SOL untracked-closed or still reported


# --------------------------------------------------------------------------- #
# ModeManager: requested vs running                                           #
# --------------------------------------------------------------------------- #

def mm(tmp_path, mirror=False):
    return ModeManager(mode_path=tmp_path / 'bot_mode.json',
                       command_path=tmp_path / 'cmd.json', result_path=tmp_path / 'res.json',
                       mirror=mirror, primary_path=tmp_path / 'primary_mode.json')


def write(p: Path, mode):
    p.write_text(json.dumps({'mode': mode}))


def test_primary_sees_a_requested_change(tmp_path):
    write(tmp_path / 'bot_mode.json', 'test')
    m = mm(tmp_path)
    assert m.requested_mode_change() is None
    write(tmp_path / 'bot_mode.json', 'live')
    assert m.requested_mode_change() == 'live'


def test_primary_ignores_torn_or_unknown_requests(tmp_path):
    write(tmp_path / 'bot_mode.json', 'test')
    m = mm(tmp_path)
    (tmp_path / 'bot_mode.json').write_text('{"mode": "li')
    assert m.requested_mode_change() is None
    write(tmp_path / 'bot_mode.json', 'paper')
    assert m.requested_mode_change() is None


def test_mirror_follows_the_running_primary_not_the_request(tmp_path):
    write(tmp_path / 'bot_mode.json', 'test')
    write(tmp_path / 'primary_mode.json', 'test')
    mirror = mm(tmp_path, mirror=True)
    assert mirror.current_mode == 'live'
    write(tmp_path / 'bot_mode.json', 'live')          # button pressed, primary still closing
    assert mirror.mirror_target_changed() is False     # used to restart into 'test' here
    write(tmp_path / 'primary_mode.json', 'live')      # primary restarted in live
    assert mirror.mirror_target_changed() is True


def test_mirror_falls_back_to_bot_mode_before_any_primary_wrote_it(tmp_path):
    write(tmp_path / 'bot_mode.json', 'test')
    mirror = mm(tmp_path, mirror=True)
    assert mirror.current_mode == 'live'
    write(tmp_path / 'bot_mode.json', 'live')
    assert mirror.mirror_target_changed() is True


def test_primary_records_its_running_mode_and_mirror_never_writes(tmp_path):
    write(tmp_path / 'bot_mode.json', 'live')
    mm(tmp_path).write_primary_mode('t0')
    assert json.loads((tmp_path / 'primary_mode.json').read_text())['mode'] == 'live'
    write(tmp_path / 'primary_mode.json', 'test')
    mm(tmp_path, mirror=True).write_primary_mode('t1')
    assert json.loads((tmp_path / 'primary_mode.json').read_text())['mode'] == 'test'


def test_instance_mode(tmp_path):
    bm, pm = tmp_path / 'bot_mode.json', tmp_path / 'primary_mode.json'
    write(bm, 'live')
    write(pm, 'test')
    assert instance_mode(False, pm, bm) == 'live'      # primary: the request
    assert instance_mode(True, pm, bm) == 'live'       # mirror: opposite of running 'test'
    assert read_primary_running_mode(pm, bm) == 'test'


# --------------------------------------------------------------------------- #
# main.py wiring                                                              #
# --------------------------------------------------------------------------- #

SRC = MAIN.read_text()


def test_pending_switch_stops_new_real_orders():
    block = SRC[SRC.index('_placement_symbols = [] if _virtual_only'):][:400]
    assert 'if _switch_pending[0] is not None:' in block
    assert '_placement_symbols = []' in block.split('if _switch_pending[0] is not None:')[1]


def test_only_the_primary_runs_the_switch_watch():
    block = SRC[SRC.index('_switch_task = None'):][:400]
    assert 'if _virtual_only:' in block and '_primary_mode_watch()' in block.split('else:')[1]


def test_exit_happens_only_after_a_flat_close_out():
    watch = SRC[SRC.index('async def _primary_mode_watch'):]
    watch = watch[:watch.index('sys.exit(0)')]
    assert "result.outcome == 'refused'" in watch and "result.outcome == 'postponed'" in watch
    # both non-flat outcomes loop back before the exit
    assert watch.count('continue') >= 4


def test_primary_writes_its_running_mode_at_startup():
    assert 'mode_manager.write_primary_mode(started_at)' in SRC


def test_in_place_switch_removed():
    assert 'async def on_switch_mode' not in SRC
    assert 'on_switch_mode=' not in SRC


def test_rate_limit_state_is_keyed_by_mode():
    assert 'f"rate_limit_state_{current_mode}.json"' in SRC
