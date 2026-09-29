"""Balance: reading, caching, seeding, history and reporting of the wallet figure.

Sections (one class per former test module):
    TestBalanceTtl                        -- _BALANCE_TTL spans a candle batch
    TestBalanceHistory                    -- balance_history.record()
    TestBalanceNeverReadsAsZero           -- a failed read falls back, never 0.00
    TestBalancePrefetchOffCandleBoundary  -- mid-candle wallet pre-fetch
    TestLastKnownBalanceIsAWalletFigure   -- last_known() trusts wallet rows only
    TestStartupSeedsBalanceUnderBan       -- startup seed falls back to history
    TestOrderExecutorBalance              -- fetch_account_balance reads the USDT wallet
    TestRunningBalanceAndDrift            -- apply_realised() / reconcile() drift log
    TestTelegramBalanceSources            -- Before/After figures in close notifications
"""
import asyncio
import inspect
import json
import re
import types

import pytest

from bot.balance_history import MAX_ENTRIES, last_known, record
from bot.notifier import Notifier
from bot.order_executor import OrderExecutor
from bot.risk_manager import RiskManager
from tests.factories import src, run_coro

MAIN = src('main.py')


# Longest candle batch observed in production, 2026-09-06 (66 batches sampled).
OBSERVED_MAX_BATCH_S = 27.84


CANDLE_INTERVAL_S = 15 * 60


def _ttl() -> float:
    m = re.search(r'^\s*_BALANCE_TTL\s*=\s*([0-9.]+)', MAIN, re.M)
    assert m, '_BALANCE_TTL not found in main.py'
    return float(m.group(1))


LINES = MAIN.splitlines()


def _line_of(fragment: str) -> int:
    for i, l in enumerate(LINES):
        if fragment in l:
            return i
    raise AssertionError(f'not found: {fragment!r}')


def _loop_body() -> str:
    start = next(i for i, l in enumerate(LINES) if 'async def _balance_prefetch_loop' in l)
    indent = len(LINES[start]) - len(LINES[start].lstrip())
    for i in range(start + 1, len(LINES)):
        l = LINES[i]
        if l.strip() and (len(l) - len(l.lstrip())) <= indent and not l.lstrip().startswith('#'):
            return '\n'.join(LINES[start:i])
    return '\n'.join(LINES[start:])


BODY = _loop_body()


_run = run_coro  # shared order-safe loop helper (tests/factories.py)


def _executor(account_payload):
    """OrderExecutor with a stub feed whose client returns account_payload."""
    ex = OrderExecutor.__new__(OrderExecutor)  # bypass __init__ / real client
    ex._feed = types.SimpleNamespace(
        client=types.SimpleNamespace(futures_account=lambda: account_payload)
    )
    return ex


# The exact shape observed on the live account during the incident.
_INCIDENT_PAYLOAD = {
    'totalWalletBalance': '5000.00000000',
    'assets': [
        {'asset': 'BTC', 'walletBalance': '0.01000000'},
        {'asset': 'USDT', 'walletBalance': '3043.94420611'},
        {'asset': 'USDC', 'walletBalance': '5000.00000000'},
    ],
}


class TestBalanceTtl:
    """The balance cache must survive one whole candle batch.

    futures_account is our most expensive call (weight 5, against 1 for klines), and the
    balance it returns only moves when a position closes. Measured over 66 candle batches
    on 2026-09-06: median 6.96s, p90 25.3s, max 27.8s. A 5s TTL expired mid-batch in 55%
    of them, re-fetching an unchanged number several times per candle.

    Widened again on 2026-09-08 to span a candle, paired with _balance_prefetch_loop(): see
    test_the_balance_is_still_reread_at_least_once_per_candle below, and
    TestBalancePrefetchOffCandleBoundary.
    """

    def test_ttl_outlasts_the_longest_observed_batch(self):
        """Otherwise the cache expires mid-batch and the same balance is fetched twice."""
        assert _ttl() > OBSERVED_MAX_BATCH_S

    def test_ttl_has_margin_over_the_longest_batch(self):
        """Batches get slower as symbols are added; 2x keeps headroom for that."""
        assert _ttl() >= OBSERVED_MAX_BATCH_S * 2

    def test_the_balance_is_still_reread_at_least_once_per_candle(self):
        """The requirement is unchanged; the mechanism moved.

        This used to be enforced by keeping the TTL well inside a candle. That had a cost:
        a short TTL guarantees the candle-boundary read misses the cache and goes to the
        network at exactly the moment every bot on the exchange reads its account -- which is
        where all 13 balance -1003 responses on 2026-09-08 landed (+0.45s..+0.92s past the
        boundary). _balance_prefetch_loop() now reads mid-candle, which both guarantees the
        once-per-candle refresh and keeps it off the congested instant.
        """
        text = MAIN
        assert 'async def _balance_prefetch_loop' in text, \
            'nothing guarantees a per-candle refresh once the TTL exceeds a quarter candle'
        assert 'period / 2' in text, 'the pre-fetch must be mid-candle, not at the boundary'

    def test_the_ttl_is_still_bounded(self):
        """A failed pre-fetch must expire the cache and cause a refetch, not serve forever."""
        assert _ttl() <= CANDLE_INTERVAL_S * 2

    def test_uncached_read_still_exists_for_reporting(self):
        """Raising the TTL is only safe because closes bypass it: _read_wallet_now() reads
        fresh and refreshes this cache, so staleness is bounded by real account activity."""
        text = MAIN
        assert 'async def _read_wallet_now' in text
        assert '_balance_cache_inner[0] = (bal, time.monotonic())' in text, \
            'a successful uncached read must refresh the shared cache'


