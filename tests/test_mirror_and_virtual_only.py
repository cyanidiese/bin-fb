"""The mirror instance and the virtual-only guarantees that make it safe.

Sections (one class per former test file):
- TestMirrorMode                     — the mirror runs whatever mode the primary is not
- TestVirtualOnlySetting             — the Settings.virtual_only flag
- TestVirtualOnlyNoTelegram          — a statistics-only mirror sends no Telegram
- TestVirtualOnlyControlResources    — single-owner resources belong to the trading bot
- TestVirtualOnlyNeedsNoCredentials  — a virtual-only instance starts with no keys
- TestVirtualOnlySkipsRealOrders     — no trading, no writes to shared state
- TestComposeMirrorHasNoCredentials  — docker-compose keeps the mirror keyless
"""
import dataclasses
import json
import re
from unittest.mock import patch

import pytest

from bot.mode_manager import ModeManager, opposite_mode, read_mode_file
from bot.notifier import Notifier
from config.settings import Settings, load_settings
from tests.factories import src

MAIN_SRC = src('main.py')


# =========================================================================== #
# Mirror mode                                                                 #
# =========================================================================== #

def _mm(tmp_path, mode, mirror):
    mp = tmp_path / 'bot_mode.json'
    if mode is not None:
        mp.write_text(json.dumps({'mode': mode}))
    return ModeManager(mode_path=mp, command_path=tmp_path / 'c.json',
                       result_path=tmp_path / 'r.json', mirror=mirror)


