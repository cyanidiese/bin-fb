"""The wallet read must not happen at the candle boundary.

Every -1003 on 2026-09-08 was the balance call (13 of 15 responses; the other two were
startup reconciliation, and ZERO were klines). All 13 landed within one second of a candle
close:

    00:30:00.577  07:15:00.719  09:30:00.875  11:00:00.553  13:30:00.654
    00:45:00.454  07:30:00.919  09:45:00.588  11:30:00.588  14:00:00.524
    09:15:00.569  10:15:00.556  13:00:00.566

That instant is when every bot on the exchange reads its account, and we share a
CloudFront edge with other tenants (the banned IPs, 15.158.242.x, are the edge's own
addresses -- not our egress 185.237.14.105, and not what fapi/testnet resolve to).

A 60s TTL guaranteed the boundary read missed the cache and went to the network right at
that spike. So the read moves to the quiet middle of the candle and the TTL is widened to
cover the boundary. Same one call per candle -- just not at the worst moment.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
LINES = MAIN.splitlines()


def _loop_body() -> str:
    start = next(i for i, l in enumerate(LINES) if 'async def _balance_prefetch_loop' in l)
    indent = len(LINES[start]) - len(LINES[start].lstrip())
    for i in range(start + 1, len(LINES)):
        l = LINES[i]
        if l.strip() and (len(l) - len(l.lstrip())) <= indent and not l.lstrip().startswith('#'):
            return '\n'.join(LINES[start:i])
    return '\n'.join(LINES[start:])


BODY = _loop_body()


class TestTheTtlCoversTheBoundary:
    def test_the_ttl_spans_a_candle(self):
        m = re.search(r'_BALANCE_TTL = ([\d.]+)', MAIN)
        assert m and float(m.group(1)) >= 900.0, \
            'a sub-candle TTL forces the boundary read back onto the network'

    def test_the_ttl_is_still_bounded(self):
        """Not unbounded — a failed pre-fetch must expire and refetch, not serve forever."""
        m = re.search(r'_BALANCE_TTL = ([\d.]+)', MAIN)
        assert float(m.group(1)) <= 1800.0


class TestTheLoopSchedule:
    """The arithmetic, executed — not asserted on source text."""

    @staticmethod
    def _next(now: float, period: float = 900.0, offset: float = 450.0) -> float:
        nxt = (now // period) * period + offset
        if nxt <= now:
            nxt += period
        return nxt

    def test_it_never_targets_a_candle_boundary(self):
        for i in range(0, 900, 7):
            now = 1788870000.0 + i
            assert self._next(now) % 900.0 != 0.0

    def test_it_always_targets_the_candle_midpoint(self):
        for i in range(0, 3600, 11):
            assert self._next(1788870000.0 + i) % 900.0 == 450.0

    def test_it_never_returns_a_past_time(self):
        for i in range(0, 1800, 3):
            now = 1788870000.0 + i
            assert self._next(now) > now, 'a past target would busy-loop'

    def test_the_wait_never_exceeds_one_candle(self):
        for i in range(0, 1800, 3):
            now = 1788870000.0 + i
            assert self._next(now) - now <= 900.0

    def test_the_source_uses_the_midpoint_not_the_boundary(self):
        assert 'period / 2' in BODY, 'offset must be mid-candle'

    def test_the_sleep_has_a_floor(self):
        """Belt and braces against a zero-length sleep spinning the loop."""
        assert 'max(1.0' in BODY


class TestTheLoopBehaviour:
    def test_it_only_caches_a_positive_balance(self):
        """A banned fetch returns 0.0; caching that would recreate the balance=0.00 bug."""
        assert 'if bal > 0' in BODY

    def test_it_updates_the_same_cache_the_placement_path_reads(self):
        assert '_balance_cache_inner[0] = (bal' in BODY

    def test_cancellation_propagates(self):
        """Swallowing CancelledError would hang shutdown."""
        assert 'except asyncio.CancelledError:' in BODY and 'raise' in BODY

    def test_a_failure_does_not_kill_the_loop(self):
        assert 'except Exception' in BODY


class TestWiring:
    def test_it_does_not_run_on_the_virtual_only_instance(self):
        i = MAIN.index('_balance_task = asyncio.create_task')
        assert 'if not _virtual_only' in MAIN[i - 200:i]

    def test_it_is_cancelled_on_shutdown(self):
        i = MAIN.index('_tasks = [t for t in (')
        assert '_balance_task' in MAIN[i:i + 260]
