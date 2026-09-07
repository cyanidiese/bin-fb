"""Closes that were not strategy exits must be explained, not silently dropped.

A preset showing "3v" with nothing in Wins/Part/Trail/Losses is not a display bug: those
three closes were bookkeeping — the rank table reshuffled, or a restart force-closed them.
The trade count includes them; no outcome column does. The tooltip says which.

A column was rejected in favour of a tooltip: these reasons are diagnostic, and a seventh
numeric column would compete with the outcomes that actually matter.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / 'dashboard/app/trades/page.tsx').read_text()
SIM = (ROOT / 'bot/virtual_order_simulator.py').read_text()

STRATEGY = {'win', 'partial', 'trail', 'loss'}


def _dashboard_labels() -> set:
    """Keys of the BOOKKEEPING_LABELS map in the page."""
    block = PAGE.split('BOOKKEEPING_LABELS: Record<string, string> = {', 1)[1]
    block = block.split('}', 1)[0]
    return set(re.findall(r'^\s*([a-z_]+):', block, re.M))


def _emitted_reasons() -> set:
    """Every close reason the simulator can write, read from the code that writes it."""
    evicted = set(re.findall(r"_evict\([^)]*?'([a-z_]+)'\s*\)", SIM))
    direct = set(re.findall(r"'result':\s*'([a-z_]+)'", SIM))
    return {r for r in evicted | direct if r not in STRATEGY}


def test_the_bot_emits_reasons_the_dashboard_can_name():
    """The whole point is naming them; an unlabelled reason renders as a raw enum."""
    missing = _emitted_reasons() - _dashboard_labels()
    assert not missing, f'no dashboard label for: {sorted(missing)}'


def test_the_known_reasons_are_actually_covered():
    """Guards the regexes above from silently matching nothing and passing."""
    assert {'rank_change', 'closed_early'} <= _emitted_reasons()


def test_strategy_results_are_exactly_the_four_outcomes():
    block = PAGE.split('STRATEGY_RESULTS = [', 1)[1].split(']', 1)[0]
    assert set(re.findall(r"'([a-z]+)'", block)) == STRATEGY


def test_counting_excludes_strategy_results():
    """Otherwise wins would be double-reported as bookkeeping."""
    assert 'STRATEGY_RESULTS.includes(r)' in PAGE


def test_the_count_is_derived_not_enumerated():
    """A reason added to the bot must still appear, labelled or not."""
    assert 'bookkeeping[r] = (bookkeeping[r] ?? 0) + 1' in PAGE


def test_the_trades_cell_carries_the_tooltip():
    i = PAGE.index('{tradesLabel}')
    assert 'title={bookTip}' in PAGE[i - 500:i]


def test_rows_without_bookkeeping_get_no_tooltip():
    """An empty title renders an empty grey box on hover."""
    fn = PAGE.split('function bookkeepingTooltip', 1)[1].split('\nfunction ', 1)[0]
    assert 'return undefined' in fn