class TestMirrorMode:
    """The mirror instance runs whatever mode the primary is not running.

    Supersedes tests/test_mode_manager_forced_mode.py: the secondary is no longer pinned to
    a statically configured mode, it is the primary's opposite. Two instances in the same
    mode would write the same mode-suffixed files, corrupting the very preset statistics the
    mirror exists to gather — so "never the same mode as the primary" is the invariant these
    tests defend.

    Also covers the mode source-of-truth fix: current_mode named every data file while
    Settings.trading_mode picked the REST/WebSocket endpoints, from two independent sources.
    """

    # ----------------------------------------------------------------------- #
    # opposite_mode                                                            #
    # ----------------------------------------------------------------------- #

    def test_opposite_mode_flips_both_ways(self):
        assert opposite_mode('test') == 'live'
        assert opposite_mode('live') == 'test'

    def test_opposite_mode_defaults_unknown_to_live(self):
        """Fail safe, not fail same.

        The primary defaults to 'test' for anything it cannot read, so 'live' is the only
        answer for an unknown value that cannot leave both instances in the same mode.
        """
        assert opposite_mode('') == 'live'
        assert opposite_mode('nonsense') == 'live'
        assert opposite_mode('testnet') == 'live'

    def test_the_two_instances_can_never_share_a_mode(self):
        """The invariant, stated directly."""
        for primary in ('test', 'live', '', 'garbage'):
            resolved_primary = primary if primary in ('test', 'live') else 'test'
            assert opposite_mode(primary) != resolved_primary, primary

    # ----------------------------------------------------------------------- #
    # read_mode_file                                                           #
    # ----------------------------------------------------------------------- #

    def test_read_mode_file_reads_a_valid_mode(self, tmp_path):
        p = tmp_path / 'bot_mode.json'
        p.write_text(json.dumps({'mode': 'live'}))
        assert read_mode_file(p) == 'live'

    def test_read_mode_file_defaults_to_test(self, tmp_path):
        """Missing, malformed and out-of-vocabulary all mean 'test' — the same default
        ModeManager has always used, so log filenames match current_mode."""
        assert read_mode_file(tmp_path / 'missing.json') == 'test'
        bad = tmp_path / 'bad.json'
        bad.write_text('{not json')
        assert read_mode_file(bad) == 'test'
        weird = tmp_path / 'weird.json'
        weird.write_text(json.dumps({'mode': 'production'}))
        assert read_mode_file(weird) == 'test'

    # ----------------------------------------------------------------------- #
    # ModeManager(mirror=...)                                                  #
    # ----------------------------------------------------------------------- #

    def test_primary_follows_the_file(self, tmp_path):
        """The trading bot must keep reading the file exactly as before."""
        assert _mm(tmp_path, 'live', mirror=False).current_mode == 'live'
        assert _mm(tmp_path, 'test', mirror=False).current_mode == 'test'

    def test_primary_falls_back_to_test_when_the_file_is_absent(self, tmp_path):
        assert _mm(tmp_path, None, mirror=False).current_mode == 'test'

    def test_mirror_takes_the_opposite(self, tmp_path):
        assert _mm(tmp_path, 'live', mirror=True).current_mode == 'test'
        assert _mm(tmp_path, 'test', mirror=True).current_mode == 'live'

    def test_mirror_of_missing_file_is_live(self, tmp_path):
        """No file means the primary defaults to test, so the mirror must be live."""
        assert _mm(tmp_path, None, mirror=True).current_mode == 'live'

    # ----------------------------------------------------------------------- #
    # mirror_target_changed — the restart trigger                              #
    # ----------------------------------------------------------------------- #

    def test_mirror_detects_a_flip(self, tmp_path):
        mm = _mm(tmp_path, 'test', mirror=True)
        assert mm.current_mode == 'live'
        assert mm.mirror_target_changed() is False
        (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'live'}))
        assert mm.mirror_target_changed() is True

    def test_rewriting_the_same_mode_is_not_a_flip(self, tmp_path):
        """The dashboard rewrites the file with a fresh switched_at even when the mode is
        unchanged; that must not restart the mirror."""
        mm = _mm(tmp_path, 'test', mirror=True)
        (tmp_path / 'bot_mode.json').write_text(
            json.dumps({'mode': 'test', 'switched_at': 'later'}))
        assert mm.mirror_target_changed() is False

    def test_primary_never_reports_a_flip(self, tmp_path):
        """Only the mirror restarts on a mode change. The primary must never self-exit."""
        mm = _mm(tmp_path, 'test', mirror=False)
        (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'live'}))
        assert mm.mirror_target_changed() is False

    def test_garbage_file_is_not_a_flip(self, tmp_path):
        """A half-written or corrupt file must not trigger a restart loop."""
        mm = _mm(tmp_path, 'test', mirror=True)
        (tmp_path / 'bot_mode.json').write_text('{not json')
        assert mm.mirror_target_changed() is False

    def test_out_of_vocabulary_mode_is_not_a_flip(self, tmp_path):
        mm = _mm(tmp_path, 'test', mirror=True)
        (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'production'}))
        assert mm.mirror_target_changed() is False

    def test_deleted_file_is_not_a_flip(self, tmp_path):
        """The file is replaced via tmp+rename, so it should never vanish — but if it does,
        exiting on it would be a restart loop with no way out."""
        mm = _mm(tmp_path, 'test', mirror=True)
        (tmp_path / 'bot_mode.json').unlink()
        assert mm.mirror_target_changed() is False

    # ----------------------------------------------------------------------- #
    # One source of truth for mode                                             #
    # ----------------------------------------------------------------------- #

    def test_resolve_mode_stamps_the_resolved_mode_onto_settings(self):
        """The feed must read the market whose name the files carry."""
        import main

        s = load_settings()
        s.trading_mode = 'live'          # pretend TRADING_MODE=live in the environment

        class _MM:
            current_mode = 'test'        # but bot_mode.json says test

        assert main._resolve_mode(s, _MM()) == 'test'
        assert s.trading_mode == 'test', \
            'endpoints would talk to live while filenames claimed test'

    def test_resolve_mode_is_a_noop_when_the_sources_agree(self):
        import main

        s = load_settings()
        s.trading_mode = 'test'

        class _MM:
            current_mode = 'test'

        assert main._resolve_mode(s, _MM()) == 'test'
        assert s.trading_mode == 'test'

    def test_every_per_symbol_settings_object_gets_the_resolved_mode(self):
        """DataFeed is built from a per-symbol Settings, not from _base_settings, so
        stamping only the base object would leave the endpoint wrong."""
        loop = MAIN_SRC[MAIN_SRC.index('    for symbol in symbols:'):]
        loop = loop[:loop.index('\n\n')]
        assert 'trading_mode = current_mode' in loop, \
            'per-symbol Settings must also carry the resolved mode'

    def test_the_mirror_watcher_exists_and_is_gated(self):
        assert 'async def _mirror_watch' in MAIN_SRC
        creation = MAIN_SRC[MAIN_SRC.index('_mirror_task'):]
        creation = creation[:400]
        assert '_virtual_only' in creation, 'only the mirror may self-exit on a mode change'

    def test_primary_rejects_an_unrecognised_mode(self, tmp_path):
        """current_mode names every data file, so an unrecognised value would give the
        primary a set of files nothing else looks for — and would disagree with
        read_mode_file(), which names the log."""
        mm = _mm(tmp_path, 'garbage', mirror=False)
        assert mm.current_mode == 'test'

    def test_both_mode_readers_agree_on_every_input(self, tmp_path):
        """ModeManager._read_mode and read_mode_file must never diverge: one names data
        files, the other names the log file, and they describe the same instance."""
        mp = tmp_path / 'bot_mode.json'
        for raw in ('test', 'live', 'garbage', 'testnet', ''):
            mp.write_text(json.dumps({'mode': raw}))
            primary = ModeManager(mode_path=mp, command_path=tmp_path / 'c.json',
                                  result_path=tmp_path / 'r.json', mirror=False)
            assert primary.current_mode == read_mode_file(mp), raw


