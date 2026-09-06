"""The guard must actually stop the network call, not just record state."""
import asyncio
import dataclasses
import time
from unittest.mock import MagicMock, patch

import pytest

from bot.data_feed import DataFeed
from bot.rate_limit_guard import RateLimited, guard
from config.settings import load_settings

def _run(coro):
    """Run a coroutine without destroying the ambient event loop.

    asyncio.run() leaves the current loop unset, which makes later tests that call
    get_event_loop() fail depending purely on ordering.
    """
    try:
        prev = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)


BAN = ("APIError(code=-1003): Way too many requests; IP(15.158.242.76) banned until "
       "{ms}. Please use the websocket for live updates to avoid bans.")


@pytest.fixture(autouse=True)
def clean():
    guard.reset()
    yield
    guard.reset()


def _feed():
    s = dataclasses.replace(load_settings(), trading_mode='test',
                            api_key='k', api_secret='s')
    with patch('bot.data_feed.Client', side_effect=lambda *a, **k: MagicMock()):
        return DataFeed(s, live_klines=False)


def test_a_ban_arms_the_guard_and_the_next_fetch_skips_the_network():
    f = _feed()
    future_ms = int((time.time() + 300) * 1000)
    f._klines_client.futures_klines.side_effect = Exception(BAN.format(ms=future_ms))

    with pytest.raises(Exception):
        f._fetch('EIGENUSDT', '15m', limit=20)
    assert f._klines_client.futures_klines.call_count == 1
    assert guard.is_blocked('testnet') is True

    # The second attempt must not reach the API — that call is what extends the ban.
    with pytest.raises(RateLimited):
        f._fetch('EIGENUSDT', '15m', limit=20)
    assert f._klines_client.futures_klines.call_count == 1, \
        'a banned endpoint was called again — this is what turned 4-minute bans into 82'


def test_ordinary_errors_do_not_suppress_later_fetches():
    """Only rate-limit errors arm the guard; a normal failure must not stop trading."""
    f = _feed()
    f._klines_client.futures_klines.side_effect = Exception('APIError(code=-2019)')
    for _ in range(3):
        with pytest.raises(Exception):
            f._fetch('EIGENUSDT', '15m', limit=20)
    assert f._klines_client.futures_klines.call_count == 3
    assert guard.is_blocked('testnet') is False


def test_balance_fetch_skips_the_call_while_banned():
    from bot.order_executor import OrderExecutor

    ex = OrderExecutor.__new__(OrderExecutor)
    ex._feed = MagicMock()
    ex._feed._is_testnet = True
    ex._QUOTE_ASSET = 'USDT'

    guard.note_exception('testnet', Exception(BAN.format(ms=int((time.time() + 300) * 1000))))
    assert _run(ex.fetch_account_balance()) == 0.0
    ex._feed.client.futures_account.assert_not_called()


def test_balance_fetch_works_normally_when_clear():
    from bot.order_executor import OrderExecutor

    ex = OrderExecutor.__new__(OrderExecutor)
    ex._feed = MagicMock()
    ex._feed._is_testnet = True
    ex._QUOTE_ASSET = 'USDT'
    ex._feed.client.futures_account.return_value = {
        'assets': [{'asset': 'USDT', 'walletBalance': '3142.50'}]
    }
    assert _run(ex.fetch_account_balance()) == pytest.approx(3142.50)
