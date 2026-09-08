"""Every real-order rejection must record a reason.

17 of the 27 exits in _try_place_order returned silently, so a weighted, signalling
symbol could produce nothing all day with nothing in the decision log saying why.
Measured on the server: 118 floor_sl_pct events with no follow-up decision, and
ETHFIUSDT/REZUSDT logging "Using manually locked preset" and then vanishing while
holding 20.3% and 12.5% of allocated capital.

decision_log.record()'s own docstring already listed 'skip_already_open' and
'skip_no_signal' as expected values -- they were never wired up.

This is observability only: _skip() returns 0.0, exactly what the silent paths returned.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
LINES = MAIN.splitlines()


def _fn_body(name: str) -> list:
    start = next(i for i, l in enumerate(LINES) if f'def {name}' in l)
    indent = len(LINES[start]) - len(LINES[start].lstrip())
    for i in range(start + 1, len(LINES)):
        l = LINES[i]
        if l.strip() and (len(l) - len(l.lstrip())) <= indent and re.match(r'\s*(async )?def ', l):
            return LINES[start:i]
    return LINES[start:]


BODY = _fn_body('_try_place_order')


def test_the_helper_exists():
    assert any('def _skip(' in l for l in BODY)


def test_the_helper_returns_zero_so_behaviour_is_unchanged():
    """The silent paths returned 0.0. If _skip returned anything else this would be a
    behaviour change disguised as logging."""
    i = next(i for i, l in enumerate(BODY) if 'def _skip(' in l)
    assert any(l.strip() == 'return 0.0' for l in BODY[i:i + 12])


def test_no_rejection_returns_without_recording_a_reason():
    offenders = []
    for i, l in enumerate(BODY):
        if not re.match(r'\s*return\b', l):
            continue
        if '_skip(' in l or 'trade_margin' in l:
            continue
        window = '\n'.join(BODY[max(0, i - 14):i + 1])
        if 'dl_record' not in window and 'def _skip' not in l:
            offenders.append((i, l.strip()))
    assert not offenders, f'silent rejections: {offenders}'


def test_the_locked_preset_no_signal_case_is_named():
    """The most valuable one: the order path re-runs the engine under the locked preset's
    own settings, and a locked symbol producing no signal there is why it never trades."""
    assert "'skip_no_signal'" in MAIN


def test_the_docstring_values_are_now_wired():
    for d in ("'skip_already_open'", "'skip_no_signal'"):
        assert d in MAIN, f'{d} still unused'


def test_reasons_are_distinct_enough_to_diagnose():
    """A single generic reason would defeat the point."""
    decisions = set(re.findall(r"_skip\(\s*'([a-z_]+)'", MAIN))
    assert len(decisions) >= 12, f'only {len(decisions)} distinct: {sorted(decisions)}'


def test_the_helper_passes_the_real_balance_through():
    """Recording balance=0 for every skip would make the log useless for sizing questions."""
    i = next(i for i, l in enumerate(BODY) if 'def _skip(' in l)
    blk = '\n'.join(BODY[i:i + 12])
    assert 'balance=balance' in blk