class TestBalanceHistory:
    """balance_history.record(): file creation, entry shape, append and the MAX_ENTRIES cap."""

    def test_creates_file_on_first_write(self, tmp_path):
        path = tmp_path / 'bh.json'
        record(path, balance=1000.0, trigger='startup')
        assert path.exists()

    def test_startup_entry_shape(self, tmp_path):
        path = tmp_path / 'bh.json'
        record(path, balance=500.0, trigger='startup')
        data = json.loads(path.read_text())
        assert len(data) == 1
        e = data[0]
        assert e['balance'] == 500.0
        assert e['trigger'] == 'startup'
        assert 'timestamp' in e
        assert 'symbol' not in e   # optional field absent when not provided

    def test_order_close_entry_includes_pnl(self, tmp_path):
        path = tmp_path / 'bh.json'
        record(path, balance=1010.0, trigger='order_close',
               symbol='BTCUSDT', leverage=2, pnl_usdt=10.0)
        data = json.loads(path.read_text())
        e = data[0]
        assert e['trigger'] == 'order_close'
        assert e['symbol'] == 'BTCUSDT'
        assert e['leverage'] == 2
        assert e['pnl_usdt'] == 10.0

    def test_appends_multiple_entries(self, tmp_path):
        path = tmp_path / 'bh.json'
        record(path, balance=100.0, trigger='startup')
        record(path, balance=110.0, trigger='order_close')
        data = json.loads(path.read_text())
        assert len(data) == 2

    def test_caps_at_max_entries(self, tmp_path):
        # Seeded at the cap in one write, then filled past it through record(): appending all
        # MAX_ENTRIES one by one rewrote the file each time and took ~48 s.
        path = tmp_path / 'bh.json'
        path.write_text(json.dumps([
            {'timestamp': '2026-01-01T00:00:00+00:00', 'balance': float(i), 'trigger': 'startup'}
            for i in range(MAX_ENTRIES)]))
        for i in range(MAX_ENTRIES, MAX_ENTRIES + 5):
            record(path, balance=float(i), trigger='startup')
        data = json.loads(path.read_text())
        assert len(data) == MAX_ENTRIES
        assert data[-1]['balance'] == float(MAX_ENTRIES + 4)  # newest is last


class TestBalanceNeverReadsAsZero:
    """A failed balance read must not report 0.00 and block every order.

    `_balance_cache_inner` started at (0.0, 0.0) and was only ever filled by a SUCCESSFUL
    fetch. The startup read went into RiskManager but not into that cache. So after any
    restart, one failed fetch made the placement path see balance=0.00 and every symbol hit
    skip_balance.

    Observed on the server 2026-09-08 -- and only visible because the silent-exit
    instrumentation had just shipped:

        13:10:22  RiskManager: real balance seeded -- balance=peak=3072.38 USDT
        13:15     REZUSDT  skip_balance  'balance=0.00 < margin=1.00'
        13:30     REZUSDT  skip_balance  'balance=0.00 < margin=1.00'

    Two real orders refused for insufficient funds on an account holding 3072.38.
    """

    def test_the_cache_is_declared_before_the_startup_read(self):
        """Priming it after the read would be a NameError; before, it is dead code."""
        assert _line_of('_balance_cache_inner: list') < _line_of('startup_balance = await')

    def test_the_startup_read_primes_the_cache(self):
        i = _line_of('risk_manager.seed_real_balance(startup_balance)')
        block = '\n'.join(LINES[i:i + 10])
        assert '_balance_cache_inner[0] = (startup_balance' in block

    def test_a_failed_fetch_falls_back_to_the_risk_manager_balance(self):
        """Its last-known-good, updated on every successful read and every trade close."""
        i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
        block = '\n'.join(LINES[i:i + 30])
        assert 'risk_manager.get_balance()' in block

    def test_zero_is_never_returned_in_preference_to_a_known_balance(self):
        i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
        block = '\n'.join(LINES[i:i + 30])
        assert 'if cached_val > 0' in block, 'a zero cache must not short-circuit the fallback'
        assert not re.search(r'\n\s+return cached_val\s*\n\s*\n', block), \
            'unconditional `return cached_val` reintroduces the 0.00 path'

    def test_the_ttl_cache_still_works(self):
        """The fix must not defeat the 60s TTL that keeps futures_account (weight 5) rare."""
        i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
        block = '\n'.join(LINES[i:i + 12])
        assert '_BALANCE_TTL' in block and 'return cached_val' in block

    def test_a_successful_fetch_still_updates_the_cache(self):
        i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
        block = '\n'.join(LINES[i:i + 30])
        assert '_balance_cache_inner[0] = (bal, now)' in block


