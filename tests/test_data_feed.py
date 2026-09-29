"""DataFeed: WebSocket stream handling, kline cache, REST kline fetching and routing, and
symbol roster changes.

Sections (one class per original test module):
  TestDataFeed                   combined-stream parsing, dedup guard, has_gap()
  TestWsKlineCache               closed WS candles are written to the kline cache
  TestWsKlineFullFields          WS candles stored with the 12 REST fields
  TestKlineStartupWeight         startup fetch limit sized to the real cache gap
  TestKlineEndpointRouting       kline endpoint follows the trading mode
  TestKlineFeedFixes20260927     /market/ stream path, atomic cache writes, backfill
  TestDisabledSymbolsPersistKlines  disabled symbols persist their WS candles too
  TestSymbolHotSubscribe         registry adds/removes subscribe without a restart
  TestSymbolDiscovery            precandidate filtering, fast presets, baseline, scoring
"""
import asyncio
import dataclasses
import json
import os
import re
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import data_feed as df
from bot.data_feed import _KLINE_FIELDS, CacheUnreadable, DataFeed, update_fetch_limit
from bot.symbol_discovery import SymbolDiscovery
from bot.symbol_registry import SymbolRegistry
from config.settings import load_settings
from tests.factories import src


# ── test_data_feed.py ─────────────────────────────────────────────────

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


# ── has_gap() ──────────────────────────────────────────────────────────── #

def make_kline(open_ms: int, interval_ms: int) -> list:
    """Returns a minimal kline: [open_ms, o, h, l, c, v, close_ms]."""
    return [open_ms, '1.0', '1.0', '1.0', '1.0', '1.0', open_ms + interval_ms - 1]


class TestDataFeed:
    """DataFeed combined-stream message parsing, the per-symbol candle dedup guard and has_gap()."""

    # ── Combined stream message parsing ────────────────────────────────────── #

    @pytest.mark.asyncio
    async def test_stream_combined_dispatches_price_update(self):
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
    async def test_stream_combined_dispatches_candle_close(self):
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
    async def test_stream_combined_dedup_guard_rejects_same_open_time(self):
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
    async def test_stream_combined_new_open_time_dispatches_again(self):
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

    def test_has_gap_no_cache_returns_false(self, tmp_path, monkeypatch):
        """Missing cache → no gap assumed (safe default)."""
        feed = make_feed()
        monkeypatch.chdir(tmp_path)
        os.makedirs('data', exist_ok=True)
        assert feed.has_gap('BTCUSDT', '15m', 1_000_000) is False

    def test_has_gap_sequential_candle_returns_false(self, tmp_path, monkeypatch):
        """Incoming candle opens exactly one interval after last cache close → no gap."""
        feed = make_feed()
        monkeypatch.chdir(tmp_path)
        os.makedirs('data', exist_ok=True)
        interval_ms = 15 * 60 * 1000
        k = make_kline(0, interval_ms)         # close_ms = interval_ms - 1
        cache_path = tmp_path / 'data' / 'BTCUSDT_15m_test.json'
        cache_path.write_text(json.dumps([k]))
        # Next candle opens at interval_ms (immediately after previous close)
        assert feed.has_gap('BTCUSDT', '15m', interval_ms) is False

    def test_has_gap_with_gap_returns_true(self, tmp_path, monkeypatch):
        """Incoming candle opens more than one interval after last cache close → gap."""
        feed = make_feed()
        monkeypatch.chdir(tmp_path)
        os.makedirs('data', exist_ok=True)
        interval_ms = 15 * 60 * 1000
        k = make_kline(0, interval_ms)
        cache_path = tmp_path / 'data' / 'BTCUSDT_15m_test.json'
        cache_path.write_text(json.dumps([k]))
        # Two intervals later = definitely a gap
        assert feed.has_gap('BTCUSDT', '15m', interval_ms * 2 + 1) is True


# ── test_ws_kline_cache.py ────────────────────────────────────────────

def _feed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = MagicMock()
    s.trading_mode = 'test'
    s.api_key = ''
    s.api_secret = ''
    s.kline_cache_limit = 5000
    monkeypatch.setattr('bot.data_feed.Client', MagicMock())
    return DataFeed(s)


def _candle(open_ms, close=100.0):
    return [open_ms, '99', '101', '98', str(close), '10', open_ms + 899_999]


