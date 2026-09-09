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
import json
import time

import pytest

from bot.balance_history import last_known, record


@pytest.fixture
def bh(tmp_path):
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