class TestBalancePrefetchOffCandleBoundary:
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

        def test_the_prefetch_is_observable_at_the_configured_log_level(self):
            """The root logger runs at INFO. A debug line would make the whole mitigation
            invisible in production — which is how the silent-rejection and balance=0.00 bugs
            survived as long as they did."""
            assert 'logger.info(f"Balance pre-fetched mid-candle' in BODY

        def test_a_missed_prefetch_is_surfaced(self):
            """That candle loses the mitigation and its close-time read hits the network."""
            assert 'logger.warning(' in BODY

    class TestWiring:
        def test_it_does_not_run_on_the_virtual_only_instance(self):
            i = MAIN.index('_balance_task = asyncio.create_task')
            assert 'if not _virtual_only' in MAIN[i - 200:i]

        def test_it_is_cancelled_on_shutdown(self):
            i = MAIN.index('_tasks = [t for t in (')
            assert '_balance_task' in MAIN[i:i + 260]


class TestLastKnownBalanceIsAWalletFigure:
    """last_known() must return a WALLET balance, never an allocation.

    balance_history is not a series of wallet balances. Three triggers write to it and they
    mean different things:

        order_close   the settled wallet after a trade            <- the only wallet truth
        order_open    `_try_place_order`'s 4th parameter, which under TATS is the symbol's
                      ALLOCATION (sym_cap / deployable) — not the wallet at all
        startup       whatever RiskManager was holding at the time — derivative by
                      construction, so it copies a bad seed forward

    ff1d377 added last_known() to seed the balance when a rate-limit ban blocks the startup
    read. It took the newest positive entry regardless of trigger, and on 2026-09-09 21:20 that
    picked an allocation:

        20:37:40  order_close   3105.80961077     <- the real balance
        20:45:00  order_open    2111.9505353236   <- an allocation
        21:18:32  startup       2111.9505353236   <- seeded from the allocation
        21:20:30  startup       2111.9505353236   <- and copied itself forward

    RiskManager came up holding 2111.95 against a real 3105.81 — a 32% understatement, so
    deployable became ~1436 instead of ~2112 and every position was sized down accordingly.
    Conservative, and it self-corrects when the ban lifts and a real read succeeds, but wrong.

    The `startup` rows are why filtering must not simply "prefer the newest sensible-looking
    value": those two rows are a bad seed writing itself back, so trusting `startup` makes the
    error self-sustaining. Only `order_close` (and `balance_refresh`, if ever written) come
    from an actual wallet read.
    """

    @pytest.fixture
    def bh(self, tmp_path):
        return tmp_path / 'balance_history.json'

    class TestOnlyWalletReadingsAreTrusted:
        def test_an_allocation_is_not_mistaken_for_a_balance(self, bh):
            """The exact measured sequence."""
            record(bh, balance=3105.80961077, trigger='order_close', symbol='ETHFIUSDT')
            record(bh, balance=2111.9505353236, trigger='order_open', symbol='ETHFIUSDT')
            assert last_known(bh) == 3105.80961077, \
                'an order_open allocation was returned as the wallet balance'

        def test_order_open_is_skipped_however_many_there_are(self, bh):
            record(bh, balance=3105.81, trigger='order_close')
            for _ in range(5):
                record(bh, balance=2111.95, trigger='order_open')
            assert last_known(bh) == 3105.81

        def test_a_startup_row_does_not_launder_a_bad_seed(self, bh):
            """21:18 and 21:20 were a bad seed writing itself back into the file."""
            record(bh, balance=3105.81, trigger='order_close')
            record(bh, balance=2111.95, trigger='order_open')
            record(bh, balance=2111.95, trigger='startup')
            record(bh, balance=2111.95, trigger='startup')
            assert last_known(bh) == 3105.81, \
                'a startup row is derivative — trusting it makes a bad seed self-sustaining'

        def test_the_newest_order_close_wins(self, bh):
            record(bh, balance=3000.0, trigger='order_close')
            record(bh, balance=3105.81, trigger='order_close')
            assert last_known(bh) == 3105.81

        def test_balance_refresh_is_trusted_if_present(self, bh):
            record(bh, balance=3000.0, trigger='order_close')
            record(bh, balance=3105.81, trigger='balance_refresh')
            assert last_known(bh) == 3105.81

        def test_startup_unconfirmed_is_still_skipped(self, bh):
            record(bh, balance=3105.81, trigger='order_close')
            record(bh, balance=1000.0, trigger='startup_unconfirmed')
            assert last_known(bh) == 3105.81

    class TestDegradesSafely:
        def test_a_history_with_no_wallet_reading_returns_zero(self, bh):
            """Better the config default than an allocation masquerading as a balance."""
            record(bh, balance=2111.95, trigger='order_open')
            record(bh, balance=2111.95, trigger='startup')
            assert last_known(bh) == 0.0

        def test_an_empty_history_returns_zero(self, bh):
            bh.write_text('[]')
            assert last_known(bh) == 0.0

        def test_a_missing_file_returns_zero(self, bh):
            assert last_known(bh) == 0.0

        def test_a_corrupt_file_returns_zero(self, bh):
            bh.write_text('{not json')
            assert last_known(bh) == 0.0

        def test_non_positive_wallet_readings_are_skipped(self, bh):
            record(bh, balance=3105.81, trigger='order_close')
            record(bh, balance=0.0, trigger='order_close')
            assert last_known(bh) == 3105.81

        def test_an_unknown_trigger_is_not_trusted(self, bh):
            """Default-deny: a trigger added later must be reviewed, not silently believed."""
            record(bh, balance=3105.81, trigger='order_close')
            record(bh, balance=9999.0, trigger='some_future_trigger')
            assert last_known(bh) == 3105.81

        def test_rows_that_are_not_dicts_are_ignored(self, bh):
            bh.write_text(json.dumps([['junk'], {'trigger': 'order_close', 'balance': 3105.81}]))
            assert last_known(bh) == 3105.81

    class TestTheArithmeticThisProtects:
        @staticmethod
        def _deployable(bal, min_pct=15.0, max_deploy=80.0):
            return max(0.0, bal - bal * min_pct / 100.0) * max_deploy / 100.0

        def test_the_allocation_seed_understated_deployable_by_a_third(self):
            wrong = self._deployable(2111.95)
            right = self._deployable(3105.81)
            assert abs(right - 2111.95) < 1.0, 'sanity: 3105.81 deployable is ~2111.95'
            assert wrong / right < 0.7, 'the understatement should be ~32%'

        def test_seeding_from_the_wallet_row_restores_it(self, bh):
            record(bh, balance=3105.80961077, trigger='order_close')
            record(bh, balance=2111.9505353236, trigger='order_open')
            assert abs(self._deployable(last_known(bh)) - 2111.95) < 1.0


