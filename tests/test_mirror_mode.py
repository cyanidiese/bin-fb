"""The mirror instance runs whatever mode the primary is not running.

Supersedes tests/test_mode_manager_forced_mode.py: the secondary is no longer pinned to
a statically configured mode, it is the primary's opposite. Two instances in the same
mode would write the same mode-suffixed files, corrupting the very preset statistics the
mirror exists to gather — so "never the same mode as the primary" is the invariant these
tests defend.

Also covers the mode source-of-truth fix: current_mode named every data file while
Settings.trading_mode picked the REST/WebSocket endpoints, from two independent sources.
"""
import json
from pathlib import Path

from bot.mode_manager import ModeManager, opposite_mode, read_mode_file

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


# --------------------------------------------------------------------------- #
# opposite_mode                                                                #
# --------------------------------------------------------------------------- #

def test_opposite_mode_flips_both_ways():
    assert opposite_mode('test') == 'live'
    assert opposite_mode('live') == 'test'


def test_opposite_mode_defaults_unknown_to_live():
    """Fail safe, not fail same.

    The primary defaults to 'test' for anything it cannot read, so 'live' is the only
    answer for an unknown value that cannot leave both instances in the same mode.
    """
    assert opposite_mode('') == 'live'
    assert opposite_mode('nonsense') == 'live'
    assert opposite_mode('testnet') == 'live'


def test_the_two_instances_can_never_share_a_mode():
    """The invariant, stated directly."""
    for primary in ('test', 'live', '', 'garbage'):
        resolved_primary = primary if primary in ('test', 'live') else 'test'
        assert opposite_mode(primary) != resolved_primary, primary


# --------------------------------------------------------------------------- #
# read_mode_file                                                               #
# --------------------------------------------------------------------------- #

def test_read_mode_file_reads_a_valid_mode(tmp_path):
    p = tmp_path / 'bot_mode.json'
    p.write_text(json.dumps({'mode': 'live'}))
    assert read_mode_file(p) == 'live'


def test_read_mode_file_defaults_to_test(tmp_path):
    """Missing, malformed and out-of-vocabulary all mean 'test' — the same default
    ModeManager has always used, so log filenames match current_mode."""
    assert read_mode_file(tmp_path / 'missing.json') == 'test'
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    assert read_mode_file(bad) == 'test'
    weird = tmp_path / 'weird.json'
    weird.write_text(json.dumps({'mode': 'production'}))
    assert read_mode_file(weird) == 'test'


# --------------------------------------------------------------------------- #
# ModeManager(mirror=...)                                                      #
# --------------------------------------------------------------------------- #

def _mm(tmp_path, mode, mirror):
    mp = tmp_path / 'bot_mode.json'
    if mode is not None:
        mp.write_text(json.dumps({'mode': mode}))
    return ModeManager(mode_path=mp, command_path=tmp_path / 'c.json',
                       result_path=tmp_path / 'r.json', mirror=mirror)


def test_primary_follows_the_file(tmp_path):
    """The trading bot must keep reading the file exactly as before."""
    assert _mm(tmp_path, 'live', mirror=False).current_mode == 'live'
    assert _mm(tmp_path, 'test', mirror=False).current_mode == 'test'


def test_primary_falls_back_to_test_when_the_file_is_absent(tmp_path):
    assert _mm(tmp_path, None, mirror=False).current_mode == 'test'


def test_mirror_takes_the_opposite(tmp_path):
    assert _mm(tmp_path, 'live', mirror=True).current_mode == 'test'
    assert _mm(tmp_path, 'test', mirror=True).current_mode == 'live'


def test_mirror_of_missing_file_is_live(tmp_path):
    """No file means the primary defaults to test, so the mirror must be live."""
    assert _mm(tmp_path, None, mirror=True).current_mode == 'live'


# --------------------------------------------------------------------------- #
# mirror_target_changed — the restart trigger                                  #
# --------------------------------------------------------------------------- #

def test_mirror_detects_a_flip(tmp_path):
    mm = _mm(tmp_path, 'test', mirror=True)
    assert mm.current_mode == 'live'
    assert mm.mirror_target_changed() is False
    (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'live'}))
    assert mm.mirror_target_changed() is True


