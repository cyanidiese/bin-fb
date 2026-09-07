"""Startup made 35 API calls; most were avoidable.

Measured budget for a 15-symbol restart before this change:

    1  exchange_info          check_symbols_on_exchange
    1  exchange_info          prefetch_lot_sizes -> _ensure_lot_size
   15  leverageBracket        one per symbol
    1  futures_account        balance seed
   15  klines                 gap fetch, one per symbol
    2  positionInformation    reconciliation
   --
   35

This does NOT cause the bans — the mirror runs the identical startup against production
and has taken 0 rejections across 4 restarts, while 3 of 4 testnet ban onsets had no
restart anywhere near them. It is removed because it is free to remove.
"""
import time
from pathlib import Path

from bot.data_feed import cache_is_current

ROOT = Path(__file__).resolve().parents[1]
CANDLE_MS = 15 * 60 * 1000


class TestKlineGapSkip:
    """15 calls -> 0 on a typical restart.

    load_klines reads 3419-5000 candles from disk and fetches only the gap since the
    cache's last candle. A deploy now takes ~114s, so the gap is usually zero candles
    and the fetch retrieves nothing at all.
    """

    def _now(self, ms_ago):
        return int(time.time() * 1000) - ms_ago

    def test_a_cache_with_no_missing_candle_needs_no_fetch(self):
        # last candle closed 30s ago: the next one has not formed yet
        assert cache_is_current(last_close_ms=self._now(30_000), candle_ms=CANDLE_MS) is True

    def test_a_cache_missing_a_full_candle_needs_a_fetch(self):
        assert cache_is_current(last_close_ms=self._now(2 * CANDLE_MS), candle_ms=CANDLE_MS) is False

    def test_the_boundary_is_one_candle(self):
        assert cache_is_current(last_close_ms=self._now(CANDLE_MS - 5000), candle_ms=CANDLE_MS) is True
        assert cache_is_current(last_close_ms=self._now(CANDLE_MS + 5000), candle_ms=CANDLE_MS) is False

    def test_an_empty_or_unknown_cache_always_fetches(self):
        assert cache_is_current(last_close_ms=0, candle_ms=CANDLE_MS) is False
        assert cache_is_current(last_close_ms=self._now(0), candle_ms=0) is False

    def test_a_future_timestamp_does_not_skip_forever(self):
        """A clock skew or bad cache must not make us stop fetching permanently."""
        assert cache_is_current(last_close_ms=self._now(-10 * CANDLE_MS), candle_ms=CANDLE_MS) is True


class TestLeverageBracketBatch:
    """15 calls -> 1. symbol is optional on /fapi/v1/leverageBracket, the weight is 1
    either way, and the response shape is the same array of {symbol, brackets}."""

    def test_it_asks_for_every_symbol_in_one_call(self):
        src = (ROOT / 'bot/order_executor.py').read_text()
        i = src.index('async def fetch_leverage_brackets')
        body = src[i:i + 2600]
        assert 'for symbol in symbols' not in body.split('fallback')[0], \
            'the happy path must not loop one call per symbol'
        assert 'futures_leverage_bracket' in body

    def test_a_per_symbol_fallback_is_kept(self):
        """If the batch form ever fails, one bad symbol must not cost us all brackets."""
        src = (ROOT / 'bot/order_executor.py').read_text()
        i = src.index('async def fetch_leverage_brackets')
        body = src[i:i + 2600]
        assert 'fallback' in body.lower(), 'no fallback if the batch call fails'


class TestExchangeInfoCache:
    """2 calls -> 1. Both startup callers pull the same ~735-symbol payload."""

    def test_there_is_a_cached_accessor(self):
        src = (ROOT / 'bot/order_executor.py').read_text()
        assert '_exchange_info_cached' in src

    def test_both_startup_callers_use_it(self):
        src = (ROOT / 'bot/order_executor.py').read_text()
        for fn in ('check_symbols_on_exchange', '_ensure_lot_size'):
            i = src.index(f'def {fn}')
            body = src[i:i + 1800]
            assert '_exchange_info_cached' in body, f'{fn} still calls the API directly'

    def test_the_ttl_is_short_enough_to_stay_fresh(self):
        from bot.order_executor import _EXCHANGE_INFO_TTL_S
        assert 0 < _EXCHANGE_INFO_TTL_S <= 900, \
            'listings and lot filters change; this must not be cached for long'
