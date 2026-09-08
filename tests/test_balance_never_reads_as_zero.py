"""A failed balance read must not report 0.00 and block every order.

`_balance_cache_inner` started at (0.0, 0.0) and was only ever filled by a SUCCESSFUL
fetch. The startup read went into RiskManager but not into that cache. So after any
restart, one failed fetch made the placement path see balance=0.00 and every symbol hit
skip_balance.

Observed on the server 2026-09-08 -- and only visible because the silent-exit
instrumentation had just shipped:

    13:10:22  RiskManager: real balance seeded -- balance=peak=3072.38 USDT
    13:15     REZUSDT  skip_balance  'balance=0.00 < margin=1.00'
    13:30     REZUSDT  skip_balance  'balance=0.00 < margin=1.00'

Two real orders refused for insufficient funds on an account holding 3072.38.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
LINES = MAIN.splitlines()


def _line_of(fragment: str) -> int:
    for i, l in enumerate(LINES):
        if fragment in l:
            return i
    raise AssertionError(f'not found: {fragment!r}')


def test_the_cache_is_declared_before_the_startup_read():
    """Priming it after the read would be a NameError; before, it is dead code."""
    assert _line_of('_balance_cache_inner: list') < _line_of('startup_balance = await')


def test_the_startup_read_primes_the_cache():
    i = _line_of('risk_manager.seed_real_balance(startup_balance)')
    block = '\n'.join(LINES[i:i + 10])
    assert '_balance_cache_inner[0] = (startup_balance' in block


def test_a_failed_fetch_falls_back_to_the_risk_manager_balance():
    """Its last-known-good, updated on every successful read and every trade close."""
    i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
    block = '\n'.join(LINES[i:i + 30])
    assert 'risk_manager.get_balance()' in block


def test_zero_is_never_returned_in_preference_to_a_known_balance():
    i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
    block = '\n'.join(LINES[i:i + 30])
    assert 'if cached_val > 0' in block, 'a zero cache must not short-circuit the fallback'
    assert not re.search(r'\n\s+return cached_val\s*\n\s*\n', block), \
        'unconditional `return cached_val` reintroduces the 0.00 path'


def test_the_ttl_cache_still_works():
    """The fix must not defeat the 60s TTL that keeps futures_account (weight 5) rare."""
    i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
    block = '\n'.join(LINES[i:i + 12])
    assert '_BALANCE_TTL' in block and 'return cached_val' in block


def test_a_successful_fetch_still_updates_the_cache():
    i = _line_of('cached_val, cached_ts = _balance_cache_inner[0]')
    block = '\n'.join(LINES[i:i + 30])
    assert '_balance_cache_inner[0] = (bal, now)' in block