# =========================================================================== #
# virtual_only setting                                                        #
# =========================================================================== #

class TestVirtualOnlySetting:
    """virtual_only marks an instance that gathers statistics only.

    No real orders, no private endpoints, no writes to shared config. The live-market
    instance also runs with no credentials, so this flag is a second expression of the
    same guarantee rather than the only one. It must default to False so the existing
    testnet bot is unaffected.
    """

    def test_defaults_to_false(self):
        """The trading bot must be unaffected by the flag's introduction."""
        assert load_settings().virtual_only is False

    def test_is_a_real_settings_field(self):
        assert 'virtual_only' in {f.name for f in dataclasses.fields(Settings)}

    def test_env_enables_it(self, monkeypatch):
        monkeypatch.setenv('VIRTUAL_ONLY', '1')
        assert load_settings().virtual_only is True

    def test_env_accepts_the_usual_truthy_spellings(self, monkeypatch):
        for val in ('1', 'true', 'TRUE', 'yes'):
            monkeypatch.setenv('VIRTUAL_ONLY', val)
            assert load_settings().virtual_only is True, val
        for val in ('0', 'false', 'no', ''):
            monkeypatch.setenv('VIRTUAL_ONLY', val)
            assert load_settings().virtual_only is False, val


# =========================================================================== #
# virtual_only: no Telegram                                                   #
# =========================================================================== #

def _token_arg() -> str:
    """The telegram_token argument, however many lines it spans.

    Captured up to the next argument rather than to end-of-line, so wrapping the
    expression across lines does not silently empty this test.
    """
    m = re.search(r'telegram_token\s*=(.*?)telegram_chat_id\s*=', MAIN_SRC, re.DOTALL)
    assert m, 'telegram_token argument not found'
    return m.group(1)


def _notifier(tmp_path, token, chat_id):
    return Notifier(
        log_path=tmp_path / 'system_log.json',
        alert_path=tmp_path / 'alert_state.json',
        telegram_token=token,
        telegram_chat_id=chat_id,
        min_interval_s=0.0,
    )


class TestVirtualOnlyNoTelegram:
    """A statistics-only mirror instance sends no Telegram at all.

    It has no trades to report, and duplicate ban alerts from two bots are worse than none.
    Notifier guards sending on `if self._token and self._chat_id`, so an empty token disables
    sending while local logging continues.

    Two levels of test: that main.py wires the token conditionally, and that an empty token
    genuinely reaches no network call — the second is the property we actually depend on.
    """

    def test_token_is_blanked_for_virtual_only(self):
        assert 'virtual_only' in _token_arg(), \
            'virtual_only must blank the token so no message is ever sent'

    def test_the_real_token_still_reaches_the_trading_bot(self):
        """The guard must be conditional, not a blanket disable."""
        assert 'token' in _token_arg().replace('telegram_token', '')

    def test_settings_are_loaded_before_the_notifier_is_built(self):
        """The guard reads _base_settings, so it must already exist at that point."""
        assert MAIN_SRC.index('_base_settings = load_settings()') < \
            MAIN_SRC.index('notifier = Notifier('), \
            '_base_settings must be loaded above the Notifier construction'

    def test_empty_token_sends_nothing(self, tmp_path):
        n = _notifier(tmp_path, '', '12345')
        with patch('requests.post') as post:
            n.notify('emergency', 'should not be sent', 'body', 'test')
        assert post.call_count == 0, 'a blank token must not reach the network'

    def test_a_real_token_does_send(self, tmp_path):
        """Proves the previous test measures the token, not some unrelated suppression."""
        n = _notifier(tmp_path, 'tok', '12345')
        with patch('requests.post') as post:
            n.notify('emergency', 'should be sent', 'body', 'test')
        assert post.call_count == 1, 'a configured notifier must still send'


