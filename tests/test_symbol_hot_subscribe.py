"""Adding a symbol must subscribe it, removing one must unsubscribe it — no restart.

Measured 2026-09-09 20:03. Six symbols were added to symbol_registry.json for virtual-only
observation (LINKUSDT, LTCUSDT, ENAUSDT, EGLDUSDT, ARBUSDT, ETHUSDT). The registry held 22
symbols; the running bot was subscribed to 16 and processed nothing for the new six — no
virtual orders, no rank files, nothing. They had fresh 15m kline caches written by another
process, so it looked like they were live.

Root cause: `reload_from_disk()` re-reads `_disabled`, `_weights`, `_paused`,
`_disabled_ranks` and `_leverage_overrides` — but never `_symbols`. The symbol LIST was
fixed at startup, so a dashboard add was invisible until the next restart.

Three parts have to line up:

1. the registry must notice the list changed (this file, TestReloadPicksUpListChanges)
2. per-symbol state must exist, or `on_candle_close` returns at
   `if symbol not in sym_settings or symbol not in analyzers` and the symbol stays inert
3. the websocket must resubscribe — `stream_combined` already calls `get_symbols()` on
   every reconnect, so it only needs to be asked to reconnect

Safety rules encoded below:

* A symbol holding an OPEN REAL POSITION is never dropped. Unsubscribing it would orphan
  a live position: its exchange stop-loss would survive, but the bot would stop tracking
  the fill, so the close would never be recorded.
* A symbol whose kline bootstrap fails is NOT added. Bootstrap is an API call, and it is
  guarded by the rate-limit guard — during a ban it returns nothing. Half-initialised
  state would make the symbol permanently inert with no retry.
"""
import json

import pytest

from bot.symbol_registry import SymbolRegistry


def _write(path, symbols, **extra):
    d = {'symbols': symbols, 'status': {}}
    d.update(extra)
    path.write_text(json.dumps(d))


# NOTE: seed_symbols is the FIRST positional arg and registry_path the SECOND, so
# SymbolRegistry(path) silently passes the path as the seed list and then reads the
# DEFAULT relative path 'symbol_registry.json' instead. That is how an earlier version
# of this file ended up asserting against the production roster.
@pytest.fixture
def reg_path(tmp_path):
    p = tmp_path / 'symbol_registry.json'
    _write(p, ['SOLUSDT', 'TIAUSDT'])
    return p


