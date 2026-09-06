import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import websockets
from binance.client import Client

from config.settings import Settings

from bot.rate_limit_guard import guard as rl_guard, RateLimited

logger = logging.getLogger(__name__)

# REST endpoints
_FUTURES_REST_TESTNET = 'https://testnet.binancefuture.com/fapi'
_FUTURES_REST_LIVE = 'https://fapi.binance.com/fapi'

# WebSocket stream base URLs
_WS_TESTNET = 'wss://stream.binancefuture.com/ws'
_WS_LIVE = 'wss://fstream.binance.com/ws'


class DataFeed:
    def __init__(self, settings: Settings, live_klines: bool = False):
        self._settings = settings
        self._is_testnet = settings.trading_mode == 'test'
        self._mode_suffix = 'test' if self._is_testnet else 'live'

        self._client = Client(settings.api_key, settings.api_secret, testnet=self._is_testnet)
        if self._is_testnet:
            self._client.FUTURES_URL = _FUTURES_REST_TESTNET

        # When live_klines=True and running in test mode, kline fetches use the
        # production API (klines are public data; production is far more stable).
        # The cache file path is unchanged — only the fetch endpoint differs.
        if live_klines and self._is_testnet:
            self._klines_client = Client(settings.api_key, settings.api_secret, testnet=False)
            self._klines_source = 'production'
        else:
            self._klines_client = self._client
            self._klines_source = 'testnet' if self._is_testnet else 'production'
        # Logged because this is invisible otherwise, and it is the difference between
        # a quota that bans us several times a day and one that sits near idle.
        logger.info(
            f"Kline REST endpoint: {self._klines_source} "
            f"({getattr(self._klines_client, 'FUTURES_URL', '?')}) | "
            f"trading endpoint: {'testnet' if self._is_testnet else 'production'}"
        )

        self._ws_base = _WS_TESTNET if self._is_testnet else _WS_LIVE

        # Combined stream / watchdog shared state
        self._last_candle_open: dict[str, int] = {}   # symbol → open_time_ms of last dispatched candle
        self._last_price_ts: dict[str, float] = {}    # symbol → monotonic time of last price tick
        self._last_candle_ts: dict[str, float] = {}   # symbol → monotonic time of last candle close
        self._reconnect_requested: bool = False

    def reinit(self, mode: str, api_key: str, api_secret: str) -> None:
        """Re-initialise client and endpoints for a new mode without creating a new DataFeed."""
        self._is_testnet = (mode == 'test')
        self._mode_suffix = 'test' if self._is_testnet else 'live'
        self._client = Client(api_key, api_secret, testnet=self._is_testnet)
        if self._is_testnet:
            self._client.FUTURES_URL = _FUTURES_REST_TESTNET
        self._klines_client = self._client  # reinit always uses the trading client
        self._klines_source = 'testnet' if self._is_testnet else 'production'
        logger.info(f"Kline REST endpoint after reinit: {self._klines_source}")
        self._ws_base = _WS_TESTNET if self._is_testnet else _WS_LIVE
        self._reconnect_requested = True
        self._last_candle_open.clear()
        self._last_price_ts.clear()
        self._last_candle_ts.clear()

    @property
    def client(self):
        return self._client

    @staticmethod
    def combined_stream_url(symbols: list[str], timeframe: str, testnet: bool) -> str:
        streams = '/'.join(f"{s.lower()}@kline_{timeframe}" for s in symbols)
        base = _WS_TESTNET if testnet else _WS_LIVE
        return f"{base.removesuffix('/ws')}/stream?streams={streams}"

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
            try:
                fresh = self._fetch(symbol, timeframe, limit=limit, start_ms=last_open_ms + 1)
            except Exception as e:
                logger.warning(f"[{symbol}] Kline update fetch failed (using cache): {e}")
                fresh = []
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

    def refresh_klines(self, symbol: str, timeframe: str, fetch_count: int = 10) -> list:
        """
        Fetch recent klines from the REST API and merge into the cache.

        Called on every candle close (fetch_count=10) and on backtest start
        (fetch_count=1500).  If the fetched batch has a gap vs the existing
        cache, re-fetches 1500 klines to fill it.
        Returns the updated kline list.
        """
        cache_path = self._cache_path(symbol, timeframe)
        cached = self._read_cache(cache_path)

        fresh = self._fetch(symbol, timeframe, limit=fetch_count)

        if cached and fresh and fetch_count < 1500:
            candle_ms = self._timeframe_to_ms(timeframe)
            if int(fresh[0][0]) > int(cached[-1][6]) + candle_ms:
                logger.warning("Gap detected in kline cache — re-fetching 1500 klines")
                fresh = self._fetch(symbol, timeframe, limit=1500)

        merged = self._merge(cached, fresh, timeframe, self._settings.kline_cache_limit)
        self._write_cache(cache_path, merged)
        logger.info(f"Kline cache refreshed: {len(merged)} candles")
        return merged

    def has_gap(self, symbol: str, timeframe: str, incoming_open_ms: int) -> bool:
        """Return True if incoming_open_ms is more than one candle-interval after the
        last cached candle's close time. Returns False if cache is missing or unreadable."""
        cache_path = self._cache_path(symbol, timeframe)
        cached = self._read_cache(cache_path)
        if not cached:
            return False
        last_close_ms = int(cached[-1][6])
        candle_ms = self._timeframe_to_ms(timeframe)
        return incoming_open_ms > last_close_ms + candle_ms

    def _fetch(self, symbol: str, timeframe: str, limit: int, start_ms: Optional[int] = None) -> list:
        params = {'symbol': symbol, 'interval': timeframe, 'limit': limit}
        if start_ms is not None:
            params['startTime'] = start_ms
        _key = self._klines_source
        _wait = rl_guard.blocked_for(_key)
        if _wait > 0:
            raise RateLimited(_key, _wait)
        try:
            return self._klines_client.futures_klines(**params)
        except Exception as e:
            # Arm the guard before re-raising so the next scheduled fetch skips the
            # network entirely instead of extending the ban.
            rl_guard.note_exception(_key, e)
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
        on_price_update: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        """
        Streams kline updates for `symbol`/`timeframe`.
        Calls `on_price_update(price)` on every tick.
        Calls `on_candle_close(kline)` when a candle closes (kline[x] == True).
        Reconnects with exponential backoff on failure.
        """
        backoff = 1

        while True:
            url = f"{self._ws_base}/{symbol.lower()}@kline_{timeframe}"
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"WebSocket connected: {url}")
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        k = msg['k']

                        if on_price_update is not None:
                            await on_price_update(float(k['c']))

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

    async def stream_combined(
        self,
        get_symbols: Callable[[], list[str]],
        timeframe: str,
        on_candle_close: Callable[[str, list], Awaitable[None]],
        on_price_update: Callable[[str, float], Awaitable[None]],
    ) -> None:
        """
        Single combined WebSocket for all active symbols.
        get_symbols() is called on each reconnect so disabled symbols are excluded.
        """
        backoff = 1
        while True:
            symbols = get_symbols()
            url = self.combined_stream_url(symbols, timeframe, self._is_testnet)
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Combined stream connected ({len(symbols)} symbols): {', '.join(symbols)}")
                    backoff = 1
                    async for raw in ws:
                        if self._reconnect_requested:
                            self._reconnect_requested = False
                            break
                        try:
                            msg = json.loads(raw)
                        except Exception as exc:
                            logger.warning(f"Failed to parse WebSocket message: {exc}")
                            continue
                        stream = msg.get("stream", "")
                        k = msg.get("data", {}).get("k", {})
                        if not stream or "@kline_" not in stream or not k:
                            continue
                        symbol = stream.split("@")[0].upper()
                        try:
                            price = float(k["c"])
                        except (ValueError, KeyError) as exc:
                            logger.warning(f"[{symbol}] Invalid price in message: {exc}")
                            continue
                        now = time.monotonic()
                        self._last_price_ts[symbol] = now

                        try:
                            await on_price_update(symbol, price)
                        except Exception as exc:
                            logger.warning(f"[{symbol}] on_price_update error: {exc}")

                        if k.get("x"):
                            open_time = int(k["t"])
                            if open_time > self._last_candle_open.get(symbol, -1):
                                self._last_candle_open[symbol] = open_time
                                self._last_candle_ts[symbol] = now
                                candle = [
                                    int(k["t"]), k["o"], k["h"], k["l"], k["c"], k["v"],
                                    int(k["T"]),
                                ]
                                try:
                                    await on_candle_close(symbol, candle)
                                except Exception as exc:
                                    logger.warning(f"[{symbol}] on_candle_close error: {exc}")
            except asyncio.CancelledError:
                logger.info("Combined stream cancelled")
                return
            except Exception as exc:
                logger.warning(f"Combined stream error: {exc}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def start_watchdog(
        self,
        get_symbols: Callable[[], list[str]],
        timeframe: str,
        on_candle_close: Callable[[str, list], Awaitable[None]],
        on_price_update: Callable[[str, float], Awaitable[None]],
        stale_threshold_s: float = 15.0,
    ) -> None:
        """
        Background REST fallback for stale price/candle data.
        Initialises timestamps so the stream gets a grace period before any fallback fires.
        """
        now = time.monotonic()
        for symbol in get_symbols():
            self._last_price_ts.setdefault(symbol, now)
            self._last_candle_ts.setdefault(symbol, now)

        timeframe_s = self._timeframe_to_ms(timeframe) / 1000.0
        candle_tick = 0
        CANDLE_EVERY = 6  # 6 × 5s = 30s

        while True:
            try:
                await asyncio.sleep(5)
                now = time.monotonic()
                now_ms = int(time.time() * 1000)
                symbols = get_symbols()

                # Initialize state for any symbols added since watchdog start
                for symbol in symbols:
                    if symbol not in self._last_price_ts:
                        self._last_price_ts[symbol] = now
                        self._last_candle_ts[symbol] = now

                # Price watchdog: check every 5s
                for symbol in symbols:
                    if now - self._last_price_ts.get(symbol, now) > stale_threshold_s:
                        try:
                            ticker = await asyncio.to_thread(
                                self._client.futures_symbol_ticker, symbol=symbol
                            )
                            price = float(ticker.get("price", 0))
                            if price > 0:
                                self._last_price_ts[symbol] = now
                                await on_price_update(symbol, price)
                        except Exception as exc:
                            logger.warning(f"[{symbol}] Price watchdog fetch failed: {exc}")

                candle_tick += 1
                if candle_tick < CANDLE_EVERY:
                    continue
                candle_tick = 0

                # Candle watchdog: check every 30s
                for symbol in symbols:
                    if now - self._last_candle_ts.get(symbol, now) <= 1.5 * timeframe_s:
                        continue
                    try:
                        klines = await asyncio.to_thread(
                            self._client.futures_klines,
                            symbol=symbol, interval=timeframe, limit=3,
                        )
                        for kline in reversed(klines):
                            if int(kline[6]) < now_ms:
                                open_time = int(kline[0])
                                if open_time > self._last_candle_open.get(symbol, -1):
                                    self._last_candle_open[symbol] = open_time
                                    self._last_candle_ts[symbol] = now
                                    candle = [
                                        int(kline[0]), kline[1], kline[2], kline[3],
                                        kline[4], kline[5], int(kline[6]),
                                    ]
                                    try:
                                        await on_candle_close(symbol, candle)
                                    except Exception as exc:
                                        logger.warning(f"[{symbol}] Watchdog candle error: {exc}")
                                break
                    except Exception as exc:
                        logger.warning(f"[{symbol}] Candle watchdog fetch failed: {exc}")

            except asyncio.CancelledError:
                logger.info("Watchdog cancelled")
                return
            except Exception as exc:
                logger.warning(f"Watchdog outer error: {exc}")

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
        path.parent.mkdir(parents=True, exist_ok=True)
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