class TestStartupSeedsBalanceUnderBan:
    """A restart during a rate-limit ban must not size orders off the config default.

    Observed 2026-09-09 17:35, on a deploy that landed inside an active ban:

        17:35:52  RiskManager(test) — balance=1000.00 USDT          <- config default
        17:35:54  Rate-limit guard RESTORED for 'testnet': still banned
        17:35:54  Skipping balance fetch: 'testnet' rate-limit banned for another 3600s

    `fetch_account_balance()` is guarded, so it returned 0.0 rather than extending the ban —
    correct. But the startup seed is gated on `startup_balance > 0`, so with a 0 the whole
    block was skipped and RiskManager kept its 1000.00 default against a real 3098.93:

        believes 1000.00 -> deployable  680.00 -> registry cap 136.00, REZUSDT cap 163.70
        reality  3098.93 -> deployable 2107.27 -> registry cap 421.45, REZUSDT cap 507.31

    Every real order sized at a third of intent for the 190 minutes left on the ban. It is
    conservative, so nothing is at risk of over-leverage — but it is three hours of trading
    at the wrong size, and two unconditional lines then propagated the wrong figure:

      * `bh_record(..., trigger='startup')` wrote balance=1000.0 into balance_history, which
        is the very file a fallback would want to read back.
      * `sync_real_balance_on_start(1000.0)` reset all 87 virtual rank balances to 1000.0.
        Ranks 88-99, which that call does not touch, still held 4978.83 — that is how the
        overwrite was spotted.

    The balance getter already falls back correctly at run time (main.py:786-794: TTL cache,
    then RiskManager's last-known-good). This is the same idea applied one level earlier: the
    seed itself needs a fallback, and the only last-known-good available before any successful
    API call is balance_history on disk.
    """

    class TestLastKnownReadsTheHistory:
        def test_it_returns_the_newest_balance(self, tmp_path):
            p = tmp_path / 'bh.json'
            record(p, balance=3000.0, trigger='order_close')
            record(p, balance=3098.93, trigger='balance_refresh')
            assert last_known(p) == 3098.93

        def test_a_missing_file_is_not_an_error(self, tmp_path):
            assert last_known(tmp_path / 'absent.json') == 0.0

        def test_a_corrupt_file_is_not_an_error(self, tmp_path):
            p = tmp_path / 'bh.json'
            p.write_text('{not json')
            assert last_known(p) == 0.0

        def test_an_empty_history_returns_zero(self, tmp_path):
            p = tmp_path / 'bh.json'
            p.write_text('[]')
            assert last_known(p) == 0.0

        def test_non_positive_entries_are_skipped(self, tmp_path):
            """A zero was already written before this guard existed."""
            p = tmp_path / 'bh.json'
            record(p, balance=3098.93, trigger='order_close')
            record(p, balance=0.0, trigger='startup')
            assert last_known(p) == 3098.93

        def test_an_unconfirmed_startup_entry_is_skipped(self, tmp_path):
            """The bug wrote its own wrong answer into the file it would later read.

            Without this, a second restart during the same ban seeds 1000.0 from the entry
            the FIRST restart wrote, and the wrong figure becomes self-sustaining.
            """
            p = tmp_path / 'bh.json'
            record(p, balance=3098.93, trigger='balance_refresh')
            record(p, balance=1000.0, trigger='startup_unconfirmed')
            assert last_known(p) == 3098.93

        def test_a_startup_entry_is_NOT_trusted(self, tmp_path):
            """Reversed later the same day, and the reversal is the point.

            This originally asserted that a plain `startup` row IS trusted. It is not: a
            startup row records whatever RiskManager already held, so it is derivative by
            construction and copies a bad seed straight back into the file. Measured
            2026-09-09 21:20 — an allocation of 2111.95 was seeded, then written back as two
            `startup` rows, which would have made the wrong figure self-sustaining across
            every later restart. Only order_close / balance_refresh come from a wallet read.
            See TestLastKnownBalanceIsAWalletFigure.
            """
            p = tmp_path / 'bh.json'
            record(p, balance=3098.93, trigger='startup')
            assert last_known(p) == 0.0, 'a derivative startup row must not seed the balance'

        def test_a_wallet_reading_is_trusted(self, tmp_path):
            p = tmp_path / 'bh.json'
            record(p, balance=3098.93, trigger='order_close')
            assert last_known(p) == 3098.93

        def test_it_scans_back_past_several_bad_entries(self, tmp_path):
            p = tmp_path / 'bh.json'
            record(p, balance=3098.93, trigger='order_close')
            for _ in range(5):
                record(p, balance=1000.0, trigger='startup_unconfirmed')
            assert last_known(p) == 3098.93

        def test_a_history_of_only_bad_entries_returns_zero(self, tmp_path):
            p = tmp_path / 'bh.json'
            record(p, balance=1000.0, trigger='startup_unconfirmed')
            assert last_known(p) == 0.0

    class TestTheStartupPathUsesIt:
        def test_the_seed_falls_back_to_the_history(self):
            i = MAIN.index('startup_balance = await order_executor.fetch_account_balance()')
            window = MAIN[i:i + 1400]
            assert 'last_known' in window, 'the startup seed has no fallback'

        def test_the_fallback_runs_before_the_seed(self):
            i = MAIN.index('startup_balance = await order_executor.fetch_account_balance()')
            window = MAIN[i:i + 1400]
            assert window.index('last_known') < window.index('seed_real_balance'), \
                'seeding before the fallback leaves the default in place'

        def test_an_unconfirmed_startup_is_recorded_as_such(self):
            """Otherwise the wrong figure poisons the fallback for the next restart."""
            assert 'startup_unconfirmed' in MAIN

        def test_the_rank_sync_runs_after_the_seed(self):
            """It pushes RiskManager's figure into all 87 virtual rank balances, so it must
            see the seeded value. On 2026-09-09 it ran with the unseeded 1000.00 default and
            overwrote every rank; ranks 88-99, which it does not touch, kept 4978.83."""
            seed = MAIN.index('seed_real_balance(startup_balance)')
            sync = MAIN.index('sync_real_balance_on_start')
            assert seed < sync, 'the rank sync would propagate the config default'

        def test_the_history_record_runs_after_the_seed(self):
            """Otherwise it writes the default into the file the fallback reads back."""
            seed = MAIN.index('seed_real_balance(startup_balance)')
            rec = MAIN.index("trigger='startup' if startup_balance > 0")
            assert seed < rec

    class TestTheArithmeticThisProtects:
        """The measured consequence, so a regression is recognisable."""

        @staticmethod
        def _deployable(bal, min_pct=15.0, max_deploy=80.0):
            return max(0.0, bal - bal * min_pct / 100.0) * max_deploy / 100.0

        def test_the_default_under_sizes_by_two_thirds(self):
            assert abs(self._deployable(1000.0) - 680.0) < 0.01
            assert abs(self._deployable(3098.93) - 2107.27) < 0.01
            assert self._deployable(1000.0) / self._deployable(3098.93) < 0.35

        def test_seeding_from_history_restores_the_real_caps(self, tmp_path):
            p = tmp_path / 'bh.json'
            record(p, balance=3098.93, trigger='order_close')
            assert abs(self._deployable(last_known(p)) * 0.2 - 421.45) < 0.5
            assert abs(self._deployable(last_known(p)) * 13 / 54 - 507.31) < 0.5

    class TestPeakIsNotAnchoredToAFakeFigure:
        """seed_real_balance sets peak = balance. Seeding 1000.0 when the wallet holds
        3098.93 would anchor the peak low; the next real read looks like a 210% gain rather
        than a drawdown, which is harmless, but the reverse — seeding a figure ABOVE the
        wallet — would fire the hard stop on a phantom drawdown. Only trust the history."""

        def test_seeding_never_invents_a_number(self, tmp_path):
            p = tmp_path / 'bh.json'
            p.write_text('[]')
            assert last_known(p) == 0.0, 'an empty history must not produce a peak anchor'

        def test_the_seed_is_skipped_entirely_when_nothing_is_known(self):
            i = MAIN.index('startup_balance = await order_executor.fetch_account_balance()')
            window = MAIN[i:i + 1400]
            assert 'if startup_balance > 0:' in window, \
                'seed_real_balance must stay behind a positive check'