def test_rewriting_the_same_mode_is_not_a_flip(tmp_path):
    """The dashboard rewrites the file with a fresh switched_at even when the mode is
    unchanged; that must not restart the mirror."""
    mm = _mm(tmp_path, 'test', mirror=True)
    (tmp_path / 'bot_mode.json').write_text(
        json.dumps({'mode': 'test', 'switched_at': 'later'}))
    assert mm.mirror_target_changed() is False


def test_primary_never_reports_a_flip(tmp_path):
    """Only the mirror restarts on a mode change. The primary must never self-exit."""
    mm = _mm(tmp_path, 'test', mirror=False)
    (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'live'}))
    assert mm.mirror_target_changed() is False


def test_garbage_file_is_not_a_flip(tmp_path):
    """A half-written or corrupt file must not trigger a restart loop."""
    mm = _mm(tmp_path, 'test', mirror=True)
    (tmp_path / 'bot_mode.json').write_text('{not json')
    assert mm.mirror_target_changed() is False


def test_out_of_vocabulary_mode_is_not_a_flip(tmp_path):
    mm = _mm(tmp_path, 'test', mirror=True)
    (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'production'}))
    assert mm.mirror_target_changed() is False


def test_deleted_file_is_not_a_flip(tmp_path):
    """The file is replaced via tmp+rename, so it should never vanish — but if it does,
    exiting on it would be a restart loop with no way out."""
    mm = _mm(tmp_path, 'test', mirror=True)
    (tmp_path / 'bot_mode.json').unlink()
    assert mm.mirror_target_changed() is False


# --------------------------------------------------------------------------- #
# One source of truth for mode                                                 #
# --------------------------------------------------------------------------- #

def test_resolve_mode_stamps_the_resolved_mode_onto_settings():
    """The feed must read the market whose name the files carry."""
    import main
    from config.settings import load_settings

    s = load_settings()
    s.trading_mode = 'live'          # pretend TRADING_MODE=live in the environment

    class _MM:
        current_mode = 'test'        # but bot_mode.json says test

    assert main._resolve_mode(s, _MM()) == 'test'
    assert s.trading_mode == 'test', \
        'endpoints would talk to live while filenames claimed test'


def test_resolve_mode_is_a_noop_when_the_sources_agree():
    import main
    from config.settings import load_settings

    s = load_settings()
    s.trading_mode = 'test'

    class _MM:
        current_mode = 'test'

    assert main._resolve_mode(s, _MM()) == 'test'
    assert s.trading_mode == 'test'


def test_every_per_symbol_settings_object_gets_the_resolved_mode():
    """DataFeed is built from a per-symbol Settings, not from _base_settings, so
    stamping only the base object would leave the endpoint wrong."""
    src = MAIN.read_text()
    loop = src[src.index('    for symbol in symbols:'):]
    loop = loop[:loop.index('\n\n')]
    assert 'trading_mode = current_mode' in loop, \
        'per-symbol Settings must also carry the resolved mode'


def test_the_mirror_watcher_exists_and_is_gated():
    src = MAIN.read_text()
    assert 'async def _mirror_watch' in src
    creation = src[src.index('_mirror_task'):]
    creation = creation[:400]
    assert '_virtual_only' in creation, 'only the mirror may self-exit on a mode change'


def test_primary_rejects_an_unrecognised_mode(tmp_path):
    """current_mode names every data file, so an unrecognised value would give the
    primary a set of files nothing else looks for — and would disagree with
    read_mode_file(), which names the log."""
    mm = _mm(tmp_path, 'garbage', mirror=False)
    assert mm.current_mode == 'test'


def test_both_mode_readers_agree_on_every_input(tmp_path):
    """ModeManager._read_mode and read_mode_file must never diverge: one names data
    files, the other names the log file, and they describe the same instance."""
    mp = tmp_path / 'bot_mode.json'
    for raw in ('test', 'live', 'garbage', 'testnet', ''):
        mp.write_text(json.dumps({'mode': raw}))
        primary = ModeManager(mode_path=mp, command_path=tmp_path / 'c.json',
                              result_path=tmp_path / 'r.json', mirror=False)
        assert primary.current_mode == read_mode_file(mp), raw
