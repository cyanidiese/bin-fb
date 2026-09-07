"""A virtual-only instance must start with no credentials at all.

That is the whole safety argument for running a second instance against the live
market: python-binance refuses private endpoints without a secret, so the mirror is
structurally unable to place an order rather than merely configured not to.

load_settings() contradicted it — it raised "Missing required .env variables:
TESTNET_API_KEY, TESTNET_API_SECRET, SYMBOL" and the mirror crash-looped on first
deploy (2026-09-07 12:08). Requiring credentials from the one instance that must not
have them is the bug; the crash was the symptom.
"""
import pytest

from config.settings import load_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in ('TESTNET_API_KEY', 'TESTNET_API_SECRET', 'API_KEY', 'API_SECRET',
              'VIRTUAL_ONLY', 'TRADING_MODE', 'SYMBOL'):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv('SYMBOL', 'INJUSDT')


def test_virtual_only_starts_without_any_keys(monkeypatch):
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'test')
    s = load_settings()
    assert s.virtual_only is True
    assert s.api_key == ''
    assert s.api_secret == ''


def test_virtual_only_in_live_mode_starts_without_keys(monkeypatch):
    """The dangerous direction: a live-market instance with no way to trade."""
    monkeypatch.setenv('VIRTUAL_ONLY', 'true')
    monkeypatch.setenv('TRADING_MODE', 'live')
    s = load_settings()
    assert s.trading_mode == 'live'
    assert (s.api_key, s.api_secret) == ('', '')


def test_the_trading_bot_still_demands_its_keys(monkeypatch):
    """The check must stay for the instance that actually trades — losing it would let
    the real bot boot keyless and fail at the first order instead of at startup."""
    monkeypatch.setenv('TRADING_MODE', 'test')
    with pytest.raises(RuntimeError, match='TESTNET_API_KEY'):
        load_settings()


def test_the_live_trading_bot_still_demands_its_keys(monkeypatch):
    monkeypatch.setenv('TRADING_MODE', 'live')
    with pytest.raises(RuntimeError, match='API_KEY'):
        load_settings()


def test_symbol_is_still_required_for_everyone(monkeypatch):
    """SYMBOL is not a secret and seeds the registry; keep requiring it so a
    misconfigured deploy fails loudly rather than seeding an empty registry."""
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.delenv('SYMBOL', raising=False)
    with pytest.raises(RuntimeError, match='SYMBOL'):
        load_settings()


def test_keys_present_in_the_environment_are_ignored_when_virtual_only(monkeypatch):
    """Defence in depth: even if the environment leaks real keys in, a virtual-only
    instance must not pick them up and become able to trade."""
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'test')
    monkeypatch.setenv('TESTNET_API_KEY', 'leaked-key')
    monkeypatch.setenv('TESTNET_API_SECRET', 'leaked-secret')
    s = load_settings()
    assert s.api_key == '', 'a leaked key reached a virtual-only instance'
    assert s.api_secret == '', 'a leaked secret reached a virtual-only instance'