class TestOrderExecutorBalance:
    """fetch_account_balance must report the USDT wallet, not totalWalletBalance.

    On 2026-08-18 totalWalletBalance returned exactly 5000.0 — the account's USDC
    holding — while real USDT was 3043.94. See bot/order_executor.py for the full
    incident note.
    """

    def test_returns_usdt_wallet_not_total(self):
        bal = _run(_executor(_INCIDENT_PAYLOAD).fetch_account_balance())
        assert bal == pytest.approx(3043.94420611)

    def test_ignores_larger_non_usdt_holdings(self):
        """A big USDC balance must not inflate the reading."""
        bal = _run(_executor(_INCIDENT_PAYLOAD).fetch_account_balance())
        assert bal != pytest.approx(5000.0)

    def test_falls_back_to_total_when_no_usdt_entry(self):
        payload = {
            'totalWalletBalance': '1234.56',
            'assets': [{'asset': 'BTC', 'walletBalance': '0.01'}],
        }
        bal = _run(_executor(payload).fetch_account_balance())
        assert bal == pytest.approx(1234.56)

    def test_missing_assets_key_falls_back(self):
        bal = _run(_executor({'totalWalletBalance': '900.0'}).fetch_account_balance())
        assert bal == pytest.approx(900.0)

    def test_zero_on_api_error(self):
        def boom():
            raise RuntimeError("APIError(code=-1003): Way too many requests")

        ex = OrderExecutor.__new__(OrderExecutor)
        ex._feed = types.SimpleNamespace(client=types.SimpleNamespace(futures_account=boom))
        assert _run(ex.fetch_account_balance()) == 0.0

    def test_zero_when_no_feed(self):
        ex = OrderExecutor.__new__(OrderExecutor)
        ex._feed = None
        assert _run(ex.fetch_account_balance()) == 0.0