class TestWsKlineCache:
    """Closed candles from the WebSocket must be written to the kline cache.

    `append_kline()` existed and was correct but was never called — the WS candle went only
    to `analyzer.add_candle()`, so the on-disk cache only advanced when a REST refresh ran.
    Consequences:

      - every restart needed a REST gap fetch (15 calls) to recover candles the WS had
        already delivered
      - during an API ban the cache froze, so a restart mid-ban came back with stale history

    Wiring it means the cache tracks the stream, the gap fetch becomes unnecessary, and a
    ban stops affecting candle history at all — which is what lets virtual orders keep
    running through one.
    """

    class TestAppend:
        def test_a_closed_candle_is_written_to_the_cache(self, tmp_path, monkeypatch):
            f = _feed(tmp_path, monkeypatch)
            f.append_kline('SOLUSDT', '15m', _candle(1_000_000))
            got = json.loads((tmp_path / 'data' / 'SOLUSDT_15m_test.json').read_text())
            assert len(got) == 1
            assert int(got[0][0]) == 1_000_000

        def test_the_same_candle_twice_is_not_duplicated(self, tmp_path, monkeypatch):
            """The watchdog and the stream can both deliver the same close."""
            f = _feed(tmp_path, monkeypatch)
            f.append_kline('SOLUSDT', '15m', _candle(1_000_000))
            f.append_kline('SOLUSDT', '15m', _candle(1_000_000))
            got = json.loads((tmp_path / 'data' / 'SOLUSDT_15m_test.json').read_text())
            assert len(got) == 1

        def test_successive_candles_accumulate_in_order(self, tmp_path, monkeypatch):
            f = _feed(tmp_path, monkeypatch)
            for i in range(5):
                f.append_kline('SOLUSDT', '15m', _candle(1_000_000 + i * 900_000))
            got = json.loads((tmp_path / 'data' / 'SOLUSDT_15m_test.json').read_text())
            assert [int(c[0]) for c in got] == [1_000_000 + i * 900_000 for i in range(5)]

        def test_the_cache_limit_is_respected(self, tmp_path, monkeypatch):
            f = _feed(tmp_path, monkeypatch)
            f._settings.kline_cache_limit = 3
            for i in range(6):
                f.append_kline('SOLUSDT', '15m', _candle(1_000_000 + i * 900_000))
            got = json.loads((tmp_path / 'data' / 'SOLUSDT_15m_test.json').read_text())
            assert len(got) == 3, 'unbounded growth would fill the disk'
            assert int(got[-1][0]) == 1_000_000 + 5 * 900_000

        def test_symbols_are_kept_in_separate_files(self, tmp_path, monkeypatch):
            f = _feed(tmp_path, monkeypatch)
            f.append_kline('SOLUSDT', '15m', _candle(1_000_000))
            f.append_kline('INJUSDT', '15m', _candle(1_000_000))
            d = tmp_path / 'data'
            assert (d / 'SOLUSDT_15m_test.json').exists()
            assert (d / 'INJUSDT_15m_test.json').exists()

    class TestWiring:
        """The whole point is that the live handler calls it."""

        def test_on_candle_close_appends_to_the_cache(self):
            main_src = src('main.py')
            i = main_src.index('async def on_candle_close')
            body = main_src[i:main_src.index('async def on_price_update', i)]
            assert 'append_kline' in body, \
                'the WS candle must reach the cache or a restart needs a REST backfill'

        def test_it_runs_off_the_event_loop(self):
            """15 symbols x ~640KB read+parse+write per candle close would stall the loop."""
            main_src = src('main.py')
            # anchor on the call, not the first mention — prose above it explains why
            i = main_src.index('feed.append_kline')
            near = main_src[max(0, i - 120):i + 120]
            assert 'to_thread' in near, 'cache I/O must not block the event loop'

        def test_a_cache_write_failure_cannot_break_candle_handling(self):
            main_src = src('main.py')
            i = main_src.index('feed.append_kline')
            near = main_src[max(0, i - 200):i + 300]
            assert 'except' in near, 'a disk problem must not stop the bot trading'


# ── test_ws_kline_full_fields.py ──────────────────────────────────────

# A real payload captured from the live stream, trimmed to the fields we map.
WS_K = {
    't': 1788861420000, 'T': 1788861479999, 's': 'BTCUSDT', 'i': '1m',
    'o': '78723.50', 'c': '78728.00', 'h': '78728.00', 'l': '78711.90',
    'v': '0.4264', 'n': 47, 'x': True,
    'q': '33564.816170', 'V': '0.1240', 'Q': '9762.232030', 'B': '0',
}


# A real REST row, for type comparison.
REST_ROW = [
    1784360700000, '74.9900', '75.0100', '74.8700', '74.9300', '29826.93',
    1784361599999, '2234802.866500', 3210, '12730.28', '953832.296400', '0',
]


class TestWsKlineFullFields:
    """A closed WS candle must be stored with the same 12 fields REST returns.

    Both WS handlers built 7 fields, so caches ended up holding two shapes -- measured on the
    server: SOLUSDT_15m_test had 4935 rows of 12 and 65 of 7. Nothing reads index > 6 today
    (the analyser uses [4]; data_feed uses [0] and [6]), so it was harmless, but it is a
    latent IndexError for the first feature that wants trade count or taker volume, and it
    discarded data the exchange already sent for free.

    The WS->REST mapping was verified against the live stream on 2026-09-08: every field is
    present and the types match (t/T/n int, the rest str).

    See docs/specs/2026-09-08-watchdog-guard-and-full-ws-klines.md.
    """

    class TestMapping:
        def test_the_row_has_all_twelve_fields(self):
            assert len(DataFeed._ws_kline_to_row(WS_K)) == _KLINE_FIELDS

        def test_every_field_type_matches_a_rest_row(self):
            row = DataFeed._ws_kline_to_row(WS_K)
            assert [type(v) for v in row] == [type(v) for v in REST_ROW]

        def test_the_values_land_on_the_right_indices(self):
            row = DataFeed._ws_kline_to_row(WS_K)
            assert row[0] == 1788861420000        # open time
            assert row[1] == '78723.50'           # open
            assert row[2] == '78728.00'           # high
            assert row[3] == '78711.90'           # low
            assert row[4] == '78728.00'           # close
            assert row[5] == '0.4264'             # base volume
            assert row[6] == 1788861479999        # close time
            assert row[7] == '33564.816170'       # quote volume
            assert row[8] == 47                   # trade count
            assert row[9] == '0.1240'             # taker buy base
            assert row[10] == '9762.232030'       # taker buy quote

        def test_close_is_still_index_4(self):
            """The analyser reads [4]; getting this wrong would corrupt every indicator."""
            assert DataFeed._ws_kline_to_row(WS_K)[4] == WS_K['c']

        def test_a_missing_optional_field_does_not_raise(self):
            """A schema change must degrade to a short row, never crash the stream loop."""
            k = {k_: v for k_, v in WS_K.items() if k_ not in ('q', 'V', 'Q', 'B', 'n')}
            row = DataFeed._ws_kline_to_row(k)
            assert row[:7] == [1788861420000, '78723.50', '78728.00', '78711.90',
                               '78728.00', '0.4264', 1788861479999]

    class TestTwoVersionsOnDisk:
        """Legacy 7-field rows and new 12-field rows coexist until the cache rotates."""

        def test_short_rows_are_padded_on_read(self, tmp_path, monkeypatch):
            feed = _feed(tmp_path, monkeypatch)
            path = tmp_path / 'data' / 'MIX_15m_test.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            legacy = [1, '2', '3', '4', '5', '6', 7]
            path.write_text(json.dumps([legacy, REST_ROW]))
            rows = feed._read_cache(path)
            assert all(len(r) == _KLINE_FIELDS for r in rows)

        def test_padding_is_none_not_zero(self, tmp_path, monkeypatch):
            """A consumer must tell 'predates full capture' from 'genuinely zero volume'.
            Zeros would let an average over these fields quietly return a wrong number."""
            feed = _feed(tmp_path, monkeypatch)
            path = tmp_path / 'data' / 'MIX_15m_test.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([[1, '2', '3', '4', '5', '6', 7]]))
            assert feed._read_cache(path)[0][7:] == [None] * 5

        def test_the_first_seven_fields_are_untouched(self, tmp_path, monkeypatch):
            """No consumer of index 0-6 may change behaviour."""
            feed = _feed(tmp_path, monkeypatch)
            path = tmp_path / 'data' / 'MIX_15m_test.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            legacy = [1, '2', '3', '4', '5', '6', 7]
            path.write_text(json.dumps([legacy]))
            assert feed._read_cache(path)[0][:7] == legacy

        def test_full_rows_pass_through_unchanged(self, tmp_path, monkeypatch):
            feed = _feed(tmp_path, monkeypatch)
            path = tmp_path / 'data' / 'MIX_15m_test.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([REST_ROW]))
            assert feed._read_cache(path)[0] == REST_ROW

        def test_a_ws_candle_round_trips_through_the_cache(self, tmp_path, monkeypatch):
            """append_kline writes it; _read_cache reads it back at full width."""
            feed = _feed(tmp_path, monkeypatch)
            feed.append_kline('BTCUSDT', '15m', DataFeed._ws_kline_to_row(WS_K))
            rows = feed._read_cache(feed._cache_path('BTCUSDT', '15m'))
            assert len(rows) == 1 and len(rows[0]) == _KLINE_FIELDS
            assert rows[0][8] == 47


