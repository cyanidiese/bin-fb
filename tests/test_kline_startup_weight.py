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
from bot.data_feed import update_fetch_limit

CANDLE_MS = 15 * 60 * 1000


def test_a_fresh_cache_needs_the_cheapest_tier():
    """Typical restart: the cache is minutes old."""
    assert update_fetch_limit(gap_ms=5 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 100


def test_a_day_old_cache_still_fits_the_cheapest_tier():
    """100 candles of 15m covers just over 25 hours."""
    assert update_fetch_limit(gap_ms=90 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 100


def test_a_wider_gap_steps_up_one_tier_only():
    got = update_fetch_limit(gap_ms=300 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500)
    assert got == 500, got


def test_a_very_old_cache_uses_the_full_limit():
    assert update_fetch_limit(gap_ms=5000 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 1500


def test_it_never_exceeds_the_caller_s_limit():
    for gap in (1, 100, 10_000):
        got = update_fetch_limit(gap_ms=gap * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=200)
        assert got <= 200, (gap, got)


def test_it_only_returns_binance_weight_tier_boundaries():
    """Anything between tiers costs the same as the tier above, so returning 137 would
    pay for 500 while fetching less. Only 100/500/1000/1500 are ever useful."""
    for gap in range(1, 2000, 7):
        got = update_fetch_limit(gap_ms=gap * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500)
        assert got in (100, 500, 1000, 1500), (gap, got)


def test_a_zero_or_negative_gap_is_safe():
    for gap in (0, -1, -CANDLE_MS):
        assert update_fetch_limit(gap_ms=gap, candle_ms=CANDLE_MS, max_limit=1500) == 100


def test_a_zero_candle_ms_falls_back_to_the_full_limit():
    """Never divide by zero; an unknown timeframe must not silently fetch 100."""
    assert update_fetch_limit(gap_ms=1000, candle_ms=0, max_limit=1500) == 1500


def test_the_margin_covers_a_boundary_gap():
    """A gap of exactly 100 candles must not ask for exactly 100 and lose the newest
    candle to an off-by-one."""
    assert update_fetch_limit(gap_ms=100 * CANDLE_MS, candle_ms=CANDLE_MS, max_limit=1500) == 500
