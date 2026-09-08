"""The balance cache must survive one whole candle batch.

futures_account is our most expensive call (weight 5, against 1 for klines), and the
balance it returns only moves when a position closes. Measured over 66 candle batches
on 2026-09-06: median 6.96s, p90 25.3s, max 27.8s. A 5s TTL expired mid-batch in 55%
of them, re-fetching an unchanged number several times per candle.

Widened again on 2026-09-08 to span a candle, paired with _balance_prefetch_loop(): see
test_the_balance_is_still_reread_at_least_once_per_candle below, and
tests/test_balance_prefetch_off_candle_boundary.py.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / 'main.py'

# Longest candle batch observed in production, 2026-09-06 (66 batches sampled).
OBSERVED_MAX_BATCH_S = 27.84
CANDLE_INTERVAL_S = 15 * 60


def _ttl() -> float:
    m = re.search(r'^\s*_BALANCE_TTL\s*=\s*([0-9.]+)', MAIN.read_text(), re.M)
    assert m, '_BALANCE_TTL not found in main.py'
    return float(m.group(1))


def test_ttl_outlasts_the_longest_observed_batch():
    """Otherwise the cache expires mid-batch and the same balance is fetched twice."""
    assert _ttl() > OBSERVED_MAX_BATCH_S


def test_ttl_has_margin_over_the_longest_batch():
    """Batches get slower as symbols are added; 2x keeps headroom for that."""
    assert _ttl() >= OBSERVED_MAX_BATCH_S * 2


def test_the_balance_is_still_reread_at_least_once_per_candle():
    """The requirement is unchanged; the mechanism moved.

    This used to be enforced by keeping the TTL well inside a candle. That had a cost:
    a short TTL guarantees the candle-boundary read misses the cache and goes to the
    network at exactly the moment every bot on the exchange reads its account -- which is
    where all 13 balance -1003 responses on 2026-09-08 landed (+0.45s..+0.92s past the
    boundary). _balance_prefetch_loop() now reads mid-candle, which both guarantees the
    once-per-candle refresh and keeps it off the congested instant.
    """
    src = MAIN.read_text()
    assert 'async def _balance_prefetch_loop' in src, \
        'nothing guarantees a per-candle refresh once the TTL exceeds a quarter candle'
    assert 'period / 2' in src, 'the pre-fetch must be mid-candle, not at the boundary'


def test_the_ttl_is_still_bounded():
    """A failed pre-fetch must expire the cache and cause a refetch, not serve forever."""
    assert _ttl() <= CANDLE_INTERVAL_S * 2


def test_uncached_read_still_exists_for_reporting():
    """Raising the TTL is only safe because closes bypass it: _read_wallet_now() reads
    fresh and refreshes this cache, so staleness is bounded by real account activity."""
    src = MAIN.read_text()
    assert 'async def _read_wallet_now' in src
    assert '_balance_cache_inner[0] = (bal, time.monotonic())' in src, \
        'a successful uncached read must refresh the shared cache'