# ── test_kline_startup_weight.py ──────────────────────────────────────

CANDLE_MS = 15 * 60 * 1000


class TestKlineStartupWeight:
    """Startup must not pay weight 10 per symbol to fetch a handful of candles.

    Measured from X-MBX-USED-WEIGHT-1M on 2026-09-07 (production, public endpoint):

        limit=100  -> weight 1
        limit=1000 -> weight 5
        limit=1500 -> weight 10
        limit=1500 + startTime -> weight 10   <- startTime does NOT reduce it

    `load_klines` passes limit=1500 for all 15 symbols on every restart while logging
    "Cache has N klines, fetching updates since ..." — 150 weight to retrieve maybe 20 rows.
    Binance charges on `limit` alone, so sizing it to the real cache gap costs nothing and
    removes the largest burst we generate.
    """

    def test_a_fresh_cache_needs_the_cheapest_tier(self):
        """Typical restart: the cache is minutes old."""
        assert update_fetch_limit(gap_ms=5 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 100

    def test_a_day_old_cache_still_fits_the_cheapest_tier(self):
        """100 candles of 15m covers just over 25 hours."""
        assert update_fetch_limit(gap_ms=90 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 100

    def test_a_wider_gap_steps_up_one_tier_only(self):
        got = update_fetch_limit(gap_ms=300 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500)
        assert got == 500, got

    def test_a_very_old_cache_uses_the_full_limit(self):
        assert update_fetch_limit(gap_ms=5000 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 1500

    def test_it_never_exceeds_the_caller_s_limit(self):
        for gap in (1, 100, 10_000):
            got = update_fetch_limit(gap_ms=gap * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=200)
            assert got <= 200, (gap, got)

    def test_it_only_returns_binance_weight_tier_boundaries(self):
        """Anything between tiers costs the same as the tier above, so returning 137 would
        pay for 500 while fetching less. Only 100/500/1000/1500 are ever useful."""
        for gap in range(1, 2000, 7):
            got = update_fetch_limit(gap_ms=gap * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500)
            assert got in (100, 500, 1000, 1500), (gap, got)

    def test_a_zero_or_negative_gap_is_safe(self):
        for gap in (0, -1, -CANDLE_MS):
            assert update_fetch_limit(gap_ms=gap, candle_ms=CANDLE_MS, max_limit=1500) == 100

    def test_a_zero_candle_ms_falls_back_to_the_full_limit(self):
        """Never divide by zero; an unknown timeframe must not silently fetch 100."""
        assert update_fetch_limit(gap_ms=1000, candle_ms=0, max_limit=1500) == 1500

    def test_the_margin_covers_a_boundary_gap(self):
        """A gap of exactly 100 candles must not ask for exactly 100 and lose the newest
        candle to an off-by-one."""
        assert update_fetch_limit(gap_ms=100 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 500


# ── test_kline_endpoint_routing.py ────────────────────────────────────

def _routing_feed(mode: str, live_klines: bool) -> DataFeed:
    """Build a DataFeed with a fresh mock per Client() call.

    A bare patch() hands back the same return_value every time, which would make
    two distinct clients look identical and quietly pass the assertions below.
    """
    s = dataclasses.replace(load_settings(), trading_mode=mode,
                            api_key='k', api_secret='s')
    with patch('bot.data_feed.Client', side_effect=lambda *a, **k: MagicMock()):
        return DataFeed(s, live_klines=live_klines)


class TestKlineEndpointRouting:
    """Kline endpoint follows the trading mode.

    Each mode must read the chart it trades on — test fills happen at testnet prices, so
    feeding the strategy production candles would yield statistics describing neither
    market. LIVE_KLINES exists only as a manual escape hatch and defaults to off; these
    tests pin both directions so the routing cannot drift silently, which is how it went
    unnoticed before.
    """

    def test_testnet_with_live_klines_uses_a_separate_production_client(self):
        f = _routing_feed('test', live_klines=True)
        assert f._klines_source == 'production'
        assert f._klines_client is not f._client, 'klines must not share the testnet client'

    def test_testnet_without_live_klines_keeps_the_old_behaviour(self):
        f = _routing_feed('test', live_klines=False)
        assert f._klines_source == 'testnet'
        assert f._klines_client is f._client

    def test_live_mode_is_a_no_op(self):
        """In live mode both endpoints are production already — the flag changes nothing."""
        for flag in (True, False):
            f = _routing_feed('live', live_klines=flag)
            assert f._klines_source == 'production'
            assert f._klines_client is f._client, 'live mode must not open a second client'

    def test_trading_client_stays_on_testnet_when_klines_are_production(self):
        """The whole point: only public reads move. Orders and balance stay on testnet."""
        f = _routing_feed('test', live_klines=True)
        assert f._is_testnet is True
        assert f._klines_client is not f._client

    def test_reinit_falls_back_to_the_trading_client(self):
        f = _routing_feed('test', live_klines=True)
        with patch('bot.data_feed.Client', side_effect=lambda *a, **k: MagicMock()):
            f.reinit('test', 'k2', 's2')
        assert f._klines_source == 'testnet'
        assert f._klines_client is f._client

    def test_setting_defaults_to_disabled(self):
        """Each mode must read the chart it trades on: test fills happen at testnet prices,
        so test mode reads testnet candles. This stays off unless deliberately overridden."""
        assert load_settings().live_klines is False

    @pytest.mark.parametrize('val,expected', [
        ('false', False), ('0', False), ('no', False),
        ('true', True), ('1', True), ('yes', True), ('TRUE', True),
    ])
    def test_env_toggle(self, monkeypatch, val, expected):
        """Must be switchable off without a code change if production ever misbehaves."""
        monkeypatch.setenv('LIVE_KLINES', val)
        assert load_settings().live_klines is expected


# ── test_kline_feed_fixes_2026_09_27.py ───────────────────────────────

# 2. cache integrity ----------------------------------------------------------

def _row(i: int) -> list:
    t = 1_790_000_000_000 + i * 900_000
    return [t, '1', '1', '1', '1', '1', t + 899_999, '0', 0, '0', '0', '0']


class TestKlineFeedFixes20260927:
    """Fixes from the 2026-09-27 kline investigation.

    1. Production klines stream under /market/ — the root path connects but pushes nothing,
       so the live mirror ran on the REST watchdog since Sep 7 (~40% of candles skipped).
    2. Kline cache writes are atomic and a torn/unreadable cache is never written over — the
       mirror's live caches were cut to ~100 candles that way.
    3. Disabled symbols export their chart every candle (it froze at the last restart).
    4. The Trades page orders table includes rank-1 orders, as Preset Efficiency counts them.
    """

    # 1. stream URL ---------------------------------------------------------------

    def test_live_combined_stream_uses_the_market_path(self):
        url = DataFeed.combined_stream_url(['SOLUSDT', 'INJUSDT'], '15m', testnet=False)
        assert url == 'wss://fstream.binance.com/market/stream?streams=solusdt@kline_15m/injusdt@kline_15m'

    def test_testnet_stream_unchanged(self):
        url = DataFeed.combined_stream_url(['SOLUSDT'], '15m', testnet=True)
        assert url == 'wss://stream.binancefuture.com/stream?streams=solusdt@kline_15m'

    @pytest.fixture
    def feed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = DataFeed.__new__(DataFeed)
        f._mode_suffix = 'live'
        f._klines_source = 'production'

        class S:
            kline_cache_limit = 5000
        f._settings = S()
        return f

    def test_unreadable_cache_raises_instead_of_reading_empty(self, feed):
        p = feed._cache_path('SOLUSDT', '15m')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('[[1790000000000, "1"')            # torn write
        with pytest.raises(CacheUnreadable):
            feed._read_cache(p)

    def test_append_never_writes_over_an_unreadable_cache(self, feed):
        p = feed._cache_path('SOLUSDT', '15m')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('[[1790000000000, "1"')
        feed.append_kline('SOLUSDT', '15m', _row(1))
        assert p.read_text() == '[[1790000000000, "1"'

    def test_refresh_never_writes_over_an_unreadable_cache(self, feed):
        p = feed._cache_path('SOLUSDT', '15m')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('[[1790000000000, "1"')
        feed._fetch = lambda *a, **k: [_row(i) for i in range(100)]
        assert feed.refresh_klines('SOLUSDT', '15m', 100) == []
        assert p.read_text() == '[[1790000000000, "1"'

    def test_write_is_atomic_and_leaves_no_temp_files(self, feed, tmp_path):
        p = feed._cache_path('SOLUSDT', '15m')
        DataFeed._write_cache(p, [_row(0), _row(1)])
        assert len(json.loads(p.read_text())) == 2
        assert not list(p.parent.glob('.*.tmp'))

    def test_concurrent_append_and_refresh_keep_the_history(self, feed):
        """The pattern that cut caches to ~100: append (WS) and refresh (background) at once."""
        p = feed._cache_path('SOLUSDT', '15m')
        DataFeed._write_cache(p, [_row(i) for i in range(3000)])
        feed._fetch = lambda *a, **k: [_row(i) for i in range(2900, 3000)]
        threads = [threading.Thread(target=feed.refresh_klines, args=('SOLUSDT', '15m', 100))
                   for _ in range(8)]
        threads += [threading.Thread(target=feed.append_kline, args=('SOLUSDT', '15m', _row(3000 + i)))
                    for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = json.loads(p.read_text())
        assert len(rows) >= 3000, f'history lost: {len(rows)} rows'
        assert rows[0][0] == _row(0)[0]

    # 3. disabled symbols export their chart ---------------------------------------

    def test_disabled_branch_exports_the_chart(self):
        main_src = src('main.py')
        branch = main_src[main_src.index('        if _sym_disabled:'):]
        branch = branch[:branch.index('\n            return\n')]
        assert 'export(' in branch

    # 4. orders table includes rank 1 ----------------------------------------------

    def test_orders_table_includes_rank1_like_preset_efficiency(self):
        page = src('dashboard/app/trades/page.tsx')
        block = page[page.index('const allRankOrdersFlat: RankOrder[] ='):][:200]
        assert 'fdata.rank1_orders' in block

    def test_short_cache_is_backfilled_by_paging_back(self, feed, monkeypatch):
        """The live mirror's caches were ~100 candles; the update path only fetched forward.
        Backfill pages back with endTime until the requested depth (one request per 1500)."""
        p = feed._cache_path('SOLUSDT', '15m')
        DataFeed._write_cache(p, [_row(i) for i in range(4900, 5000)])      # 100 newest
        universe = [_row(i) for i in range(0, 5000)]
        calls = []

        def fetch(sym, tf, limit, start_ms=None, end_ms=None):
            calls.append((limit, start_ms, end_ms))
            assert limit <= 1500, 'Binance rejects limit > 1500'
            rows = [r for r in universe if (end_ms is None or r[0] <= end_ms)
                    and (start_ms is None or r[0] >= start_ms)]
            return rows[-limit:] if start_ms is None else rows[:limit]
        feed._fetch = fetch
        monkeypatch.setattr(df, 'cache_is_current', lambda *a: True)
        rows = feed.load_klines('SOLUSDT', '15m', 5000)
        assert len(rows) == 5000 and rows[0][0] == _row(0)[0]
        assert len(calls) == 4                      # newest page + 3 pages back
        calls.clear()
        feed.load_klines('SOLUSDT', '15m', 5000)
        assert calls == [], 'a full cache must not be re-fetched'

    def test_backfill_stops_at_the_listing_date(self, feed, monkeypatch):
        p = feed._cache_path('NEWUSDT', '15m')
        DataFeed._write_cache(p, [_row(i) for i in range(250, 300)])
        feed._fetch = lambda sym, tf, limit, start_ms=None, end_ms=None: (
            [_row(i) for i in range(0, 300) if end_ms is None or _row(i)[0] <= end_ms][-limit:])
        monkeypatch.setattr(df, 'cache_is_current', lambda *a: True)
        assert len(feed.load_klines('NEWUSDT', '15m', 5000)) == 300

    def test_fetch_never_asks_for_more_than_1500(self):
        feed_src = src('bot/data_feed.py')
        assert "'limit': min(int(limit), _KLINE_MAX_PER_REQUEST)" in feed_src


# ── test_disabled_symbols_persist_klines.py ───────────────────────────

MAIN = src('main.py')


LINES = MAIN.splitlines()


def _disabled_branch() -> str:
    start = next(i for i, l in enumerate(LINES) if 'if _sym_disabled:' in l)
    for i in range(start + 1, len(LINES)):
        if re.match(r'\s{12}return\s*$', LINES[i]):
            return '\n'.join(LINES[start:i + 1])
    raise AssertionError('disabled branch has no return')


BRANCH = _disabled_branch()


class TestDisabledSymbolsPersistKlines:
    """Disabled symbols must persist their WebSocket candles too.

    on_candle_close's disabled-symbol branch returned before the append_kline call, so their
    on-disk caches never advanced from the stream. Every restart then re-fetched all of them
    through load_klines.

    Measured on the server 2026-09-08: 65 load_klines REST calls across 8 restarts — about 8
    per restart, which is exactly the number of disabled symbols (WLDUSDT, 1000SHIBUSDT,
    THETAUSDT, APTUSDT, JUPUSDT, 1000PEPEUSDT, DOGEUSDT, MEMEUSDT). The pattern was visible in
    the log: the first restart of a pair fetched ~8, the second a minute later skipped all 16,
    because the first restart's fetches had brought those caches current.

    Disabled symbols are in the combined stream and their analyzers are updated, so there was
    never anything to fetch — only a missing write.
    """

    def test_the_branch_appends_the_candle(self):
        assert 'append_kline' in BRANCH, 'disabled symbols still do not persist their candles'

    def test_the_append_happens_before_the_return(self):
        """After the return it would be dead code — the original bug in mirror image."""
        assert BRANCH.index('append_kline') < BRANCH.rindex('return')

    def test_it_stays_off_the_event_loop(self):
        """Same reason as the enabled path: a 640KB read+parse+write per symbol per candle."""
        i = BRANCH.index('append_kline')
        assert 'to_thread' in BRANCH[max(0, i - 120):i]

    def test_a_disk_error_cannot_break_candle_processing(self):
        i = BRANCH.index('append_kline')
        assert 'try:' in BRANCH[max(0, i - 200):i]
        assert 'except' in BRANCH[i:]

    def test_the_virtual_simulation_still_runs_first(self):
        """Data collection for disabled symbols is the whole reason this branch exists."""
        assert BRANCH.index('on_candle_close') < BRANCH.index('append_kline')

    def test_the_enabled_path_still_appends_too(self):
        assert MAIN.count('feed.append_kline') >= 2


# ── test_symbol_hot_subscribe.py ──────────────────────────────────────

def _write(path, symbols, **extra):
    d = {'symbols': symbols, 'status': {}}
    d.update(extra)
    path.write_text(json.dumps(d))


class TestSymbolHotSubscribe:
    """Adding a symbol must subscribe it, removing one must unsubscribe it — no restart.

    Measured 2026-09-09 20:03. Six symbols were added to symbol_registry.json for virtual-only
    observation (LINKUSDT, LTCUSDT, ENAUSDT, EGLDUSDT, ARBUSDT, ETHUSDT). The registry held 22
    symbols; the running bot was subscribed to 16 and processed nothing for the new six — no
    virtual orders, no rank files, nothing. They had fresh 15m kline caches written by another
    process, so it looked like they were live.

    Root cause: `reload_from_disk()` re-reads `_disabled`, `_weights`, `_paused`,
    `_disabled_ranks` and `_leverage_overrides` — but never `_symbols`. The symbol LIST was
    fixed at startup, so a dashboard add was invisible until the next restart.

    Three parts have to line up:

    1. the registry must notice the list changed (this file, TestReloadPicksUpListChanges)
    2. per-symbol state must exist, or `on_candle_close` returns at
       `if symbol not in sym_settings or symbol not in analyzers` and the symbol stays inert
    3. the websocket must resubscribe — `stream_combined` already calls `get_symbols()` on
       every reconnect, so it only needs to be asked to reconnect

    Safety rules encoded below:

    * A symbol holding an OPEN REAL POSITION is never dropped. Unsubscribing it would orphan
      a live position: its exchange stop-loss would survive, but the bot would stop tracking
      the fill, so the close would never be recorded.
    * A symbol whose kline bootstrap fails is NOT added. Bootstrap is an API call, and it is
      guarded by the rate-limit guard — during a ban it returns nothing. Half-initialised
      state would make the symbol permanently inert with no retry.
    """

    # NOTE: seed_symbols is the FIRST positional arg and registry_path the SECOND, so
    # SymbolRegistry(path) silently passes the path as the seed list and then reads the
    # DEFAULT relative path 'symbol_registry.json' instead. That is how an earlier version
    # of this file ended up asserting against the production roster.
    @pytest.fixture
    def reg_path(self, tmp_path):
        p = tmp_path / 'symbol_registry.json'
        _write(p, ['SOLUSDT', 'TIAUSDT'])
        return p

    class TestReloadPicksUpListChanges:
        def test_an_added_symbol_appears(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            assert r.get_symbols() == ['SOLUSDT', 'TIAUSDT']
            _write(reg_path, ['SOLUSDT', 'TIAUSDT', 'LINKUSDT'])
            r.reload_from_disk()
            assert 'LINKUSDT' in r.get_symbols(), \
                'reload_from_disk never re-read _symbols — this is the reported bug'

        def test_a_removed_symbol_disappears(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            _write(reg_path, ['SOLUSDT'])
            r.reload_from_disk()
            assert r.get_symbols() == ['SOLUSDT']

        def test_it_reports_what_changed(self, reg_path):
            """The caller has to bootstrap the added ones and tear down the removed ones,
            so it needs the delta rather than having to diff the list itself."""
            r = SymbolRegistry([], registry_path=reg_path)
            _write(reg_path, ['SOLUSDT', 'LINKUSDT', 'LTCUSDT'])
            added, removed = r.reload_from_disk()
            assert set(added) == {'LINKUSDT', 'LTCUSDT'}
            assert set(removed) == {'TIAUSDT'}

        def test_no_change_reports_nothing(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            added, removed = r.reload_from_disk()
            assert added == [] and removed == []

        def test_symbols_are_upper_cased(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            _write(reg_path, ['SOLUSDT', 'linkusdt'])
            added, _ = r.reload_from_disk()
            assert added == ['LINKUSDT']
            assert 'LINKUSDT' in r.get_symbols()

        def test_the_six_real_symbols_round_trip(self, reg_path):
            """The exact set added on 2026-09-09."""
            new = ['LINKUSDT', 'LTCUSDT', 'ENAUSDT', 'EGLDUSDT', 'ARBUSDT', 'ETHUSDT']
            r = SymbolRegistry([], registry_path=reg_path)
            _write(reg_path, ['SOLUSDT', 'TIAUSDT'] + new)
            added, removed = r.reload_from_disk()
            assert set(added) == set(new) and removed == []

    class TestReloadStaysSafe:
        def test_a_corrupt_file_keeps_the_current_list(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            before = r.get_symbols()
            reg_path.write_text('{not json')
            added, removed = r.reload_from_disk()
            assert r.get_symbols() == before, 'a bad read must not empty the symbol list'
            assert added == [] and removed == []

        def test_a_missing_file_keeps_the_current_list(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            before = r.get_symbols()
            reg_path.unlink()
            added, removed = r.reload_from_disk()
            assert r.get_symbols() == before
            assert added == [] and removed == []

        def test_a_file_with_no_symbols_key_keeps_the_current_list(self, reg_path):
            """Truncating the roster to nothing would unsubscribe everything at once."""
            r = SymbolRegistry([], registry_path=reg_path)
            before = r.get_symbols()
            reg_path.write_text(json.dumps({'status': {}}))
            r.reload_from_disk()
            assert r.get_symbols() == before

        def test_an_empty_symbol_list_is_refused(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            before = r.get_symbols()
            _write(reg_path, [])
            r.reload_from_disk()
            assert r.get_symbols() == before, 'an empty roster must not disable every symbol'

        def test_the_other_reloaded_fields_still_work(self, reg_path):
            """Regression: the list reload must not displace what reload already did."""
            r = SymbolRegistry([], registry_path=reg_path)
            _write(reg_path, ['SOLUSDT', 'TIAUSDT'],
                   disabled={'TIAUSDT': {'reason': 'x'}}, weights={'SOLUSDT': 7})
            r.reload_from_disk()
            assert r.is_disabled('TIAUSDT') is True
            assert r.get_weight('SOLUSDT') == 7

        def test_reload_is_idempotent(self, reg_path):
            r = SymbolRegistry([], registry_path=reg_path)
            _write(reg_path, ['SOLUSDT', 'LINKUSDT'])
            first = r.reload_from_disk()
            second = r.reload_from_disk()
            assert set(first[0]) == {'LINKUSDT'}
            assert second == ([], []), 'a second reload re-reported the same change'

    class TestTheFeedCanBeAskedToResubscribe:
        def test_request_reconnect_sets_the_flag(self):
            from bot.data_feed import DataFeed
            from config.settings import load_settings
            f = DataFeed(load_settings())
            assert f._reconnect_requested is False
            f.request_reconnect()
            assert f._reconnect_requested is True

        def test_the_stream_reevaluates_the_symbol_list_on_reconnect(self):
            """Already true — stream_combined calls get_symbols() inside its retry loop, so
            a reconnect is all that is needed. Pinned so a refactor cannot hoist it out."""
            import inspect
            from bot.data_feed import DataFeed
            src = inspect.getsource(DataFeed.stream_combined)
            loop = src.index('while True:')
            assert 'get_symbols()' in src[loop:], \
                'get_symbols() moved outside the reconnect loop — adds would need a restart'

    class TestMainWiresItUp:
        """Source-level, matching how the rest of this suite pins main.py wiring."""

        @staticmethod
        def _src():
            return src('main.py')

        def test_the_candle_path_acts_on_the_reload_delta(self):
            s = self._src()
            i = s.index('symbol_registry.reload_from_disk()')
            window = s[i:i + 5000]
            assert 'request_reconnect' in window, \
                'the symbol list can change and the websocket is never told'

        def test_added_symbols_get_settings_and_an_analyzer(self):
            s = self._src()
            i = s.index('symbol_registry.reload_from_disk()')
            window = s[i:i + 5000]
            assert 'sym_settings[' in window and 'analyzers[' in window, \
                'without both, on_candle_close returns early and the symbol stays inert'

        def test_an_open_real_position_blocks_removal(self):
            s = self._src()
            i = s.index('symbol_registry.reload_from_disk()')
            window = s[i:i + 5000]
            assert 'has_open_real' in window or 'open_real' in window, \
                'unsubscribing a symbol with a live position would orphan it'

        def test_a_failed_kline_bootstrap_does_not_add_the_symbol(self):
            s = self._src()
            i = s.index('symbol_registry.reload_from_disk()')
            window = s[i:i + 5000]
            assert 'continue' in window, \
                'a symbol whose klines failed must be retried, not half-added'

        def test_the_sync_runs_once_per_candle_not_once_per_symbol(self):
            """on_candle_close fires per symbol; bootstrapping on every call would re-fetch
            klines up to 22 times a candle."""
            s = self._src()
            i = s.index('symbol_registry.reload_from_disk()')
            window = s[i:i + 5000]
            assert '_symbols_synced_at' in window, 'no once-per-candle guard'


# ── test_symbol_discovery.py ──────────────────────────────────────────

EXCHANGE_INFO_RESPONSE = {
    "symbols": [
        {"symbol": "BTCUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "ETHUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "XRPUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "FOOBAR",   "contractType": "PERPETUAL", "quoteAsset": "USDT"},  # not in FUTURES_SYMBOLS
        {"symbol": "BTCBUSD",  "contractType": "PERPETUAL", "quoteAsset": "BUSD"},  # wrong quote
        {"symbol": "DOGEUSDT", "contractType": "DELIVERING", "quoteAsset": "USDT"}, # not perpetual
    ]
}


TICKER_RESPONSE = [
    {"symbol": "BTCUSDT",  "quoteVolume": "5000000000"},
    {"symbol": "ETHUSDT",  "quoteVolume": "2000000000"},
    {"symbol": "XRPUSDT",  "quoteVolume": "500000"},      # below threshold
    {"symbol": "FOOBAR",   "quoteVolume": "9000000000"},
]


def _mock_get(url, **kwargs):
    m = MagicMock()
    if 'exchangeInfo' in url:
        m.json.return_value = EXCHANGE_INFO_RESPONSE
    else:
        m.json.return_value = TICKER_RESPONSE
    return m


class TestSymbolDiscovery:
    """SymbolDiscovery: exchange-info precandidate filtering, fast-preset ranking, baseline and
    candidate scoring."""

    def test_get_precandidates_filters_by_allowlist(self):
        sd = SymbolDiscovery()
        with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
            result = sd.get_precandidates(active=[], min_volume=1_000_000)
        # FOOBAR not in FUTURES_SYMBOLS, BTCBUSD wrong quote, DOGEUSDT not perpetual
        assert 'FOOBAR' not in result
        assert 'BTCBUSD' not in result
        assert 'DOGEUSDT' not in result

    def test_get_precandidates_filters_active(self):
        sd = SymbolDiscovery()
        with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
            result = sd.get_precandidates(active=['BTCUSDT'], min_volume=1_000_000)
        assert 'BTCUSDT' not in result

    def test_get_precandidates_filters_low_volume(self):
        sd = SymbolDiscovery()
        with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
            result = sd.get_precandidates(active=[], min_volume=1_000_000)
        # XRPUSDT has volume 500_000 < 1_000_000
        assert 'XRPUSDT' not in result
        assert 'ETHUSDT' in result

    def test_get_precandidates_raises_on_api_error(self):
        sd = SymbolDiscovery()
        with patch('bot.symbol_discovery.requests.get', side_effect=Exception("timeout")):
            with pytest.raises(RuntimeError, match="Exchange info fetch failed"):
                sd.get_precandidates(active=[], min_volume=1_000_000)

    def test_get_fast_presets_ranks_by_avg_profit(self, tmp_path):
        sd = SymbolDiscovery()

        results_dir = tmp_path / "dashboard" / "public"
        results_dir.mkdir(parents=True)
        (results_dir / "backtest_results_BTCUSDT.json").write_text(json.dumps({
            "presets": {
                "preset_a": {"total_profit_pct": 10.0, "total_trades": 5},
                "preset_b": {"total_profit_pct":  2.0, "total_trades": 5},
                "preset_c": {"total_profit_pct":  6.0, "total_trades": 5},
            }
        }))
        (results_dir / "backtest_results_ETHUSDT.json").write_text(json.dumps({
            "presets": {
                "preset_a": {"total_profit_pct": 8.0, "total_trades": 5},
                "preset_b": {"total_profit_pct": 4.0, "total_trades": 5},
            }
        }))

        with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
            result = sd.get_fast_presets(
                active=["BTCUSDT", "ETHUSDT"],
                n=2,
                all_preset_names=["preset_a", "preset_b", "preset_c"],
            )

        # preset_a avg=(10+8)/2=9, preset_c avg=6 (ETHUSDT missing → 6/1), preset_b avg=(2+4)/2=3
        assert result[0] == "preset_a"
        assert len(result) == 2

    def test_get_fast_presets_falls_back_when_no_results(self, tmp_path):
        sd = SymbolDiscovery()
        results_dir = tmp_path / "dashboard" / "public"
        results_dir.mkdir(parents=True)
        all_names = ["p1", "p2", "p3", "p4"]

        with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
            result = sd.get_fast_presets(active=["BTCUSDT"], n=2, all_preset_names=all_names)

        assert result == ["p1", "p2"]

    def test_compute_baseline_averages_best_preset_efficiency(self, tmp_path):
        sd = SymbolDiscovery()
        results_dir = tmp_path / "dashboard" / "public"
        results_dir.mkdir(parents=True)
        # BTCUSDT best preset: profit 10 / 5 trades = 2.0
        # ETHUSDT best preset: profit 6  / 2 trades = 3.0
        # baseline = (2.0 + 3.0) / 2 = 2.5
        (results_dir / "backtest_results_BTCUSDT.json").write_text(json.dumps({
            "presets": {
                "p1": {"total_profit_pct": 10.0, "total_trades": 5},
                "p2": {"total_profit_pct":  4.0, "total_trades": 5},
            }
        }))
        (results_dir / "backtest_results_ETHUSDT.json").write_text(json.dumps({
            "presets": {
                "p1": {"total_profit_pct": 6.0, "total_trades": 2},
            }
        }))

        with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
            result = sd.compute_baseline(["BTCUSDT", "ETHUSDT"])

        assert result == pytest.approx(2.5)

    def test_compute_baseline_returns_zero_when_no_files(self, tmp_path):
        sd = SymbolDiscovery()
        results_dir = tmp_path / "dashboard" / "public"
        results_dir.mkdir(parents=True)

        with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
            result = sd.compute_baseline(["BTCUSDT"])

        assert result == 0.0

    def test_score_candidate_returns_none_when_no_cache(self, tmp_path, monkeypatch):
        """When DataFeed fetch fails and no cache file exists, returns None."""
        from bot.symbol_discovery import SymbolDiscovery
        sd = SymbolDiscovery()

        # DataFeed.refresh_klines raises → cache still absent
        with patch('bot.symbol_discovery.DataFeed') as MockFeed:
            MockFeed.return_value.refresh_klines.side_effect = Exception("api error")
            # Point cache lookup to a dir that has no files
            monkeypatch.chdir(tmp_path)
            (tmp_path / "data").mkdir()
            # Load settings needs .env; patch it out
            with patch('bot.symbol_discovery.load_settings') as mock_ls:
                mock_settings = MagicMock()
                mock_settings.trading_mode = 'testnet'
                mock_settings.timeframe = '15m'
                mock_ls.return_value = mock_settings
                result = sd.score_candidate(
                    symbol="NEWUSDT",
                    preset_subset={"default": {}},
                    klines_count=500,
                    baseline=2.0,
                    baseline_ratio=0.7,
                    min_floor=0.0,
                    position_size=1000.0,
                    leverage=1.0,
                )
        assert result is None

    def test_score_candidate_returns_none_below_efficiency_threshold(self, tmp_path, monkeypatch):
        """A candidate scoring below baseline_ratio × baseline is filtered out."""
        import json as _json
        from bot.symbol_discovery import SymbolDiscovery
        sd = SymbolDiscovery()

        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Write a cache file with klines that produce zero trades (too few candles)
        klines = [[i * 60000, "100", "101", "99", "100", "1000", (i + 1) * 60000]
                  for i in range(10)]
        cache = data_dir / "NEWUSDT_15m_test.json"
        cache.write_text(_json.dumps(klines))

        with patch('bot.symbol_discovery.DataFeed'):
            with patch('bot.symbol_discovery.load_settings') as mock_ls:
                mock_settings = MagicMock()
                mock_settings.trading_mode = 'testnet'
                mock_settings.timeframe = '15m'
                mock_ls.return_value = mock_settings
                with patch('bot.symbol_discovery.load_risk_config', return_value={"backtest_initial_balance_usdt": 0.0}):
                    result = sd.score_candidate(
                        symbol="NEWUSDT",
                        preset_subset={"default": {}},
                        klines_count=500,
                        baseline=100.0,   # very high baseline
                        baseline_ratio=0.7,
                        min_floor=0.0,
                        position_size=1000.0,
                        leverage=1.0,
                    )
        # Zero trades → total_order_count=0 → returns None (no trades produced)
        assert result is None


class TestSocketKeepsBeingRead:
    """websockets' default 32-message queue filled with ticks while a candle batch ran
    inline; the library then stopped reading, the pong went unread and the stream died with
    '1011 keepalive ping timeout' (~4/hour since 2026-09-28)."""

    def test_every_stream_connects_with_an_unbounded_queue(self):
        from tests.factories import src
        import re
        calls = re.findall(r'websockets\.connect\([^)]*\)', src('bot/data_feed.py'))
        assert calls and all('max_queue=None' in c for c in calls), calls


class TestHasGapWithoutReparsing:
    """has_gap() parsed the whole ~700 KB kline cache per symbol per candle for one
    timestamp (~1.8 s per batch, 2026-09-29). It now uses the close remembered from our
    own write while the file is unchanged, and re-reads when someone else changed it."""

    def _rows(self, n, start=0):
        return [[start + i * 900_000, '1', '1', '1', '1', '0', start + i * 900_000 + 899_999]
                for i in range(n)]

    def test_after_our_write_the_cache_is_not_parsed(self, tmp_path, monkeypatch):
        from bot.data_feed import DataFeed
        p = tmp_path / 'X_15m_test.json'
        DataFeed._write_cache(p, self._rows(5))
        feed = DataFeed.__new__(DataFeed)
        feed._cache_path = lambda s, tf: p
        monkeypatch.setattr(DataFeed, '_read_cache', staticmethod(lambda path: (_ for _ in ()).throw(AssertionError('parsed'))))
        last_close = 4 * 900_000 + 899_999
        assert feed.has_gap('X', '15m', last_close + 1) is False
        assert feed.has_gap('X', '15m', last_close + 900_000 + 2) is True

    def test_an_outside_change_is_read_again(self, tmp_path):
        import json, os
        from bot.data_feed import DataFeed
        p = tmp_path / 'X_15m_test.json'
        DataFeed._write_cache(p, self._rows(5))
        p.write_text(json.dumps(self._rows(3)))                      # someone else rewrote it
        os.utime(p, ns=(1, p.stat().st_mtime_ns + 1_000_000))
        feed = DataFeed.__new__(DataFeed)
        feed._cache_path = lambda s, tf: p
        last_close_3 = 2 * 900_000 + 899_999
        assert feed.has_gap('X', '15m', last_close_3 + 900_000 + 2) is True