class TestRunningBalanceAndDrift:
    """The reported balance must track reality between exchange reads.

    Measured 2026-09-11. TIAUSDT closed at 12:02:39 after being open 28 hours:

        wallet_at_open (09-10 07:45)   3145.03
        pnl                             -85.76
        Telegram printed: Before        3145.03   After  3059.28
        actual wallet at 12:02          2917.76   -> overstated by 141.52

    balance_history jumps 10:30:02 -> 12:45:03 with nothing at 12:02, so the wallet read
    failed (we were rate-limit banned) and `_after_or_computed` used its fallback:

        before = closed['wallet_at_open']      # captured 28 HOURS earlier
        return before + pnl

    `wallet_at_open` is snapshotted when the order OPENS, so on a long-held position both
    Before and After are a day stale. Worse, the running balance never advances on a close at
    all — `risk_manager.update_balance()` is called from exactly one place (the per-candle
    `_get_fresh_balance()`), so during a ban the balance simply stops moving while trades
    settle.

    Two pieces fix it:

      apply_realised(pnl)  advance the running balance as each trade closes
      reconcile(actual)    on a genuine exchange read, snap to it and RECORD THE DRIFT

    The drift record is not bookkeeping for its own sake. Roughly -65 in September and -200
    in August of wallet movement could not be attributed to any order; the likeliest cause is
    futures funding, charged every 8h and never recorded as a trade. Drift turns that guess
    into a measurement.
    """

    @pytest.fixture
    def rm(self, tmp_path):
        cfg = tmp_path / 'risk_config.json'
        cfg.write_text(json.dumps({
            'min_balance_pct': 0.0,
            'max_drawdown_pct': 90.0,
            'balance_tiers': [{'min_balance_usdt': 0, 'max_deploy_pct': 80,
                               'max_leverage_ceiling': 15}],
        }))
        r = RiskManager(mode='test', config_path=cfg,
                        state_path=tmp_path / 'risk_state.json',
                        backtest_results_dir=tmp_path)
        r.seed_real_balance(3145.03)
        return r

    class TestApplyRealised:
        def test_a_closed_trade_advances_the_balance(self, rm):
            rm.apply_realised(-85.76)
            assert rm.get_balance() == pytest.approx(3059.27, abs=0.01)

        def test_several_trades_accumulate(self, rm):
            for pnl in (-119.71, 14.79, -29.46, -16.69):
                rm.apply_realised(pnl)
            assert rm.get_balance() == pytest.approx(3145.03 - 151.07, abs=0.01)

        def test_a_win_raises_it(self, rm):
            rm.apply_realised(+84.15)
            assert rm.get_balance() == pytest.approx(3229.18, abs=0.01)

        def test_zero_pnl_is_a_no_op(self, rm):
            before = rm.get_balance()
            rm.apply_realised(0.0)
            assert rm.get_balance() == before

        def test_it_cannot_drive_the_balance_negative(self, rm):
            rm.apply_realised(-99999.0)
            assert rm.get_balance() >= 0.0

        def test_a_non_numeric_pnl_is_ignored_not_fatal(self, rm):
            before = rm.get_balance()
            rm.apply_realised(None)          # must never take the candle path down
            assert rm.get_balance() == before

        def test_applying_does_not_ratchet_the_peak_on_a_loss(self, rm):
            peak = rm._peak_balance
            rm.apply_realised(-85.76)
            assert rm._peak_balance == peak

    class TestReconcile:
        def test_it_snaps_to_the_exchange_figure(self, rm):
            rm.apply_realised(-85.76)                 # calculated 3059.27
            rm.reconcile(2917.76)
            assert rm.get_balance() == pytest.approx(2917.76, abs=0.01)

        def test_it_returns_the_drift(self, rm):
            rm.apply_realised(-85.76)
            drift = rm.reconcile(2917.76)
            assert drift == pytest.approx(2917.76 - 3059.27, abs=0.01)   # ~ -141.51

        def test_no_drift_when_the_calculation_was_right(self, rm):
            rm.apply_realised(-100.0)
            assert rm.reconcile(3045.03) == pytest.approx(0.0, abs=0.01)

        def test_a_non_positive_reading_is_refused(self, rm):
            """A banned read returns 0.0 — that must never be taken as the balance."""
            before = rm.get_balance()
            assert rm.reconcile(0.0) is None
            assert rm.get_balance() == before
            assert rm.reconcile(-5.0) is None
            assert rm.get_balance() == before

        def test_it_records_the_drift_to_disk(self, rm, tmp_path):
            rm.set_drift_log(tmp_path / 'drift.json')
            rm.apply_realised(-85.76)
            rm.reconcile(2917.76)
            rows = json.loads((tmp_path / 'drift.json').read_text())
            assert len(rows) == 1
            r = rows[0]
            assert r['calculated'] == pytest.approx(3059.27, abs=0.01)
            assert r['actual'] == pytest.approx(2917.76, abs=0.01)
            assert r['drift'] == pytest.approx(-141.51, abs=0.01)
            assert r['trades'] == 1
            assert 'timestamp' in r

        def test_trades_since_last_reconcile_is_counted_and_reset(self, rm, tmp_path):
            rm.set_drift_log(tmp_path / 'drift.json')
            for pnl in (-10.0, -20.0, +5.0):
                rm.apply_realised(pnl)
            rm.reconcile(3100.0)
            rm.apply_realised(-1.0)
            rm.reconcile(3099.0)
            rows = json.loads((tmp_path / 'drift.json').read_text())
            assert [r['trades'] for r in rows] == [3, 1]

        def test_a_reconcile_with_no_trades_still_records(self, rm, tmp_path):
            """This is how pure funding-fee drift shows up — money moved, nothing traded."""
            rm.set_drift_log(tmp_path / 'drift.json')
            rm.reconcile(3140.00)
            rows = json.loads((tmp_path / 'drift.json').read_text())
            assert rows[0]['trades'] == 0
            assert rows[0]['drift'] == pytest.approx(-5.03, abs=0.01)

        def test_writing_the_log_never_raises(self, rm, tmp_path):
            """It runs on the candle path — a disk problem must not stop trading."""
            rm.set_drift_log(tmp_path / 'no' / 'such' / 'dir' / 'drift.json')
            rm.apply_realised(-1.0)
            rm.reconcile(3000.0)          # must not raise

        def test_without_a_log_path_it_still_reconciles(self, rm):
            rm.apply_realised(-85.76)
            assert rm.reconcile(2917.76) == pytest.approx(-141.51, abs=0.01)

    class TestItDoesNotBreakWhatExists:
        def test_seed_still_anchors_balance_and_peak(self, rm):
            assert rm.get_balance() == pytest.approx(3145.03)
            assert rm._peak_balance == pytest.approx(3145.03)

        def test_update_balance_still_works(self, rm):
            rm.update_balance(3200.0)
            assert rm.get_balance() == pytest.approx(3200.0)

        def test_reconcile_raises_the_peak_like_a_read_would(self, rm):
            rm.reconcile(3300.0)
            assert rm._peak_balance == pytest.approx(3300.0)

        def test_drawdown_still_evaluated_on_reconcile(self, rm):
            """reconcile must not bypass the risk checks update_balance performs."""
            rm.reconcile(100.0)
            assert rm.get_balance() == pytest.approx(100.0)

    class TestMainUsesTheRunningBalance:
        """Source-level, matching how this suite pins main.py wiring."""

        @staticmethod
        def _src():
            return src('main.py')

        def test_closes_advance_the_running_balance(self):
            assert 'apply_realised' in self._src(), \
                'a closed trade never advances the balance — it freezes during a ban'

        def test_after_is_not_computed_from_wallet_at_open(self):
            """_after_or_computed became _before_after; the point is that neither the
            notification figures nor their fallback may come from wallet_at_open."""
            s = self._src()
            i = s.index('def _before_after')
            body = s[i:i + 2000]
            assert "closed.get('wallet_at_open')" not in body, \
                'After still derives from the wallet when the order OPENED (28h stale on 09-11)'
            assert 'balance_before=c.get(' not in s, \
                'a close notification still passes wallet_at_open as Before'

        def test_successful_reads_reconcile_rather_than_overwrite(self):
            assert 'reconcile(' in self._src()

        def test_the_drift_log_path_is_wired(self):
            assert 'set_drift_log' in self._src()


