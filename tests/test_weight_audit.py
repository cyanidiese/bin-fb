"""`symbol_weights` changes must leave a trace.

Weights are the biggest lever on real-order sizing — weight 0 is a hard gate on real
orders, and under TATS the weights split the deployable budget. They can be changed from
the dashboard, by hand over SSH, by `weight_rebalancer`, or by editing `risk_config.json`
directly, and none of those left any record.

What that cost, 2026-09-11: ETHFIUSDT went 9 -> 3 and SOLUSDT 8 -> 3. With nothing
recorded, the change was attributed to `weight_rebalancer` — which was disabled
(`enabled: false`, zero log lines). The edits had been made by hand. A whole line of
investigation was spent on a question one log line answers.

So this is a detector, not a hook on the writers: it diffs the live config against the
last snapshot, which means a manual edit or a future code path cannot bypass it.
"""
import json

import pytest

from bot.weight_audit import MAX_CHANGES, audit, history


@pytest.fixture
def store(tmp_path):
    return tmp_path / 'weight_changes_test.json'


class TestFirstRun:
    def test_it_records_nothing_on_a_fresh_file(self, store):
        """No prior state means no change — inventing 0 -> 9 for every symbol would
        bury the real edits that follow."""
        assert audit(store, {'REZUSDT': 14, 'INJUSDT': 9}) == []
        assert history(store) == []

    def test_it_still_stores_the_snapshot(self, store):
        audit(store, {'REZUSDT': 14})
        assert json.loads(store.read_text())['snapshot'] == {'REZUSDT': 14.0}


class TestDetectingChanges:
    def test_the_measured_case(self, store):
        """ETHFIUSDT 9 -> 3 and SOLUSDT 8 -> 3, the edit that was mis-attributed."""
        audit(store, {'ETHFIUSDT': 9, 'SOLUSDT': 8, 'REZUSDT': 14})
        changes = audit(store, {'ETHFIUSDT': 3, 'SOLUSDT': 3, 'REZUSDT': 14})
        got = {c['symbol']: (c['old'], c['new']) for c in changes}
        assert got == {'ETHFIUSDT': (9.0, 3.0), 'SOLUSDT': (8.0, 3.0)}

    def test_an_unchanged_symbol_is_not_reported(self, store):
        audit(store, {'REZUSDT': 14, 'INJUSDT': 9})
        changes = audit(store, {'REZUSDT': 14, 'INJUSDT': 8})
        assert [c['symbol'] for c in changes] == ['INJUSDT']

    def test_no_change_writes_nothing(self, store):
        audit(store, {'REZUSDT': 14})
        assert audit(store, {'REZUSDT': 14}) == []
        assert history(store) == []

    def test_a_new_symbol_reports_old_as_none(self, store):
        audit(store, {'REZUSDT': 14})
        changes = audit(store, {'REZUSDT': 14, 'ENAUSDT': 5})
        assert changes[0]['symbol'] == 'ENAUSDT'
        assert changes[0]['old'] is None and changes[0]['new'] == 5.0

    def test_a_removed_symbol_is_reported(self, store):
        """Dropping a symbol from the config stops its real orders exactly as
        setting it to 0 does, so it must not vanish silently."""
        audit(store, {'REZUSDT': 14, 'AVAXUSDT': 3})
        changes = audit(store, {'REZUSDT': 14})
        assert changes[0]['symbol'] == 'AVAXUSDT'
        assert changes[0]['old'] == 3.0 and changes[0]['new'] is None

    def test_the_zero_gate_is_recorded(self, store):
        """weight 0 is a hard gate on real orders — the most important edit of all."""
        audit(store, {'EIGENUSDT': 8})
        changes = audit(store, {'EIGENUSDT': 0})
        assert changes[0]['old'] == 8.0 and changes[0]['new'] == 0.0

    def test_int_to_float_is_not_a_change(self, store):
        """The dashboard writes ints, hand edits write floats. 9 -> 9.0 is noise."""
        audit(store, {'INJUSDT': 9})
        assert audit(store, {'INJUSDT': 9.0}) == []

    def test_the_source_is_recorded(self, store):
        audit(store, {'INJUSDT': 9})
        changes = audit(store, {'INJUSDT': 3}, source='dashboard')
        assert changes[0]['source'] == 'dashboard'

    def test_every_change_is_timestamped(self, store):
        audit(store, {'INJUSDT': 9})
        changes = audit(store, {'INJUSDT': 3})
        assert 'timestamp' in changes[0]


class TestHistory:
    def test_it_accumulates_across_edits(self, store):
        audit(store, {'INJUSDT': 9})
        audit(store, {'INJUSDT': 5})
        audit(store, {'INJUSDT': 2})
        assert [(c['old'], c['new']) for c in history(store)] == [(9.0, 5.0), (5.0, 2.0)]

    def test_it_can_be_scoped_to_one_symbol(self, store):
        audit(store, {'INJUSDT': 9, 'REZUSDT': 14})
        audit(store, {'INJUSDT': 5, 'REZUSDT': 10})
        assert [c['symbol'] for c in history(store, 'INJUSDT')] == ['INJUSDT']

    def test_it_is_capped(self, store):
        audit(store, {'X': 0})
        for i in range(1, MAX_CHANGES + 50):
            audit(store, {'X': i})
        assert len(history(store)) == MAX_CHANGES

    def test_the_cap_keeps_the_newest(self, store):
        audit(store, {'X': 0})
        for i in range(1, MAX_CHANGES + 50):
            audit(store, {'X': i})
        assert history(store)[-1]['new'] == float(MAX_CHANGES + 49)


class TestItNeverBreaksTrading:
    def test_a_corrupt_store_does_not_raise(self, store):
        store.write_text('{not json')
        assert audit(store, {'INJUSDT': 9}) == []

    def test_an_unwritable_path_does_not_raise(self, tmp_path):
        audit(tmp_path / 'no' / 'such' / 'dir' / 'w.json', {'INJUSDT': 9})

    def test_junk_weights_are_skipped_not_fatal(self, store):
        audit(store, {'INJUSDT': 9})
        changes = audit(store, {'INJUSDT': 9, 'BAD': 'not-a-number'})
        assert changes == []

    def test_empty_weights_are_handled(self, store):
        assert audit(store, {}) == []

    def test_history_of_a_missing_file_is_empty(self, tmp_path):
        assert history(tmp_path / 'absent.json') == []


class TestItIsWiredIntoTheCandlePath:
    """A detector only works if it actually runs on every config reload."""

    @staticmethod
    def _main():
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / 'main.py').read_text()

    def test_main_audits_the_weights(self):
        assert 'weight_audit' in self._main(), \
            'weight changes are not audited — a manual edit leaves no trace again'