# =========================================================================== #
# virtual_only: control resources                                             #
# =========================================================================== #

def _body(fn: str, chars: int = 500) -> str:
    return MAIN_SRC[MAIN_SRC.index(f'def {fn}'):][:chars]


class TestVirtualOnlyControlResources:
    """Single-owner resources must have exactly one owner: the trading bot.

    bot_pid.json drives the dashboard's Stop button, and mode_manager DELETES a command
    after reading it (mode_manager.py:73). If the virtual instance owns the PID or eats
    the command, pressing Stop reports success while the trading bot keeps running. That
    specific failure is what these tests prevent.

    Guarded inside the writers rather than at each call site: _write_bot_state is called
    from startup, a 10-second heartbeat loop and shutdown, and missing one is the whole
    failure mode.
    """

    def test_pid_write_short_circuits(self):
        """Whoever owns bot_pid.json is who the dashboard Stop button kills."""
        b = _body('_write_pid')
        assert 'VIRTUAL_ONLY' in b or 'virtual_only' in b
        assert 'return' in b

    def test_bot_state_write_short_circuits(self):
        """The 'is the bot alive' indicator must reflect the trading bot, and a
        virtual-only instance would otherwise overwrite it every 10 seconds."""
        b = _body('_write_bot_state')
        assert 'VIRTUAL_ONLY' in b or 'virtual_only' in b
        assert 'return' in b

    def test_command_polling_is_guarded(self):
        """mode_manager deletes the command file after reading it — consume-once. A Stop
        taken by the virtual instance leaves the trading bot running."""
        m = re.search(r'poll_loop\(', MAIN_SRC)
        assert m, 'poll_loop call not found'
        window = MAIN_SRC[max(0, m.start() - 700):m.start()]
        assert 'virtual_only' in window.lower(), 'command polling must be guarded'

    def test_the_flag_is_actually_set_from_settings(self):
        """A module-level flag that nothing assigns would silently guard nothing."""
        assert re.search(r'_VIRTUAL_ONLY\s*=\s*.*virtual_only', MAIN_SRC), \
            'the module flag must be assigned from Settings.virtual_only'

    def test_trading_bot_still_writes_both(self):
        """The guards must be conditional, not a blanket disable."""
        for fn in ('_write_pid', '_write_bot_state'):
            b = _body(fn, 1200)   # wide enough to reach the write past the guard
            assert 'tmp.replace' in b, f'{fn} must still write for the trading bot'
            assert b.index('if _VIRTUAL_ONLY') < b.index('tmp.replace'), \
                f'{fn}: the guard must precede the write, not follow it'


# =========================================================================== #
# virtual_only: needs no credentials                                          #
# =========================================================================== #

