"""The mirror container must not be able to trade, and the trading bot must be untouched.

The keyless property is the core safety argument for running a second instance against
the live market: python-binance refuses private endpoints without a secret, so the mirror
is *structurally* unable to place an order rather than merely configured not to. That
guarantee lives entirely in docker-compose.yml, where a single added `env_file: .env`
would hand it real credentials — on the live market, with real money.

Parsed with a small indentation walker rather than PyYAML, which is not a dependency of
this project and should not become one for a test.
"""
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[1] / 'docker-compose.yml'


def _service(name: str) -> dict:
    """The service's top-level keys, as raw strings. Nested blocks become lists of
    their stripped lines, which is all these assertions need."""
    lines = COMPOSE.read_text().splitlines()
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


@pytest.fixture(scope='module')
def mirror():
    s = _service('bot_mirror')
    assert s, 'bot_mirror service not found in docker-compose.yml'
    return s


@pytest.fixture(scope='module')
def primary():
    s = _service('bot')
    assert s, 'bot service not found in docker-compose.yml'
    return s


# --------------------------------------------------------------------------- #
# The mirror cannot trade                                                     #
# --------------------------------------------------------------------------- #

def test_mirror_has_no_env_file(mirror):
    """env_file: .env would inject the real API keys and make the mirror able to trade
    on the live market. This is the single most dangerous edit to this file."""
    assert 'env_file' not in mirror


def test_mirror_credentials_are_explicitly_empty(mirror):
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


def test_mirror_gets_symbol_but_nothing_else_from_dotenv(mirror):
    """SYMBOL seeds the registry and is not a secret. It must come through compose
    interpolation of exactly one variable, never via env_file, which would inject the
    real API keys alongside it."""
    env = mirror['environment']
    symbol = [l for l in env if l.startswith('SYMBOL:')]
    assert symbol, 'SYMBOL is required by load_settings() and would crash the mirror'
    assert '${SYMBOL' in symbol[0], 'SYMBOL should be interpolated, not duplicated'


def test_mirror_is_virtual_only(mirror):
    assert 'VIRTUAL_ONLY: "1"' in mirror['environment']


def test_mirror_does_not_pin_a_trading_mode(mirror):
    """A mirror derives its mode as the opposite of bot_mode.json. A TRADING_MODE here
    would be ignored by _resolve_mode() and would only mislead a reader."""
    assert not any(l.startswith('TRADING_MODE') for l in mirror['environment'])


def test_mirror_config_is_read_only(mirror):
    ro = [v for v in mirror['volumes'] if v.endswith(':ro')]
    assert len(ro) == 2, f'risk_config and symbol_registry must both be :ro, got {ro}'
    assert any('risk_config.json' in v for v in ro)
    assert any('symbol_registry.json' in v for v in ro)


def test_mirror_restarts_so_a_mode_flip_takes_effect(mirror):
    """Load-bearing, not just resilience: on a bot-mode change the mirror exits 0 and
    this policy brings it back up as the new opposite."""
    assert mirror['restart'] == 'unless-stopped'


# --------------------------------------------------------------------------- #
# The trading bot is untouched                                                #
# --------------------------------------------------------------------------- #

def test_primary_keeps_its_credentials(primary):
    assert primary['env_file'] == '.env'


def test_primary_is_not_virtual_only(primary):
    env = primary.get('environment') or []
    assert not any('VIRTUAL_ONLY' in l for l in env), \
        'the trading bot must keep placing real orders'


def test_primary_config_stays_writable(primary):
    """weight_rebalancer writes risk_config every candle and _auto_disable writes the
    registry; a stray :ro here would break the trading bot."""
    assert not [v for v in primary['volumes'] if v.endswith(':ro')]


def test_the_two_instances_do_not_share_a_log_file(mirror, primary):
    def redirect(svc):
        cmd = svc['command']
        return cmd if isinstance(cmd, str) else ' '.join(cmd)
    assert 'bot_mirror_stdout.log' in redirect(mirror)
    assert 'bot_mirror_stdout.log' not in redirect(primary)