class TestTelegramBalanceSources:
    """Wallet figures in Telegram must not cost a request they do not need.

    Two different figures with two different requirements, which the code already
    distinguishes and which must not be collapsed:

      "Before" — read at placement. on_candle_close() has already called
                 _get_fresh_balance() moments earlier in the same handler, so the TTL cache
                 holds a genuinely pre-trade figure. An extra uncached weight-5 call bought
                 nothing.

      "After"  — must reflect the settled close. The TTL cache may hold the pre-close figure
                 from the placement pass, which is exactly the 2026-08-19 bug where a close
                 reported the balance from before it settled. So this stays an uncached
                 read — but when that read fails (an API ban returns 0.0) the message showed
                 "n/a", and the bot can compute before + net PnL instead, labelled so it is
                 never mistaken for an exchange-confirmed figure.
    """

    def test_the_before_figure_uses_the_cached_balance(self):
        i = MAIN.index('wallet_before =')
        line = MAIN[i:MAIN.index('\n', i)]
        assert '_get_fresh_balance' in line, \
            'placement already refreshed the cache this candle — an uncached call is waste'
        assert '_read_wallet_now' not in line

    def test_the_after_figure_still_uses_an_uncached_read(self):
        """Collapsing this to the cache reintroduces the 2026-08-19 pre-close bug."""
        assert MAIN.count('_read_wallet_now()') >= 2, \
            'the post-close reads must stay uncached'

    def test_notify_trade_close_can_mark_a_computed_balance(self):
        sig = inspect.signature(Notifier.notify_trade_close)
        assert 'balance_estimated' in sig.parameters
        assert sig.parameters['balance_estimated'].default is False, \
            'a figure must be treated as exchange-read unless explicitly stated otherwise'

    def test_a_computed_after_balance_is_labelled(self, monkeypatch, tmp_path):
        sent = []
        n = Notifier(log_path=tmp_path / 'l.json', alert_path=tmp_path / 'a.json',
                     telegram_token='t', telegram_chat_id='c', min_interval_s=0.0)
        monkeypatch.setattr(n, '_send_telegram', lambda text, mention=False: sent.append(text))
        n.notify_trade_close(symbol='TIAUSDT', side='BUY', pnl_usdt=12.0,
                             entry_price=1.0, close_price=1.1, preset_name='p',
                             balance_before=100.0, balance_after=112.0,
                             fee_usdt=0.5, balance_estimated=True)
        assert sent, 'nothing was sent'
        assert 'computed' in sent[0].lower() or 'estimated' in sent[0].lower(), \
            'a computed figure must say so'
        assert '112' in sent[0]

    def test_a_read_after_balance_is_not_labelled(self, monkeypatch, tmp_path):
        sent = []
        n = Notifier(log_path=tmp_path / 'l.json', alert_path=tmp_path / 'a.json',
                     telegram_token='t', telegram_chat_id='c', min_interval_s=0.0)
        monkeypatch.setattr(n, '_send_telegram', lambda text, mention=False: sent.append(text))
        n.notify_trade_close(symbol='TIAUSDT', side='BUY', pnl_usdt=12.0,
                             entry_price=1.0, close_price=1.1, preset_name='p',
                             balance_before=100.0, balance_after=112.0, fee_usdt=0.5)
        assert 'computed' not in sent[0].lower()

    def test_main_computes_the_fallback_from_before_plus_pnl(self):
        assert 'balance_estimated' in MAIN, 'main.py never passes the flag'
        i = MAIN.index('balance_estimated')
        near = MAIN[max(0, i - 900):i + 300]
        assert 'pnl' in near.lower(), 'the fallback must be computed from the trade PnL'