class TestVirtualOnlyNeedsNoCredentials:
    """A virtual-only instance must start with no credentials at all.

    That is the whole safety argument for running a second instance against the live
    market: python-binance refuses private endpoints without a secret, so the mirror is
    structurally unable to place an order rather than merely configured not to.

    load_settings() contradicted it — it raised "Missing required .env variables:
    TESTNET_API_KEY, TESTNET_API_SECRET, SYMBOL" and the mirror crash-looped on first
    deploy (2026-09-07 12:08). Requiring credentials from the one instance that must not
    have them is the bug; the crash was the symptom.
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for v in ('TESTNET_API_KEY', 'TESTNET_API_SECRET', 'API_KEY', 'API_SECRET',
                  'VIRTUAL_ONLY', 'TRADING_MODE', 'SYMBOL'):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv('SYMBOL', 'INJUSDT')

    def test_virtual_only_starts_without_any_keys(self, monkeypatch):
        monkeypatch.setenv('VIRTUAL_ONLY', '1')
        monkeypatch.setenv('TRADING_MODE', 'test')
        s = load_settings()
        assert s.virtual_only is True
        assert s.api_key == ''
        assert s.api_secret == ''

    def test_virtual_only_in_live_mode_starts_without_keys(self, monkeypatch):
        """The dangerous direction: a live-market instance with no way to trade."""
        monkeypatch.setenv('VIRTUAL_ONLY', 'true')
        monkeypatch.setenv('TRADING_MODE', 'live')
        s = load_settings()
        assert s.trading_mode == 'live'
        assert (s.api_key, s.api_secret) == ('', '')

    def test_the_trading_bot_still_demands_its_keys(self, monkeypatch):
        """The check must stay for the instance that actually trades — losing it would let
        the real bot boot keyless and fail at the first order instead of at startup."""
        monkeypatch.setenv('TRADING_MODE', 'test')
        with pytest.raises(RuntimeError, match='TESTNET_API_KEY'):
            load_settings()

    def test_the_live_trading_bot_still_demands_its_keys(self, monkeypatch):
        monkeypatch.setenv('TRADING_MODE', 'live')
        with pytest.raises(RuntimeError, match='API_KEY'):
            load_settings()

    def test_symbol_is_still_required_for_everyone(self, monkeypatch):
        """SYMBOL is not a secret and seeds the registry; keep requiring it so a
        misconfigured deploy fails loudly rather than seeding an empty registry."""
        monkeypatch.setenv('VIRTUAL_ONLY', '1')
        monkeypatch.delenv('SYMBOL', raising=False)
        with pytest.raises(RuntimeError, match='SYMBOL'):
            load_settings()

    def test_keys_present_in_the_environment_are_ignored_when_virtual_only(self, monkeypatch):
        """Defence in depth: even if the environment leaks real keys in, a virtual-only
        instance must not pick them up and become able to trade."""
        monkeypatch.setenv('VIRTUAL_ONLY', '1')
        monkeypatch.setenv('TRADING_MODE', 'test')
        monkeypatch.setenv('TESTNET_API_KEY', 'leaked-key')
        monkeypatch.setenv('TESTNET_API_SECRET', 'leaked-secret')
        s = load_settings()
        assert s.api_key == '', 'a leaked key reached a virtual-only instance'
        assert s.api_secret == '', 'a leaked secret reached a virtual-only instance'


# =========================================================================== #
# virtual_only: skips real orders                                             #
# =========================================================================== #

def _guarded(call: str) -> bool:
    """True if every CALL of `call` sits under a virtual_only guard.

    Definitions are skipped — `async def _get_fresh_balance()` contains the same text
    as a call to it, and guarding a definition is not a thing.
    """
    text = MAIN_SRC
    hits = list(re.finditer(re.escape(call), text))
    assert hits, f'{call} not found in main.py — has it been renamed?'
    for m in hits:
        line_start = text.rfind('\n', 0, m.start()) + 1
        if text[line_start:m.start()].lstrip().startswith(('def ', 'async def ')):
            continue
        window = text[max(0, m.start() - 700):m.start()]
        if 'virtual_only' not in window:
            return False
    return True


class TestVirtualOnlySkipsRealOrders:
    """A virtual-only instance must not trade and must not write shared state.

    Each guard protects something specific; the docstrings say what, because a future
    reader deleting one "harmless" guard is exactly how this breaks. Most of these
    protect the TESTNET bot, not the live one.
    """

    def test_leverage_brackets_are_guarded(self):
        """futures_leverage_bracket is private — 401 without credentials."""
        assert _guarded('fetch_leverage_brackets(')

    def test_balance_fetch_is_guarded(self):
        """futures_account is private, and virtual sizing uses rank-pool balances.

        Guarded inside the function rather than at each call site, so callers added later
        are covered automatically.
        """
        body = MAIN_SRC[MAIN_SRC.index('async def _get_fresh_balance'):][:900]
        assert 'virtual_only' in body, 'the balance fetch must short-circuit for virtual_only'
        assert 'return 0.0' in body

    def test_placement_is_guarded(self):
        """The placement pass is skipped by emptying its candidate source, rather than by
        re-indenting 200 lines of allocation logic on the real-money path."""
        m = re.search(r'_placement_symbols\s*=([^\n]+)', MAIN_SRC)
        assert m, 'the placement loop must draw from a gated symbol list'
        assert 'virtual_only' in m.group(1)
        assert 'for sym in _placement_symbols:' in MAIN_SRC

    def test_weight_rebalancer_is_guarded(self):
        """It calls save_risk_config() every candle. Unguarded, the live instance would
        retune the TESTNET bot's real symbol allocation from live virtual results."""
        assert _guarded('weight_rebalancer.on_candle_close(')

    def test_exchange_symbol_check_is_guarded(self):
        """_auto_disable() writes the shared symbol_registry.json, disabling a symbol for
        the testnet bot too."""
        assert _guarded('check_symbols_on_exchange(')

    def test_reconcile_is_guarded(self):
        """Private endpoint at startup, and closing positions the bot does not know about
        is meaningless for an instance that opens none."""
        assert _guarded('reconcile_with_exchange(')

    def test_telegram_menu_is_guarded(self):
        """Telegram delivers each update exactly once. Two pollers on one token means
        commands land on a coin flip — including do_pause/do_resume/do_enable, which
        mutate the shared symbol registry."""
        assert _guarded('telegram_menu.run()')

    def test_flag_only_skips_never_alters(self):
        """Guards must be plain skips. A virtual_only branch that CHANGES an order's size,
        price, side or leverage would put the flag on the real-money path."""
        for m in re.finditer(r'virtual_only', MAIN_SRC):
            line_start = MAIN_SRC.rfind('\n', 0, m.start()) + 1
            line = MAIN_SRC[line_start:MAIN_SRC.find('\n', m.start())]
            assert not re.search(r'(quantity|entry|tp|sl|leverage)\s*=', line), \
                f'virtual_only must not alter order parameters: {line.strip()}'


