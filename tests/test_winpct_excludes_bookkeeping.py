"""Win rate must be a share of decided outcomes, not of every close.

The denominator was realCount + virtualCount, which counts bookkeeping closes -- a
reshuffle or a restart force-close. A preset with 40 reshuffles and 10 real exits read
about 4x worse than it performed, and unevenly: reshuffle counts vary hugely by rank, so
two presets with identical strategy records could show very different win rates.

Display only -- Python's preset_efficiency drives selection -- but it is the number a
human reads when deciding what to lock.
"""
import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / 'dashboard/app/trades/page.tsx').read_text()


def _winpct_line() -> str:
    for ln in PAGE.splitlines():
        if 'const winPct' in ln:
            return ln
    raise AssertionError('winPct is gone')


def test_the_denominator_is_decided_outcomes():
    assert 'decided' in _winpct_line()


def test_the_denominator_is_not_the_raw_trade_count():
    """totalTrades includes bookkeeping closes."""
    assert 'totalTrades' not in _winpct_line()


def test_decided_sums_exactly_the_four_strategy_outcomes():
    line = next(ln for ln in PAGE.splitlines() if 'const decided' in ln)
    assert set(re.findall(r'\b(wins|partials|trails|losses)\b', line)) == {
        'wins', 'partials', 'trails', 'losses'}


def test_the_numerator_still_excludes_losses():
    m = re.search(r'const winPct\s*=.*?\(\((.*?)\)\s*/', _winpct_line())
    assert m and 'losses' not in m.group(1)


def test_no_division_by_zero():
    """A preset with only bookkeeping closes has no win rate to show."""
    assert 'decided > 0' in _winpct_line()


def test_the_trade_count_column_is_unchanged():
    """Only the rate is corrected; the count still shows every close, with the tooltip."""
    assert 'const totalTrades  = realCount + virtualCount' in PAGE
