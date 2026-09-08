"""Disabled symbols must persist their WebSocket candles too.

on_candle_close's disabled-symbol branch returned before the append_kline call, so their
on-disk caches never advanced from the stream. Every restart then re-fetched all of them
through load_klines.

Measured on the server 2026-09-08: 65 load_klines REST calls across 8 restarts — about 8
per restart, which is exactly the number of disabled symbols (WLDUSDT, 1000SHIBUSDT,
THETAUSDT, APTUSDT, JUPUSDT, 1000PEPEUSDT, DOGEUSDT, MEMEUSDT). The pattern was visible in
the log: the first restart of a pair fetched ~8, the second a minute later skipped all 16,
because the first restart's fetches had brought those caches current.

Disabled symbols are in the combined stream and their analyzers are updated, so there was
never anything to fetch — only a missing write.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
LINES = MAIN.splitlines()


def _disabled_branch() -> str:
    start = next(i for i, l in enumerate(LINES) if 'if _sym_disabled:' in l)
    for i in range(start + 1, len(LINES)):
        if re.match(r'\s{12}return\s*$', LINES[i]):
            return '\n'.join(LINES[start:i + 1])
    raise AssertionError('disabled branch has no return')


BRANCH = _disabled_branch()


def test_the_branch_appends_the_candle():
    assert 'append_kline' in BRANCH, 'disabled symbols still do not persist their candles'


def test_the_append_happens_before_the_return():
    """After the return it would be dead code — the original bug in mirror image."""
    assert BRANCH.index('append_kline') < BRANCH.rindex('return')


def test_it_stays_off_the_event_loop():
    """Same reason as the enabled path: a 640KB read+parse+write per symbol per candle."""
    i = BRANCH.index('append_kline')
    assert 'to_thread' in BRANCH[max(0, i - 120):i]


def test_a_disk_error_cannot_break_candle_processing():
    i = BRANCH.index('append_kline')
    assert 'try:' in BRANCH[max(0, i - 200):i]
    assert 'except' in BRANCH[i:]


def test_the_virtual_simulation_still_runs_first():
    """Data collection for disabled symbols is the whole reason this branch exists."""
    assert BRANCH.index('on_candle_close') < BRANCH.index('append_kline')


def test_the_enabled_path_still_appends_too():
    assert MAIN.count('feed.append_kline') >= 2