# =========================================================================== #
# docker-compose: the mirror has no credentials                               #
# =========================================================================== #

def _service(name: str) -> dict:
    """The service's top-level keys, as raw strings. Nested blocks become lists of
    their stripped lines, which is all these assertions need."""
    lines = src('docker-compose.yml').splitlines()
    out: dict = {}
    in_svc = False
    key = None
    for raw in lines:
        if raw.startswith('  ') and not raw.startswith('    ') and raw.rstrip().endswith(':'):
            in_svc = raw.strip() == f'{name}:'
            key = None
            continue
        if not in_svc or not raw.strip() or raw.strip().startswith('#'):
            continue
        if raw.startswith('    ') and not raw.startswith('      '):
            k, _, v = raw.strip().partition(':')
            key = k
            v = v.strip()
            # '>' and '|' are YAML block scalars: the value is on the following,
            # more-indented lines. Treat them like any other nested block.
            out[key] = [] if v in ('', '>', '|', '>-', '|-') else v
        elif key is not None and isinstance(out.get(key), list):
            out[key].append(raw.strip())
    return out


class TestComposeMirrorHasNoCredentials:
    """The mirror container must not be able to trade, and the trading bot must be untouched.

    The keyless property is the core safety argument for running a second instance against
    the live market: python-binance refuses private endpoints without a secret, so the mirror
    is *structurally* unable to place an order rather than merely configured not to. That
    guarantee lives entirely in docker-compose.yml, where a single added `env_file: .env`
    would hand it real credentials — on the live market, with real money.

    Parsed with a small indentation walker rather than PyYAML, which is not a dependency of
    this project and should not become one for a test.
    """

    @pytest.fixture(scope='module')
    def mirror(self):
        s = _service('bot_mirror')
        assert s, 'bot_mirror service not found in docker-compose.yml'
        return s

    @pytest.fixture(scope='module')
    def primary(self):
        s = _service('bot')
        assert s, 'bot service not found in docker-compose.yml'
        return s

    # ----------------------------------------------------------------------- #
    # The mirror cannot trade                                                 #
    # ----------------------------------------------------------------------- #

    def test_mirror_has_no_env_file(self, mirror):
        """env_file: .env would inject the real API keys and make the mirror able to trade
        on the live market. This is the single most dangerous edit to this file."""
        assert 'env_file' not in mirror

    def test_mirror_credentials_are_explicitly_empty(self, mirror):
        """The names must be the ones config/settings.py actually reads.

        The first version of this block set BINANCE_API_KEY / BINANCE_API_SECRET, which
        this project does not use anywhere — so it looked safe while doing nothing, and
        load_settings() then crash-looped the container demanding TESTNET_API_KEY.
        """
        env = mirror['environment']
        for name in ('TESTNET_API_KEY', 'TESTNET_API_SECRET', 'API_KEY', 'API_SECRET'):
            assert f'{name}: ""' in env, f'{name} must be present and empty'
        assert not any('BINANCE_API' in line for line in env), \
            'BINANCE_API_* is not a name this project reads — it protects nothing'

    def test_mirror_gets_symbol_but_nothing_else_from_dotenv(self, mirror):
        """SYMBOL seeds the registry and is not a secret. It must come through compose
        interpolation of exactly one variable, never via env_file, which would inject the
        real API keys alongside it."""
        env = mirror['environment']
        symbol = [l for l in env if l.startswith('SYMBOL:')]
        assert symbol, 'SYMBOL is required by load_settings() and would crash the mirror'
        assert '${SYMBOL' in symbol[0], 'SYMBOL should be interpolated, not duplicated'

    def test_mirror_is_virtual_only(self, mirror):
        assert 'VIRTUAL_ONLY: "1"' in mirror['environment']

    def test_mirror_does_not_pin_a_trading_mode(self, mirror):
        """A mirror derives its mode as the opposite of bot_mode.json. A TRADING_MODE here
        would be ignored by _resolve_mode() and would only mislead a reader."""
        assert not any(l.startswith('TRADING_MODE') for l in mirror['environment'])

    def test_mirror_config_is_read_only(self, mirror):
        ro = [v for v in mirror['volumes'] if v.endswith(':ro')]
        # Legacy risk_config.json and symbol_registry.json, both per-mode risk configs and
        # registries (which one the mirror reads follows bot_mode), and the shared settings
        # and roster — the mirror may write none of them.
        names = ('risk_config.json', 'risk_config_test.json', 'risk_config_live.json',
                 'risk_config_shared.json', 'symbol_registry.json',
                 'symbol_registry_shared.json', 'symbol_registry_test.json',
                 'symbol_registry_live.json')
        assert len(ro) == len(names), f'every risk config and registry must be :ro, got {ro}'
        for name in names:
            assert any(f'/{name}:' in v for v in ro), name
        config_mounts = [v for v in mirror['volumes'] if 'risk_config' in v or 'symbol_registry' in v]
        assert all(v.endswith(':ro') for v in config_mounts), config_mounts

    def test_mirror_restarts_so_a_mode_flip_takes_effect(self, mirror):
        """Load-bearing, not just resilience: on a bot-mode change the mirror exits 0 and
        this policy brings it back up as the new opposite."""
        assert mirror['restart'] == 'unless-stopped'

    # ----------------------------------------------------------------------- #
    # The trading bot is untouched                                            #
    # ----------------------------------------------------------------------- #

    def test_primary_keeps_its_credentials(self, primary):
        assert primary['env_file'] == '.env'

    def test_primary_is_not_virtual_only(self, primary):
        env = primary.get('environment') or []
        assert not any('VIRTUAL_ONLY' in l for l in env), \
            'the trading bot must keep placing real orders'

    def test_primary_config_stays_writable(self, primary):
        """weight_rebalancer writes risk_config every candle and _auto_disable writes the
        registry; a stray :ro here would break the trading bot."""
        assert not [v for v in primary['volumes'] if v.endswith(':ro')]

    def test_the_two_instances_do_not_share_a_log_file(self, mirror, primary):
        def redirect(svc):
            cmd = svc['command']
            return cmd if isinstance(cmd, str) else ' '.join(cmd)
        assert 'bot_mirror_stdout.log' in redirect(mirror)
        assert 'bot_mirror_stdout.log' not in redirect(primary)
