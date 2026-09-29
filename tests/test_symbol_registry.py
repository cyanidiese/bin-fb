"""SymbolRegistry: disable/pause, per-mode files, atomic persistence, unconfigured symbols.

Sections (one class per former test file):
- TestSymbolRegistryDisable         — disable / re-enable / per-rank disable
- TestSymbolRegistryPause           — pause / resume
- TestSymbolRegistryPerMode         — one shared roster, per-mode decisions
- TestSymbolRegistryAtomic          — survives being read by two processes at once
- TestUnconfiguredSymbolIsVirtualOnly — a symbol nobody gave a weight never trades real money
"""
import inspect
import json
from pathlib import Path

import pytest

from bot.symbol_registry import SymbolRegistry
from tests.factories import src

MAIN_SRC = src('main.py')


def _make_registry(tmp_path, symbols):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "symbols": symbols,
        "weights": {s: 1.0 / len(symbols) for s in symbols},
        "disabled": {},
        "status": {s: {"backtest": "none", "pid": None} for s in symbols},
    }))
    # seed_symbols is ignored when the file already exists
    return SymbolRegistry(seed_symbols=symbols, registry_path=path)


# =========================================================================== #
# Disable                                                                     #
# =========================================================================== #

class TestSymbolRegistryDisable:
    def test_disable_marks_symbol(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        reg.disable("BTCUSDT", reason="not tradeable")
        assert reg.is_disabled("BTCUSDT")
        assert not reg.is_disabled("ETHUSDT")

    def test_disable_redistributes_weight(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        reg.disable("BTCUSDT", reason="test")
        assert abs(reg.get_weight("ETHUSDT") + reg.get_weight("SOLUSDT") - 1.0) < 0.001
        assert reg.get_weight("BTCUSDT") == 0.0

    def test_reenable_restores_equal_split(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
        reg.disable("BTCUSDT", reason="test")
        reg.reenable("BTCUSDT")
        assert not reg.is_disabled("BTCUSDT")
        assert abs(reg.get_weight("BTCUSDT") - 0.5) < 0.001

    def test_all_disabled_returns_true(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
        reg.disable("BTCUSDT", reason="a")
        reg.disable("ETHUSDT", reason="b")
        assert reg.all_disabled()

    def test_disable_rank_and_enable(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT"])
        assert not reg.is_rank_disabled("BTCUSDT", 3)
        reg.disable_rank("BTCUSDT", 3)
        assert reg.is_rank_disabled("BTCUSDT", 3)
        assert not reg.is_rank_disabled("BTCUSDT", 2)
        reg.enable_rank("BTCUSDT", 3)
        assert not reg.is_rank_disabled("BTCUSDT", 3)

    def test_disable_rank_persists(self, tmp_path):
        path = tmp_path / "registry.json"
        symbols = ["BTCUSDT"]
        path.write_text(json.dumps({
            "symbols": symbols,
            "weights": {"BTCUSDT": 1.0},
            "disabled": {},
            "status": {"BTCUSDT": {"backtest": "none", "pid": None}},
        }))
        reg = SymbolRegistry(seed_symbols=symbols, registry_path=path)
        reg.disable_rank("BTCUSDT", 4)

        # Reload from disk
        reg2 = SymbolRegistry(seed_symbols=symbols, registry_path=path)
        assert reg2.is_rank_disabled("BTCUSDT", 4)
        assert not reg2.is_rank_disabled("BTCUSDT", 2)

    def test_enable_rank_cleans_up_empty_entry(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT"])
        reg.disable_rank("BTCUSDT", 2)
        reg.enable_rank("BTCUSDT", 2)
        # Enabling the only disabled rank should remove the symbol key entirely
        data = json.loads((tmp_path / "registry.json").read_text())
        assert "BTCUSDT" not in data.get("disabled_ranks", {})


# =========================================================================== #
# Pause                                                                       #
# =========================================================================== #

class TestSymbolRegistryPause:
    def test_pause_marks_symbol(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
        reg.pause_symbol("BTCUSDT")
        assert reg.is_symbol_paused("BTCUSDT")
        assert not reg.is_symbol_paused("ETHUSDT")

    def test_resume_unmarks_symbol(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
        reg.pause_symbol("BTCUSDT")
        reg.resume_symbol("BTCUSDT")
        assert not reg.is_symbol_paused("BTCUSDT")

    def test_get_paused_symbols_returns_dict(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
        reg.pause_symbol("BTCUSDT")
        paused = reg.get_paused_symbols()
        assert "BTCUSDT" in paused
        assert "paused_at" in paused["BTCUSDT"]

    def test_pause_persists_across_reload(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({
            "symbols": ["BTCUSDT"],
            "weights": {"BTCUSDT": 1.0},
            "disabled": {},
            "status": {"BTCUSDT": {"backtest": "none", "pid": None}},
        }))
        reg = SymbolRegistry(seed_symbols=["BTCUSDT"], registry_path=path)
        reg.pause_symbol("BTCUSDT")

        reg2 = SymbolRegistry(seed_symbols=["BTCUSDT"], registry_path=path)
        assert reg2.is_symbol_paused("BTCUSDT")

    def test_pause_does_not_affect_weight(self, tmp_path):
        reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
        w_before = reg.get_weight("BTCUSDT")
        reg.pause_symbol("BTCUSDT")
        assert reg.get_weight("BTCUSDT") == w_before


# =========================================================================== #
# Per-mode registry                                                           #
# =========================================================================== #

LEGACY = {
    'symbols': ['INJUSDT', 'SOLUSDT', 'WLDUSDT'],
    'status': {'INJUSDT': {'backtest': 'complete', 'pid': None}},
    'weights': {'INJUSDT': 1.0, 'SOLUSDT': 1.0, 'WLDUSDT': 0.0},
    'disabled': {'WLDUSDT': {'reason': 'losses', 'disabled_at': '2026-06-02'}},
    'disabled_ranks': {}, 'paused': {}, 'leverage_overrides': {},
}


def _read_json(root: Path, name: str) -> dict:
    return json.loads((root / name).read_text())


def _mode_registry(root: Path, mode: str, **kw) -> SymbolRegistry:
    return SymbolRegistry(seed_symbols=['BTCUSDT'], mode=mode, root=root, **kw)


class TestSymbolRegistryPerMode:
    """Per-mode symbol registry: one shared roster, per-mode decisions.

    Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md. Disabling a symbol
    in test must not disable it in live; the symbol list is the same for both.
    """

    def test_first_start_seeds_from_the_legacy_file(self, tmp_path):
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        r = _mode_registry(tmp_path, 'test')
        assert r.get_symbols() == LEGACY['symbols']
        assert r.is_disabled('WLDUSDT')
        assert _read_json(tmp_path, 'symbol_registry_shared.json')['symbols'] == LEGACY['symbols']
        assert _read_json(tmp_path, 'symbol_registry_test.json')['disabled'] == LEGACY['disabled']
        assert _read_json(tmp_path, 'symbol_registry.json') == LEGACY          # legacy untouched

    def test_disabling_in_test_leaves_live_enabled(self, tmp_path):
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        test = _mode_registry(tmp_path, 'test')
        live = _mode_registry(tmp_path, 'live')
        test.disable('SOLUSDT', 'manual')
        live.reload_from_disk()
        assert test.is_disabled('SOLUSDT')
        assert not live.is_disabled('SOLUSDT')
        assert 'SOLUSDT' not in _read_json(tmp_path, 'symbol_registry_live.json').get('disabled', {})

    def test_live_without_its_own_file_starts_from_test(self, tmp_path):
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        _mode_registry(tmp_path, 'test').pause_symbol('INJUSDT')
        live = _mode_registry(tmp_path, 'live', read_only=True)       # the mirror
        assert live.is_symbol_paused('INJUSDT')
        assert live.is_disabled('WLDUSDT')
        assert not (tmp_path / 'symbol_registry_live.json').exists()   # :ro — never created

    def test_the_roster_is_shared(self, tmp_path):
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        test = _mode_registry(tmp_path, 'test')
        live = _mode_registry(tmp_path, 'live')
        roster = _read_json(tmp_path, 'symbol_registry_shared.json')
        roster['symbols'].append('DOGEUSDT')                 # the dashboard adds a symbol
        (tmp_path / 'symbol_registry_shared.json').write_text(json.dumps(roster))
        assert test.reload_from_disk() == (['DOGEUSDT'], [])
        assert live.reload_from_disk() == (['DOGEUSDT'], [])

    def test_decisions_never_rewrite_the_roster(self, tmp_path):
        """The dashboard owns `status` (backtest runs); a disable used to rewrite it from the
        bot's stale in-memory copy."""
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        r = _mode_registry(tmp_path, 'test')
        roster = _read_json(tmp_path, 'symbol_registry_shared.json')
        roster['status']['SOLUSDT'] = {'backtest': 'running', 'pid': 42}
        (tmp_path / 'symbol_registry_shared.json').write_text(json.dumps(roster))
        r.disable('SOLUSDT', 'manual')
        r.set_weight('INJUSDT', 2.0)
        r.pause_symbol('INJUSDT')
        assert _read_json(tmp_path, 'symbol_registry_shared.json') == roster

    def test_mirror_writes_nothing(self, tmp_path):
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        _mode_registry(tmp_path, 'test')
        _mode_registry(tmp_path, 'live')
        before = {p.name: p.read_text() for p in tmp_path.glob('*.json')}
        mirror = _mode_registry(tmp_path, 'live', read_only=True)
        mirror.disable('INJUSDT', 'x')
        mirror.pause_symbol('SOLUSDT')
        mirror.add_symbol('DOGEUSDT')
        assert {p.name: p.read_text() for p in tmp_path.glob('*.json')} == before

    def test_unreadable_state_file_is_not_overwritten(self, tmp_path):
        (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
        (tmp_path / 'symbol_registry_test.json').write_text('{"disabled": {tru')   # torn write
        r = _mode_registry(tmp_path, 'test')
        assert r.is_disabled('WLDUSDT')                      # ran from the legacy fallback
        assert (tmp_path / 'symbol_registry_test.json').read_text() == '{"disabled": {tru'

    def test_no_files_at_all_seeds_from_env(self, tmp_path):
        r = _mode_registry(tmp_path, 'test')
        assert r.get_symbols() == ['BTCUSDT']
        assert _read_json(tmp_path, 'symbol_registry_shared.json')['symbols'] == ['BTCUSDT']

    def test_mode_and_registry_path_are_exclusive(self, tmp_path):
        with pytest.raises(ValueError):
            SymbolRegistry(['X'], registry_path=tmp_path / 'r.json', mode='test')
        with pytest.raises(ValueError):
            SymbolRegistry(['X'], mode='paper', root=tmp_path)

    def test_main_builds_the_registry_for_its_mode(self):
        ctor = MAIN_SRC[MAIN_SRC.index('symbol_registry = SymbolRegistry('):]
        ctor = ctor[:ctor.index('\n    )')]
        assert 'mode=_cfg_mode' in ctor
        assert 'read_only=_virtual_only' in ctor


# =========================================================================== #
# Atomic persistence                                                          #
# =========================================================================== #

FULL = {
    'symbols': ['INJUSDT', 'SOLUSDT'],
    'status': {'INJUSDT': {'backtest': 'ok', 'pid': None}},
    'weights': {'INJUSDT': 2.0, 'SOLUSDT': 1.0},
    'disabled': {'SOLUSDT': {'reason': 'losses'}},
    'disabled_ranks': {'INJUSDT': [3]},
    'paused': {'INJUSDT': {'until': 'later'}},
    'leverage_overrides': {'INJUSDT': 7},
}


class TestSymbolRegistryAtomic:
    """The registry must survive being read by two processes at once.

    Three separate failure modes, all reachable today:

    1. `_persist()` used write_text() directly, unlike risk_config's _atomic_write(). With
       two processes on the file a reader can catch it mid-write and get truncated JSON.
    2. `_load()` answers a parse failure by reseeding from the SYMBOL env var and calling
       `_persist()` — a WRITE. So a transient read glitch would overwrite the real registry,
       discarding weights, disabled, paused and leverage_overrides.
    3. On the mirror's read-only config mount that same write raises PermissionError inside
       __init__, killing the instance at startup. The :ro safety measure turned a transient
       glitch into a hard crash.
    """

    # ----------------------------------------------------------------------- #
    # 1. atomic write                                                         #
    # ----------------------------------------------------------------------- #

    def test_persist_goes_through_a_temp_file(self, tmp_path, monkeypatch):
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)

        replaced = []
        real = Path.replace

        def spy(self, target):
            # the temp file must already hold complete JSON before it becomes the real one
            json.loads(self.read_text())
            replaced.append((self.name, Path(target).name))
            return real(self, target)

        monkeypatch.setattr(Path, 'replace', spy)
        r.pause_symbol('INJUSDT')
        assert replaced, 'persist did not use tmp+replace'
        assert replaced[-1][1] == 'symbol_registry.json'

    def test_no_temp_file_is_left_behind(self, tmp_path):
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)
        r.pause_symbol('INJUSDT')
        assert list(tmp_path.glob('*.tmp')) == []

    def test_reader_only_ever_sees_valid_json(self, tmp_path):
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT', 'SOLUSDT'], registry_path=path)
        for i in range(20):
            r.pause_symbol('INJUSDT')
            json.loads(path.read_text())
            r.resume_symbol('INJUSDT')
            json.loads(path.read_text())

    # ----------------------------------------------------------------------- #
    # 2. a corrupt file must not be destroyed                                 #
    # ----------------------------------------------------------------------- #

    def test_corrupt_file_is_not_overwritten(self, tmp_path):
        """Reseeding over an unreadable file loses weights, pauses and leverage
        overrides that a later restart could have recovered."""
        path = tmp_path / 'symbol_registry.json'
        path.write_text('{"symbols": ["INJUSDT"], "weig')      # truncated
        before = path.read_text()
        r = SymbolRegistry(seed_symbols=['SOLUSDT'], registry_path=path)
        assert path.read_text() == before, 'the unreadable registry was overwritten'
        assert r.get_symbols() == ['SOLUSDT'], 'should still run from the seed in memory'

    def test_missing_file_is_still_seeded_and_written(self, tmp_path):
        """Existing behaviour for a genuinely absent file must not change."""
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)
        assert path.exists()
        assert json.loads(path.read_text())['symbols'] == ['INJUSDT']

    def test_a_valid_file_is_loaded_unchanged(self, tmp_path):
        """The primary's normal path: nothing about loading changes."""
        path = tmp_path / 'symbol_registry.json'
        path.write_text(json.dumps(FULL))
        r = SymbolRegistry(seed_symbols=['NOTUSED'], registry_path=path)
        assert r.get_symbols() == ['INJUSDT', 'SOLUSDT']
        assert r.is_disabled('SOLUSDT')
        assert r.is_symbol_paused('INJUSDT')
        assert r.is_rank_disabled('INJUSDT', 3)

    # ----------------------------------------------------------------------- #
    # 3. read-only mount must not crash the instance                          #
    # ----------------------------------------------------------------------- #

    def test_read_only_registry_writes_nothing(self, tmp_path):
        path = tmp_path / 'symbol_registry.json'
        path.write_text(json.dumps(FULL))
        before = path.read_text()
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path, read_only=True)
        r.pause_symbol('INJUSDT')
        r.add_symbol('DOGEUSDT')
        assert path.read_text() == before, 'the mirror wrote to shared config'

    def test_read_only_with_no_file_does_not_crash(self, tmp_path):
        """The seed path calls _persist(); on a :ro mount that used to raise inside
        __init__ and take the instance down at startup."""
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path, read_only=True)
        assert r.get_symbols() == ['INJUSDT']
        assert not path.exists()

    def test_a_failed_write_does_not_raise(self, tmp_path, monkeypatch):
        """Belt and braces: even a writable instance must not die if the disk refuses."""
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)

        def boom(self, target):
            raise PermissionError('read-only file system')

        monkeypatch.setattr(Path, 'replace', boom)
        r.pause_symbol('INJUSDT')          # must not propagate
        assert r.is_symbol_paused('INJUSDT'), 'in-memory state should still be updated'

    def test_default_is_writable(self, tmp_path):
        """read_only must default to False so the trading bot is unaffected."""
        sig = inspect.signature(SymbolRegistry.__init__)
        assert sig.parameters['read_only'].default is False

    def test_main_passes_read_only_from_virtual_only(self):
        ctor = MAIN_SRC[MAIN_SRC.index('SymbolRegistry('):]
        ctor = ctor[:ctor.index(')')]
        assert 'read_only=' in ctor, 'the mirror must not be able to write shared config'

    def test_persist_lands_even_when_rename_is_busy(self, tmp_path, monkeypatch):
        """symbol_registry.json is bind-mounted as a single file, so inside the container
        rename over it fails with EBUSY. The write must still land, or every pause, disable
        and weight change would silently stop persisting."""
        import config.safe_write as sw
        path = tmp_path / 'symbol_registry.json'
        r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)

        def busy(self, target):
            raise OSError(16, 'Device or resource busy')

        monkeypatch.setattr(Path, 'replace', busy)
        sw._warned.clear()
        r.pause_symbol('INJUSDT')
        assert json.loads(path.read_text())['paused'], 'the pause was not persisted'
        assert list(tmp_path.glob('*.tmp')) == []


# =========================================================================== #
# Unconfigured symbol is virtual-only                                         #
# =========================================================================== #

class TestUnconfiguredSymbolIsVirtualOnly:
    """A symbol nobody gave a weight must not trade real money.

    `main.py` read the allocation weight as `risk_cfg.get("symbol_weights", {}).get(sym, 1.0)`,
    so a symbol present in symbol_registry.json but ABSENT from risk_config.symbol_weights
    arrived with weight 1.0 — a full real-order candidate on its first candle.

    That was survivable while adding a symbol required a restart someone would notice. Once
    the roster is hot-reloaded (see test_symbol_hot_subscribe.py) a symbol added by editing
    symbol_registry.json over SSH — without touching risk_config.json — would start taking
    real orders on the next candle with nobody having chosen that.

    Measured 2026-09-09: all 22 symbols in the live registry had an explicit symbol_weights
    entry, so this default was unreachable and flipping it changes nothing today. It is purely
    a guard against the next accident: never trade a symbol nobody configured.

    Weight 0 does NOT stop data collection. `main.py:1667` drops zero-score candidates from
    the real-order path only; virtual simulation still runs for every subscribed symbol,
    including symbols that are fully disabled. Verified on the live bot: all eight disabled
    symbols had recent virtual orders, and WLDUSDT (weight 0, disabled) held 79 open virtual
    positions.
    """

    class TestTheDefaultIsVirtualOnly:
        def test_the_score_multiplier_defaults_to_zero(self):
            assert 'symbol_weights", {}).get(sym, 1.0)' not in MAIN_SRC, \
                'an unconfigured symbol still defaults to a tradeable weight of 1.0'
            assert 'symbol_weights", {}).get(sym, 0.0)' in MAIN_SRC

        def test_the_discard_reason_reads_the_same_default(self):
            """Otherwise the log would claim weight 1.0 while the score was zeroed by 0.0."""
            assert 'symbol_weights", {}).get(_sym, 1.0)' not in MAIN_SRC
            assert 'symbol_weights", {}).get(_sym, 0.0)' in MAIN_SRC

        def test_no_tradeable_default_remains_anywhere(self):
            assert 'symbol_weights' in MAIN_SRC
            for bad in ('.get(sym, 1.0)', '.get(_sym, 1.0)'):
                assert bad not in MAIN_SRC, f'{bad} still defaults an unknown symbol to tradeable'

    class TestTheGateItFeeds:
        def test_a_zero_score_candidate_is_dropped_before_placement(self):
            """The mechanism this default relies on: zero weight zeroes the score, and
            zero-score candidates never reach _try_place_order."""
            assert 'c for c in candidates if c[3] > 0.0' in MAIN_SRC

        def test_the_drop_happens_before_the_sole_candidate_branch(self):
            drop = MAIN_SRC.index('c for c in candidates if c[3] > 0.0')
            sole = MAIN_SRC.index('tats_min_weight: low-weight symbols')
            assert drop < sole, \
                'a weight-0 symbol could reach the full-deployable-budget branch'

    class TestArithmetic:
        """The consequence, stated in numbers so a regression is recognisable."""

        @staticmethod
        def _score(eff, weight):
            return eff * max(0.0, weight)

        def test_an_unconfigured_symbol_scores_zero(self):
            assert self._score(34.29, 0.0) == 0.0

        def test_it_would_have_scored_and_traded_under_the_old_default(self):
            assert self._score(34.29, 1.0) > 0.0

        def test_a_configured_symbol_is_unaffected(self):
            for w in (4, 6, 8, 9, 13, 14):
                assert self._score(34.29, w) > 0.0
