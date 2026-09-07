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
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.data_feed import DataFeed


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
        src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
        i = src.index('async def on_candle_close')
        body = src[i:src.index('async def on_price_update', i)]
        assert 'append_kline' in body, \
            'the WS candle must reach the cache or a restart needs a REST backfill'

    def test_it_runs_off_the_event_loop(self):
        """15 symbols x ~640KB read+parse+write per candle close would stall the loop."""
        src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
        # anchor on the call, not the first mention — prose above it explains why
        i = src.index('feed.append_kline')
        near = src[max(0, i - 120):i + 120]
        assert 'to_thread' in near, 'cache I/O must not block the event loop'

    def test_a_cache_write_failure_cannot_break_candle_handling(self):
        src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
        i = src.index('feed.append_kline')
        near = src[max(0, i - 200):i + 300]
        assert 'except' in near, 'a disk problem must not stop the bot trading'
