"""Trimming the decision log must not throw away the real-order records.

`placed` rows are what every profitability analysis reads; skips are far more numerous and
individually far less valuable. A plain tail-trim discards exactly the rows worth keeping.

Measured on the server 2026-09-08: adding reasons to the 17 silent rejection paths pushed
the 5,000-row cap harder, and within four hours 15 of 79 'placed' rows had been evicted --
the log's window shrank from 27 days (Aug 12) to 18 (Aug 21). The instrumentation was
worth having; losing order history to it was not.
"""
from bot.decision_log import _trim, MAX_ENTRIES, MAX_PLACED


def _rows(n_skip: int, n_placed: int) -> list:
    """Interleaved so a tail-trim would drop the early placed rows."""
    out = []
    for i in range(n_placed):
        out.append({'decision': 'placed', 'i': i})
        out.extend({'decision': 'skip_zero_score', 'i': f'{i}.{j}'}
                   for j in range(n_skip // max(1, n_placed)))
    return out


def test_a_plain_tail_trim_would_lose_placed_rows():
    """Establishes the premise — without it the tests below prove nothing."""
    rows = _rows(6000, 40)
    tail = rows[-MAX_ENTRIES:]
    assert sum(1 for e in tail if e['decision'] == 'placed') < 40


def test_trim_keeps_every_placed_row():
    rows = _rows(6000, 40)
    kept = _trim(rows)
    assert sum(1 for e in kept if e['decision'] == 'placed') == 40


def test_trim_respects_the_row_cap():
    """The file must not grow — the per-decision read-modify-write cost depends on it."""
    kept = _trim(_rows(20000, 50))
    assert len(kept) <= MAX_ENTRIES


def test_trim_preserves_chronological_order():
    rows = _rows(6000, 30)
    kept = _trim(rows)
    idx = [rows.index(e) for e in kept]
    assert idx == sorted(idx)


def test_trim_keeps_the_newest_skips():
    """Old skips are the right thing to drop; recent ones still diagnose today."""
    rows = _rows(6000, 10)
    kept = _trim(rows)
    assert kept[-1] is rows[-1]


def test_max_placed_is_a_floor_not_a_ceiling():
    """A mostly-'placed' log must still fill to MAX_ENTRIES.

    My first version treated MAX_PLACED as a hard cap, which shrank an all-'placed' log
    from 5,000 rows to 1,004 — throwing away 4,000 rows with room to spare. The existing
    test_decision_log.py::test_caps_at_max_entries caught it.
    """
    rows = [{'decision': 'placed', 'i': i} for i in range(MAX_ENTRIES + 500)]
    kept = _trim(rows)
    assert len(kept) == MAX_ENTRIES
    assert kept[-1] is rows[-1]                      # newest retained


def test_the_file_stays_bounded_whatever_the_mix():
    for n_skip, n_placed in ((20000, 50), (0, MAX_ENTRIES + 900), (9000, 3000)):
        kept = _trim(_rows(n_skip, n_placed) if n_skip else
                     [{'decision': 'placed', 'i': i} for i in range(n_placed)])
        assert len(kept) <= MAX_ENTRIES, f'{n_skip}/{n_placed} -> {len(kept)}'


def test_a_log_under_the_cap_is_untouched():
    """_trim is only called over the cap, but it must be safe if that ever changes."""
    rows = _rows(100, 5)
    assert len(rows) < MAX_ENTRIES          # premise
    assert _trim(rows) == rows
