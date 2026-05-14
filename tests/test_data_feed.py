# tests/test_data_feed.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from bot.data_feed import DataFeed


def make_feed(testnet: bool = True) -> DataFeed:
    settings = MagicMock()
    settings.trading_mode = 'test' if testnet else 'live'
    settings.api_key = 'k'
    settings.api_secret = 's'
    settings.kline_cache_limit = 5000
    with patch('bot.data_feed.Client'):
        return DataFeed(settings)


def make_kline_msg(symbol: str, price: str, open_time: int, closed: bool) -> str:
    return json.dumps({
        "stream": f"{symbol.lower()}@kline_15m",
        "data": {
            "k": {
                "t": open_time,
                "T": open_time + 899999,
                "o": price, "h": price, "l": price, "c": price, "v": "100",
                "x": closed,
            }
        }
    })


def make_fake_ws(msgs: list):
    """
    Returns a FakeWS instance that yields msgs then blocks on asyncio.Event
    so the task can be cancelled cleanly by the test harness.
    """
    msg_iter = iter(msgs)

    class FakeWS:
        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(msg_iter)
            except StopIteration:
                # Suspend here; allow the test to cancel the task.
                await asyncio.Event().wait()
                raise StopAsyncIteration  # never reached

    return FakeWS()


async def _run_feed(feed: DataFeed, msgs: list, on_candle_close, on_price_update, symbols: list[str]) -> None:
    """
    Run stream_combined inside a cancellable task.
    Yields control after the fake WS exhausts its messages so that all
    callbacks have fired, then cancels the task and waits for it to stop.
    """
    fake_ws = make_fake_ws(msgs)

    with patch('websockets.connect') as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=fake_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        task = asyncio.create_task(
            feed.stream_combined(
                get_symbols=lambda: symbols,
                timeframe='15m',
                on_candle_close=on_candle_close,
                on_price_update=on_price_update,
            )
        )
        # Let the event loop run until the WS blocks (all messages consumed).
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # two yields to ensure callbacks have fired
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── Combined stream message parsing ────────────────────────────────────── #

@pytest.mark.asyncio
async def test_stream_combined_dispatches_price_update():
    feed = make_feed()
    received: list = []

    async def fake_price(symbol: str, price: float) -> None:
        received.append((symbol, price))

    async def fake_candle(symbol: str, kline: list) -> None:
        pass

    msgs = [make_kline_msg("BTCUSDT", "50000.0", 1000000, False)]
    await _run_feed(feed, msgs, fake_candle, fake_price, ['BTCUSDT'])

    assert any(sym == 'BTCUSDT' and price == pytest.approx(50000.0) for sym, price in received)


@pytest.mark.asyncio
async def test_stream_combined_dispatches_candle_close():
    feed = make_feed()
    candles: list = []

    async def fake_price(symbol: str, price: float) -> None:
        pass

    async def fake_candle(symbol: str, kline: list) -> None:
        candles.append((symbol, kline))

    msgs = [make_kline_msg("ETHUSDT", "2000.0", 2000000, True)]
    await _run_feed(feed, msgs, fake_candle, fake_price, ['ETHUSDT'])

    assert len(candles) == 1
    sym, kline = candles[0]
    assert sym == 'ETHUSDT'
    assert kline[0] == 2000000


# ── Dedup guard ────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_stream_combined_dedup_guard_rejects_same_open_time():
    """Two messages with the same open_time for the same symbol must fire only one candle_close."""
    feed = make_feed()
    candles: list = []

    async def fake_price(symbol: str, price: float) -> None:
        pass

    async def fake_candle(symbol: str, kline: list) -> None:
        candles.append((symbol, kline))

    # Same open_time 3000000 sent twice
    msgs = [
        make_kline_msg("BTCUSDT", "50000.0", 3000000, True),
        make_kline_msg("BTCUSDT", "50100.0", 3000000, True),
    ]
    await _run_feed(feed, msgs, fake_candle, fake_price, ['BTCUSDT'])

    assert len(candles) == 1, f"Expected 1 candle dispatch, got {len(candles)}"


@pytest.mark.asyncio
async def test_stream_combined_new_open_time_dispatches_again():
    """After a candle with open_time T fires, a candle with open_time T+1 must also fire."""
    feed = make_feed()
    candles: list = []

    async def fake_price(symbol: str, price: float) -> None:
        pass

    async def fake_candle(symbol: str, kline: list) -> None:
        candles.append(int(kline[0]))

    msgs = [
        make_kline_msg("BTCUSDT", "50000.0", 4000000, True),
        make_kline_msg("BTCUSDT", "50100.0", 4900000, True),  # newer open_time
    ]
    await _run_feed(feed, msgs, fake_candle, fake_price, ['BTCUSDT'])

    assert candles == [4000000, 4900000]
