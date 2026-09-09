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
import json
from pathlib import Path

import pytest

from bot.balance_history import last_known, record

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'main.py').read_text()


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

    def test_a_confirmed_startup_entry_is_trusted(self, tmp_path):
        p = tmp_path / 'bh.json'
        record(p, balance=3098.93, trigger='startup')
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
