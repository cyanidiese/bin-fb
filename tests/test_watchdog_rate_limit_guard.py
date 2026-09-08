"""The watchdogs must not issue requests while the endpoint has us banned.

`_fetch()` consults the rate-limit guard and refuses to touch the network while banned.
Both watchdogs bypassed it, calling `self._client` directly, and they updated their
staleness timestamps only on success -- so a symbol that failed kept qualifying on every
tick. Candles retried every 30s, prices every 5s: up to 192 requests/min across 16
symbols, each one adding 120s to the ban. A self-sustaining amplifier, dormant while the
WebSocket is healthy, which is why it went unnoticed.

See docs/specs/2026-09-08-watchdog-guard-and-full-ws-klines.md.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from bot.data_feed import DataFeed
from bot.rate_limit_guard import guard as rl_guard, RateLimited


def _feed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = MagicMock()
    s.trading_mode = 'test'
    s.api_key = ''
    s.api_secret = ''
    s.kline_cache_limit = 5000
    monkeypatch.setattr('bot.data_feed.Client', MagicMock())
    return DataFeed(s)


@pytest.fixture(autouse=True)
def _clean_guard():
    """The guard is process-wide; a leaked block would poison other tests."""
    rl_guard._blocked.clear() if hasattr(rl_guard, '_blocked') else None
    yield
    rl_guard._blocked.clear() if hasattr(rl_guard, '_blocked') else None


def _arm(monkeypatch, seconds=600.0):
    """Report every endpoint as banned, without depending on guard internals."""
    monkeypatch.setattr(rl_guard, 'blocked_for', lambda key: seconds)


class TestTickerIsGuarded:
    def test_no_network_call_while_banned(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path, monkeypatch)
        _arm(monkeypatch)
        with pytest.raises(RateLimited):
            feed._fetch_ticker('SOLUSDT')
        feed._client.futures_symbol_ticker.assert_not_called()

    def test_it_does_call_when_not_banned(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path, monkeypatch)
        monkeypatch.setattr(rl_guard, 'blocked_for', lambda key: 0.0)
        feed._client.futures_symbol_ticker.return_value = {'price': '102.5'}
        assert feed._fetch_ticker('SOLUSDT') == 102.5
        feed._client.futures_symbol_ticker.assert_called_once()

    def test_a_failure_arms_the_guard(self, tmp_path, monkeypatch):
        """So the next scheduled probe skips the network instead of extending the ban."""
        feed = _feed(tmp_path, monkeypatch)
        monkeypatch.setattr(rl_guard, 'blocked_for', lambda key: 0.0)
        noted = []
        monkeypatch.setattr(rl_guard, 'note_exception', lambda k, e: noted.append(k))
        feed._client.futures_symbol_ticker.side_effect = RuntimeError('boom')
        with pytest.raises(RuntimeError):
            feed._fetch_ticker('SOLUSDT')
        assert noted, 'a failed ticker must arm the guard'


class TestWatchdogMakesNoCallsWhileBanned:
    """Drives the real watchdog loop with time compressed, guard armed."""

    def _run(self, feed, monkeypatch, iterations=12):
        real_sleep = asyncio.sleep
        calls = {'n': 0}

        async def fast_sleep(_s):
            calls['n'] += 1
            if calls['n'] > iterations:
                raise asyncio.CancelledError
            await real_sleep(0)

        monkeypatch.setattr('bot.data_feed.asyncio.sleep', fast_sleep)

        # Make every symbol look long-stale so BOTH branches fire. Both dicts must be
        # pre-populated: start_watchdog uses setdefault (so our values survive), but the
        # per-iteration init would reset the candle stamp for any symbol missing from
        # _last_price_ts. Compressed sleep barely advances the monotonic clock, so
        # without this the candle branch's 1.5x-timeframe gate (22.5 min) never opens and
        # the assertion below would pass vacuously.
        import time as _time
        _old = _time.monotonic() - 1_000_000.0
        feed._last_price_ts = {s: _old for s in ('SOLUSDT', 'INJUSDT', 'TIAUSDT')}
        feed._last_candle_ts = dict(feed._last_price_ts)

        # start_watchdog catches CancelledError and returns, so this completes normally
        # once fast_sleep raises -- that also exercises the real cancellation path.
        async def go():
            await feed.start_watchdog(
                get_symbols=lambda: ['SOLUSDT', 'INJUSDT', 'TIAUSDT'],
                timeframe='15m',
                on_candle_close=lambda s, c: asyncio.sleep(0),
                on_price_update=lambda s, p: asyncio.sleep(0),
                stale_threshold_s=-1.0,   # stale the moment it is checked
            )

        asyncio.run(go())
        assert calls['n'] > iterations, 'the loop never ran'

    def test_neither_watchdog_touches_the_network(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path, monkeypatch)
        _arm(monkeypatch)
        self._run(feed, monkeypatch)
        assert feed._client.futures_symbol_ticker.call_count == 0, \
            'price watchdog called the API while banned'
        assert feed._client.futures_klines.call_count == 0, \
            'candle watchdog called the API while banned'

    def test_the_kline_client_is_untouched_too(self, tmp_path, monkeypatch):
        """_fetch uses _klines_client; it must be skipped as well."""
        feed = _feed(tmp_path, monkeypatch)
        _arm(monkeypatch)
        self._run(feed, monkeypatch)
        assert feed._klines_client.futures_klines.call_count == 0


class TestCandleWatchdogUsesTheKlineEndpoint:
    def test_it_goes_through_fetch_not_the_trading_client(self, tmp_path, monkeypatch):
        """The old direct call used the trading client. Under live_klines that would pull
        testnet candles into a cache the rest of the system fills from production."""
        src = (__import__('pathlib').Path('bot/data_feed.py')).read_text()
        wd = src.split('# Candle watchdog', 1)[1].split('except asyncio.CancelledError', 1)[0]
        assert 'self._fetch' in wd
        assert 'self._client.futures_klines' not in wd
