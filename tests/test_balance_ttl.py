"""The balance cache must survive one whole candle batch.

futures_account is our most expensive call (weight 5, against 1 for klines), and the
balance it returns only moves when a position closes. Measured over 66 candle batches
on 2026-09-06: median 6.96s, p90 25.3s, max 27.8s. A 5s TTL expired mid-batch in 55%
of them, re-fetching an unchanged number several times per candle.
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


def test_ttl_stays_well_inside_one_candle():
    """A TTL near the candle interval would let a whole candle pass on a stale balance.
    The balance must still be re-read at least once per candle."""
    assert _ttl() < CANDLE_INTERVAL_S / 4


def test_uncached_read_still_exists_for_reporting():
    """Raising the TTL is only safe because closes bypass it: _read_wallet_now() reads
    fresh and refreshes this cache, so staleness is bounded by real account activity."""
    src = MAIN.read_text()
    assert 'async def _read_wallet_now' in src
    assert '_balance_cache_inner[0] = (bal, time.monotonic())' in src, \
        'a successful uncached read must refresh the shared cache'
