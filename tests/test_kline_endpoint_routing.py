"""Kline reads go to production while trading stays on testnet.

Testnet bans aggressively: measured 2026-09-06 it returned HTTP 418 while production
served the same request at used-weight 1/2400, costing ~27% of that day. Klines are
public data and the two endpoints agree closely (EIGENUSDT 15m closes within ±0.09%),
so routing public reads to production keeps the fragile testnet quota for the account
calls that actually require it.
"""
import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from config.settings import load_settings
from bot.data_feed import DataFeed


def _feed(mode: str, live_klines: bool) -> DataFeed:
    """Build a DataFeed with a fresh mock per Client() call.

    A bare patch() hands back the same return_value every time, which would make
    two distinct clients look identical and quietly pass the assertions below.
    """
    s = dataclasses.replace(load_settings(), trading_mode=mode,
                            api_key='k', api_secret='s')
    with patch('bot.data_feed.Client', side_effect=lambda *a, **k: MagicMock()):
        return DataFeed(s, live_klines=live_klines)


def test_testnet_with_live_klines_uses_a_separate_production_client():
    f = _feed('test', live_klines=True)
    assert f._klines_source == 'production'
    assert f._klines_client is not f._client, 'klines must not share the testnet client'


def test_testnet_without_live_klines_keeps_the_old_behaviour():
    f = _feed('test', live_klines=False)
    assert f._klines_source == 'testnet'
    assert f._klines_client is f._client


def test_live_mode_is_a_no_op():
    """In live mode both endpoints are production already — the flag changes nothing."""
    for flag in (True, False):
        f = _feed('live', live_klines=flag)
        assert f._klines_source == 'production'
        assert f._klines_client is f._client, 'live mode must not open a second client'


def test_trading_client_stays_on_testnet_when_klines_are_production():
    """The whole point: only public reads move. Orders and balance stay on testnet."""
    f = _feed('test', live_klines=True)
    assert f._is_testnet is True
    assert f._klines_client is not f._client


def test_reinit_falls_back_to_the_trading_client():
    f = _feed('test', live_klines=True)
    with patch('bot.data_feed.Client', side_effect=lambda *a, **k: MagicMock()):
        f.reinit('test', 'k2', 's2')
    assert f._klines_source == 'testnet'
    assert f._klines_client is f._client


def test_setting_defaults_to_enabled():
    assert load_settings().live_klines is True


@pytest.mark.parametrize('val,expected', [
    ('false', False), ('0', False), ('no', False),
    ('true', True), ('1', True), ('yes', True), ('TRUE', True),
])
def test_env_toggle(monkeypatch, val, expected):
    """Must be switchable off without a code change if production ever misbehaves."""
    monkeypatch.setenv('LIVE_KLINES', val)
    assert load_settings().live_klines is expected