class TestReloadPicksUpListChanges:
    def test_an_added_symbol_appears(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        assert r.get_symbols() == ['SOLUSDT', 'TIAUSDT']
        _write(reg_path, ['SOLUSDT', 'TIAUSDT', 'LINKUSDT'])
        r.reload_from_disk()
        assert 'LINKUSDT' in r.get_symbols(), \
            'reload_from_disk never re-read _symbols — this is the reported bug'

    def test_a_removed_symbol_disappears(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        _write(reg_path, ['SOLUSDT'])
        r.reload_from_disk()
        assert r.get_symbols() == ['SOLUSDT']

    def test_it_reports_what_changed(self, reg_path):
        """The caller has to bootstrap the added ones and tear down the removed ones,
        so it needs the delta rather than having to diff the list itself."""
        r = SymbolRegistry([], registry_path=reg_path)
        _write(reg_path, ['SOLUSDT', 'LINKUSDT', 'LTCUSDT'])
        added, removed = r.reload_from_disk()
        assert set(added) == {'LINKUSDT', 'LTCUSDT'}
        assert set(removed) == {'TIAUSDT'}

    def test_no_change_reports_nothing(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        added, removed = r.reload_from_disk()
        assert added == [] and removed == []

    def test_symbols_are_upper_cased(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        _write(reg_path, ['SOLUSDT', 'linkusdt'])
        added, _ = r.reload_from_disk()
        assert added == ['LINKUSDT']
        assert 'LINKUSDT' in r.get_symbols()

    def test_the_six_real_symbols_round_trip(self, reg_path):
        """The exact set added on 2026-09-09."""
        new = ['LINKUSDT', 'LTCUSDT', 'ENAUSDT', 'EGLDUSDT', 'ARBUSDT', 'ETHUSDT']
        r = SymbolRegistry([], registry_path=reg_path)
        _write(reg_path, ['SOLUSDT', 'TIAUSDT'] + new)
        added, removed = r.reload_from_disk()
        assert set(added) == set(new) and removed == []


class TestReloadStaysSafe:
    def test_a_corrupt_file_keeps_the_current_list(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        before = r.get_symbols()
        reg_path.write_text('{not json')
        added, removed = r.reload_from_disk()
        assert r.get_symbols() == before, 'a bad read must not empty the symbol list'
        assert added == [] and removed == []

    def test_a_missing_file_keeps_the_current_list(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        before = r.get_symbols()
        reg_path.unlink()
        added, removed = r.reload_from_disk()
        assert r.get_symbols() == before
        assert added == [] and removed == []

    def test_a_file_with_no_symbols_key_keeps_the_current_list(self, reg_path):
        """Truncating the roster to nothing would unsubscribe everything at once."""
        r = SymbolRegistry([], registry_path=reg_path)
        before = r.get_symbols()
        reg_path.write_text(json.dumps({'status': {}}))
        r.reload_from_disk()
        assert r.get_symbols() == before

    def test_an_empty_symbol_list_is_refused(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        before = r.get_symbols()
        _write(reg_path, [])
        r.reload_from_disk()
        assert r.get_symbols() == before, 'an empty roster must not disable every symbol'

    def test_the_other_reloaded_fields_still_work(self, reg_path):
        """Regression: the list reload must not displace what reload already did."""
        r = SymbolRegistry([], registry_path=reg_path)
        _write(reg_path, ['SOLUSDT', 'TIAUSDT'],
               disabled={'TIAUSDT': {'reason': 'x'}}, weights={'SOLUSDT': 7})
        r.reload_from_disk()
        assert r.is_disabled('TIAUSDT') is True
        assert r.get_weight('SOLUSDT') == 7

    def test_reload_is_idempotent(self, reg_path):
        r = SymbolRegistry([], registry_path=reg_path)
        _write(reg_path, ['SOLUSDT', 'LINKUSDT'])
        first = r.reload_from_disk()
        second = r.reload_from_disk()
        assert set(first[0]) == {'LINKUSDT'}
        assert second == ([], []), 'a second reload re-reported the same change'


class TestTheFeedCanBeAskedToResubscribe:
    def test_request_reconnect_sets_the_flag(self):
        from bot.data_feed import DataFeed
        from config.settings import load_settings
        f = DataFeed(load_settings())
        assert f._reconnect_requested is False
        f.request_reconnect()
        assert f._reconnect_requested is True

    def test_the_stream_reevaluates_the_symbol_list_on_reconnect(self):
        """Already true — stream_combined calls get_symbols() inside its retry loop, so
        a reconnect is all that is needed. Pinned so a refactor cannot hoist it out."""
        import inspect
        from bot.data_feed import DataFeed
        src = inspect.getsource(DataFeed.stream_combined)
        loop = src.index('while True:')
        assert 'get_symbols()' in src[loop:], \
            'get_symbols() moved outside the reconnect loop — adds would need a restart'


class TestMainWiresItUp:
    """Source-level, matching how the rest of this suite pins main.py wiring."""

    @staticmethod
    def _src():
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / 'main.py').read_text()

    def test_the_candle_path_acts_on_the_reload_delta(self):
        s = self._src()
        i = s.index('symbol_registry.reload_from_disk()')
        window = s[i:i + 5000]
        assert 'request_reconnect' in window, \
            'the symbol list can change and the websocket is never told'

    def test_added_symbols_get_settings_and_an_analyzer(self):
        s = self._src()
        i = s.index('symbol_registry.reload_from_disk()')
        window = s[i:i + 5000]
        assert 'sym_settings[' in window and 'analyzers[' in window, \
            'without both, on_candle_close returns early and the symbol stays inert'

    def test_an_open_real_position_blocks_removal(self):
        s = self._src()
        i = s.index('symbol_registry.reload_from_disk()')
        window = s[i:i + 5000]
        assert 'has_open_real' in window or 'open_real' in window, \
            'unsubscribing a symbol with a live position would orphan it'

    def test_a_failed_kline_bootstrap_does_not_add_the_symbol(self):
        s = self._src()
        i = s.index('symbol_registry.reload_from_disk()')
        window = s[i:i + 5000]
        assert 'continue' in window, \
            'a symbol whose klines failed must be retried, not half-added'

    def test_the_sync_runs_once_per_candle_not_once_per_symbol(self):
        """on_candle_close fires per symbol; bootstrapping on every call would re-fetch
        klines up to 22 times a candle."""
        s = self._src()
        i = s.index('symbol_registry.reload_from_disk()')
        window = s[i:i + 5000]
        assert '_symbols_synced_at' in window, 'no once-per-candle guard'
