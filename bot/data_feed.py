import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

import websockets
from binance.client import Client

from config.settings import Settings

logger = logging.getLogger(__name__)

# REST endpoints
_FUTURES_REST_TESTNET = 'https://testnet.binancefuture.com/fapi'
_FUTURES_REST_LIVE = 'https://fapi.binance.com/fapi'

# WebSocket stream base URLs
_WS_TESTNET = 'wss://stream.binancefuture.com/ws'
_WS_LIVE = 'wss://fstream.binance.com/ws'


class DataFeed:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._is_testnet = settings.trading_mode == 'testnet'
        self._mode_suffix = 'test' if self._is_testnet else 'live'

        self._client = Client(settings.api_key, settings.api_secret, testnet=self._is_testnet)
        if self._is_testnet:
            self._client.FUTURES_URL = _FUTURES_REST_TESTNET

        self._ws_base = _WS_TESTNET if self._is_testnet else _WS_LIVE

    # ------------------------------------------------------------------ #
    # REST — kline history                                                 #
    # ------------------------------------------------------------------ #

    def load_klines(self, symbol: str, timeframe: str, limit: int) -> list:
        """
        Loads kline history for `symbol`/`timeframe`.
        Reads from cache first; fetches only missing candles from the exchange.
        Saves the merged result back to cache.
        """
        cache_path = self._cache_path(symbol, timeframe)
        self._migrate_old_cache(symbol, timeframe, cache_path)
        cached = self._read_cache(cache_path)

        if cached:
            last_open_ms = int(cached[-1][0])
            logger.info(f"Cache has {len(cached)} klines, fetching updates since {last_open_ms}")
            fresh = self._fetch(symbol, timeframe, limit=limit, start_ms=last_open_ms + 1)
        else:
            logger.info(f"No cache found, fetching {limit} klines")
            fresh = self._fetch(symbol, timeframe, limit=limit)

        merged = self._merge(cached, fresh, timeframe, self._settings.kline_cache_limit)
        self._write_cache(cache_path, merged)
        logger.info(f"Kline cache ready: {len(merged)} candles")
        return merged

    def append_kline(self, symbol: str, timeframe: str, kline: list) -> None:
        """Appends a single closed candle to the cache file."""
        cache_path = self._cache_path(symbol, timeframe)  # already migrated on load_klines
        klines = self._read_cache(cache_path)
        if not klines or klines[-1][0] != kline[0]:
            klines.append(kline)
            self._write_cache(cache_path, klines[-self._settings.kline_cache_limit:])

    def _fetch(self, symbol: str, timeframe: str, limit: int, start_ms: Optional[int] = None) -> list:
        params = {'symbol': symbol, 'interval': timeframe, 'limit': limit}
        if start_ms is not None:
            params['startTime'] = start_ms
        try:
            return self._client.futures_klines(**params)
        except Exception as e:
            logger.error(f"Failed to fetch klines: {e}")
            raise

    # ------------------------------------------------------------------ #
    # WebSocket — live stream                                              #
    # ------------------------------------------------------------------ #

    async def stream_klines(
        self,
        symbol: str,
        timeframe: str,
        on_candle_close: Callable[[list], Awaitable[None]],
        on_price_update: Optional[Callable[[float], None]] = None,
    ) -> None:
        """
        Streams kline updates for `symbol`/`timeframe`.
        Calls `on_price_update(price)` on every tick.
        Calls `on_candle_close(kline)` when a candle closes (kline[x] == True).
        Reconnects with exponential backoff on failure.
        """
        url = f"{self._ws_base}/{symbol.lower()}@kline_{timeframe}"
        backoff = 1

        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"WebSocket connected: {url}")
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        k = msg['k']

                        if on_price_update is not None:
                            on_price_update(float(k['c']))

                        if k['x']:
                            candle = [
                                int(k['t']),  # open time ms
                                k['o'],       # open
                                k['h'],       # high
                                k['l'],       # low
                                k['c'],       # close
                                k['v'],       # volume
                                int(k['T']),  # close time ms
                            ]
                            await on_candle_close(candle)

            except asyncio.CancelledError:
                logger.info("WebSocket stream cancelled")
                return
            except Exception as e:
                logger.warning(f"WebSocket error: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ------------------------------------------------------------------ #
    # Cache helpers                                                        #
    # ------------------------------------------------------------------ #

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return Path('data') / f'{symbol}_{timeframe}_{self._mode_suffix}.json'

    def _migrate_old_cache(self, symbol: str, timeframe: str, new_path: Path) -> None:
        """Rename the old mode-less cache file to the new name on first run."""
        if new_path.exists():
            return
        old_path = Path('data') / f'{symbol}_{timeframe}.json'
        if old_path.exists():
            old_path.rename(new_path)
            logger.info(f"Cache migrated: {old_path.name} → {new_path.name}")

    @staticmethod
    def _read_cache(path: Path) -> list:
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    @staticmethod
    def _write_cache(path: Path, klines: list) -> None:
        path.parent.mkdir(exist_ok=True)
        with open(path, 'w') as f:
            json.dump(klines, f)

    def _merge(self, cached: list, fresh: list, timeframe: str, cache_limit: int) -> list:
        if not fresh:
            return cached[-cache_limit:]

        # Gap detection: if the first fresh candle opens more than one candle-width
        # after the last cached close, the stored history is stale — discard it
        # so we don't end up with a hole in the middle of the data.
        if cached:
            candle_ms = self._timeframe_to_ms(timeframe)
            if int(fresh[0][0]) > int(cached[-1][6]) + candle_ms:
                logger.warning(
                    f"Gap detected in kline cache — discarding {len(cached)} stale candles"
                )
                cached = []

        combined = {int(k[0]): k for k in cached}
        combined.update({int(k[0]): k for k in fresh})
        sorted_klines = sorted(combined.values(), key=lambda k: int(k[0]))
        return sorted_klines[-cache_limit:]

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        units = {'m': 60_000, 'h': 3_600_000, 'd': 86_400_000}
        unit = timeframe[-1]
        value = int(timeframe[:-1])
        return value * units.get(unit, 60_000)
