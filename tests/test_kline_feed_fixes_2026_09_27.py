"""Fixes from the 2026-09-27 kline investigation.

1. Production klines stream under /market/ — the root path connects but pushes nothing,
   so the live mirror ran on the REST watchdog since Sep 7 (~40% of candles skipped).
2. Kline cache writes are atomic and a torn/unreadable cache is never written over — the
   mirror's live caches were cut to ~100 candles that way.
3. Disabled symbols export their chart every candle (it froze at the last restart).
4. The Trades page orders table includes rank-1 orders, as Preset Efficiency counts them.
"""
import json
import threading
from pathlib import Path

import pytest

from bot import data_feed as df
from bot.data_feed import CacheUnreadable, DataFeed

ROOT = Path(__file__).resolve().parents[1]


# 1. stream URL ---------------------------------------------------------------

def test_live_combined_stream_uses_the_market_path():
    url = DataFeed.combined_stream_url(['SOLUSDT', 'INJUSDT'], '15m', testnet=False)
    assert url == 'wss://fstream.binance.com/market/stream?streams=solusdt@kline_15m/injusdt@kline_15m'


def test_testnet_stream_unchanged():
    url = DataFeed.combined_stream_url(['SOLUSDT'], '15m', testnet=True)
    assert url == 'wss://stream.binancefuture.com/stream?streams=solusdt@kline_15m'


# 2. cache integrity ----------------------------------------------------------

def _row(i: int) -> list:
    t = 1_790_000_000_000 + i * 900_000
    return [t, '1', '1', '1', '1', '1', t + 899_999, '0', 0, '0', '0', '0']


@pytest.fixture
def feed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = DataFeed.__new__(DataFeed)
    f._mode_suffix = 'live'
    f._klines_source = 'production'

    class S:
        kline_cache_limit = 5000
    f._settings = S()
    return f


def test_unreadable_cache_raises_instead_of_reading_empty(feed):
    p = feed._cache_path('SOLUSDT', '15m')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[[1790000000000, "1"')            # torn write
    with pytest.raises(CacheUnreadable):
        feed._read_cache(p)


def test_append_never_writes_over_an_unreadable_cache(feed):
    p = feed._cache_path('SOLUSDT', '15m')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[[1790000000000, "1"')
    feed.append_kline('SOLUSDT', '15m', _row(1))
    assert p.read_text() == '[[1790000000000, "1"'


def test_refresh_never_writes_over_an_unreadable_cache(feed):
    p = feed._cache_path('SOLUSDT', '15m')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[[1790000000000, "1"')
    feed._fetch = lambda *a, **k: [_row(i) for i in range(100)]
    assert feed.refresh_klines('SOLUSDT', '15m', 100) == []
    assert p.read_text() == '[[1790000000000, "1"'


def test_write_is_atomic_and_leaves_no_temp_files(feed, tmp_path):
    p = feed._cache_path('SOLUSDT', '15m')
    DataFeed._write_cache(p, [_row(0), _row(1)])
    assert len(json.loads(p.read_text())) == 2
    assert not list(p.parent.glob('.*.tmp'))


def test_concurrent_append_and_refresh_keep_the_history(feed):
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

def test_disabled_branch_exports_the_chart():
    src = (ROOT / 'main.py').read_text()
    branch = src[src.index('        if _sym_disabled:'):]
    branch = branch[:branch.index('\n            return\n')]
    assert 'export(' in branch


# 4. orders table includes rank 1 ----------------------------------------------

def test_orders_table_includes_rank1_like_preset_efficiency():
    page = (ROOT / 'dashboard/app/trades/page.tsx').read_text()
    block = page[page.index('const allRankOrdersFlat: RankOrder[] ='):][:200]
    assert 'fdata.rank1_orders' in block


def test_short_cache_is_backfilled_once_at_load(feed, monkeypatch):
    p = feed._cache_path('SOLUSDT', '15m')
    DataFeed._write_cache(p, [_row(i) for i in range(1400, 1500)])      # 100 candles
    calls = []

    def fetch(sym, tf, limit, start_ms=None):
        calls.append((limit, start_ms))
        return [_row(i) for i in range(0, 1500)] if start_ms is None else []
    feed._fetch = fetch
    monkeypatch.setattr(df, 'cache_is_current', lambda *a: True)
    rows = feed.load_klines('SOLUSDT', '15m', 1500)
    assert len(rows) == 1500 and calls[0] == (1500, None)
    calls.clear()
    feed.load_klines('SOLUSDT', '15m', 1500)
    assert calls == [], 'a full cache must not be re-fetched'
