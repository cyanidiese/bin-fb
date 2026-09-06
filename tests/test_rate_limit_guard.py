"""The guard must stop us calling an endpoint that has banned us.

Calling while banned extends the ban. From the 2026-09-06 log:
    04:30:00  banned until 05:09
    04:30:01  banned until 05:35   (one second later, 26 minutes worse)
Bans left alone lasted ~4 minutes; bans we kept knocking on ran to 82.
"""
import time

import pytest

from bot.rate_limit_guard import (
    RateLimitGuard, RateLimited, looks_like_rate_limit, parse_ban_expiry_ms,
)

REAL = ("APIError(code=-1003): Way too many requests; IP(15.158.242.76) banned until "
        "1788591715275. Please use the websocket for live updates to avoid bans.")


@pytest.fixture
def g():
    return RateLimitGuard()


def test_parses_the_expiry_from_a_real_message():
    assert parse_ban_expiry_ms(REAL) == 1788591715275


def test_parse_returns_none_without_an_expiry():
    assert parse_ban_expiry_ms('APIError(code=-1003): Way too many requests') is None
    assert parse_ban_expiry_ms('') is None


@pytest.mark.parametrize('msg', [
    REAL,
    'APIError(code=-1003): Way too many requests',
    'APIError(code=-1015): Too many new orders',
    'HTTP 418 I am a teapot',
])
def test_recognises_rate_limit_messages(msg):
    assert looks_like_rate_limit(msg) is True


@pytest.mark.parametrize('msg', [
    'APIError(code=-2019): Margin is insufficient',
    'Connection reset by peer',
    'APIError(code=-4164): Order notional too small',
    '',
])
def test_ignores_unrelated_errors(msg):
    """An ordinary failure must never suppress traffic."""
    assert looks_like_rate_limit(msg) is False


def test_arms_from_a_ban_and_blocks(g):
    future_ms = int((time.time() + 120) * 1000)
    assert g.note_exception('testnet', Exception(f'-1003 banned until {future_ms}')) is True
    assert g.is_blocked('testnet') is True
    assert 100 < g.blocked_for('testnet') <= 120


def test_unrelated_error_does_not_arm(g):
    assert g.note_exception('testnet', Exception('APIError(code=-2019)')) is False
    assert g.is_blocked('testnet') is False


def test_expired_ban_does_not_block(g):
    past_ms = int((time.time() - 60) * 1000)
    g.note_exception('testnet', Exception(f'-1003 banned until {past_ms}'))
    assert g.is_blocked('testnet') is False


def test_endpoints_are_independent(g):
    """A testnet ban must not silence production."""
    future_ms = int((time.time() + 120) * 1000)
    g.note_exception('testnet', Exception(f'-1003 banned until {future_ms}'))
    assert g.is_blocked('testnet') is True
    assert g.is_blocked('production') is False


def test_message_without_expiry_uses_a_bounded_default(g):
    g.note_exception('testnet', Exception('APIError(code=-1003): Way too many requests'))
    assert 0 < g.blocked_for('testnet') <= 60


def test_absurd_expiry_is_capped(g):
    """A malformed message must not silence an endpoint for days."""
    g.note_exception('testnet', Exception(f'-1003 banned until {int((time.time()+10**7)*1000)}'))
    assert g.blocked_for('testnet') <= 3600


def test_a_nearer_expiry_never_shortens_an_active_block(g):
    far = int((time.time() + 600) * 1000)
    near = int((time.time() + 30) * 1000)
    g.note_exception('testnet', Exception(f'-1003 banned until {far}'))
    g.note_exception('testnet', Exception(f'-1003 banned until {near}'))
    assert g.blocked_for('testnet') > 500


def test_reset_clears(g):
    g.note_exception('testnet', Exception(f'-1003 banned until {int((time.time()+120)*1000)}'))
    g.reset('testnet')
    assert g.is_blocked('testnet') is False


def test_ratelimited_exception_carries_context():
    exc = RateLimited('testnet', 42.0)
    assert exc.key == 'testnet' and exc.remaining == 42.0
    assert 'extend the ban' in str(exc)
