"""Stop calling an endpoint that has told us we are banned.

Binance answers a rate-limit ban with -1003 (or HTTP 418) and states exactly when the
ban lifts: "Way too many requests; IP(x) banned until 1788591715275". We ignored that
and kept issuing scheduled calls every candle — and each call while banned pushes the
expiry further out. From the 2026-09-06 log:

    04:30:00  banned until 05:09
    04:30:01  banned until 05:35   <- one second later, 26 minutes worse

Bans left alone lasted about 4 minutes. Bans we kept knocking on ran to 82. Over that
day, ~4.5 of 16.5 hours were degraded.

This guard is damage control, not prevention. Our own consumption is 1-3 request-weight
against a 6000/min limit, and the IP Binance names (15.158.242.x) is a shared CloudFront
edge rather than our egress address (185.237.14.105) — so we are not the cause and
cannot prevent the ban by trimming calls. What we can do is stop making it worse.

Deliberately NOT applied to order placement. Placing an order is rare and high-value; if
it fails it fails loudly through paths that already handle it. This guards the two
high-frequency read paths (klines, account balance) that account for every ban error in
the log.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# "banned until 1788591715275" — epoch milliseconds.
_BANNED_UNTIL_RE = re.compile(r'banned until (\d{10,16})')

# Used when the endpoint says we are banned but not until when. Short on purpose: a
# wrong guess that is too long would suppress a working endpoint, and the next real
# error will re-arm the guard anyway.
_DEFAULT_BLOCK_S = 60.0

# Never trust an absurd expiry from a malformed message.
_MAX_BLOCK_S = 3600.0


def parse_ban_expiry_ms(message: str) -> Optional[int]:
    """Epoch-ms the ban lifts, or None when the message carries no expiry."""
    m = _BANNED_UNTIL_RE.search(message or '')
    return int(m.group(1)) if m else None


def looks_like_rate_limit(message: str) -> bool:
    """True when the endpoint is telling us we are rate limited or banned.

    -1003 is the documented code; 418 is what Binance returns once the ban is active;
    -1015 is the order-rate variant. Matching on the text as well means an unexpected
    wrapper exception is still recognised.
    """
    if not message:
        return False
    low = message.lower()
    return (
        '-1003' in message
        or '-1015' in message
        or 'too many requests' in low
        or 'banned until' in low
        or 'ip(' in low and 'banned' in low
        or '418' in message and 'teapot' in low
    )


class RateLimitGuard:
    """Tracks, per endpoint key, how long we must stay away.

    One instance is shared process-wide (see `guard` below). Keys are arbitrary strings
    — we use the endpoint name ('testnet' / 'production') so a ban on one does not
    silence the other.
    """

    def __init__(self) -> None:
        self._blocked_until: dict[str, float] = {}   # key -> monotonic deadline
        self._announced: dict[str, float] = {}       # key -> deadline already logged

    def note_exception(self, key: str, exc: BaseException) -> bool:
        """Record a ban if `exc` is one. Returns True when the guard armed.

        Any other exception is ignored, so ordinary errors never suppress traffic.
        """
        msg = str(exc)
        if not looks_like_rate_limit(msg):
            return False

        expiry_ms = parse_ban_expiry_ms(msg)
        if expiry_ms is not None:
            # Binance states the expiry in server time; convert to a local duration
            # rather than trusting our clock to agree with theirs.
            remaining = expiry_ms / 1000.0 - time.time()
        else:
            remaining = _DEFAULT_BLOCK_S

        remaining = max(0.0, min(remaining, _MAX_BLOCK_S))
        if remaining <= 0:
            return False

        deadline = time.monotonic() + remaining
        # Never shorten an existing block — a later message may report a nearer expiry
        # for a different endpoint while the longer one still stands.
        if deadline > self._blocked_until.get(key, 0.0):
            self._blocked_until[key] = deadline
            if self._announced.get(key) != deadline:
                self._announced[key] = deadline
                logger.warning(
                    f"Rate-limit guard ARMED for '{key}': suppressing requests for "
                    f"{remaining:.0f}s. Calling while banned extends the ban, so we wait."
                )
        return True

    def blocked_for(self, key: str) -> float:
        """Seconds still to wait for this endpoint. 0.0 when clear."""
        deadline = self._blocked_until.get(key)
        if deadline is None:
            return 0.0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            del self._blocked_until[key]
            self._announced.pop(key, None)
            logger.info(f"Rate-limit guard CLEARED for '{key}' — resuming requests.")
            return 0.0
        return remaining

    def is_blocked(self, key: str) -> bool:
        return self.blocked_for(key) > 0.0

    def reset(self, key: Optional[str] = None) -> None:
        """Clear state. Used by tests and on a deliberate mode switch."""
        if key is None:
            self._blocked_until.clear()
            self._announced.clear()
        else:
            self._blocked_until.pop(key, None)
            self._announced.pop(key, None)


class RateLimited(Exception):
    """Raised instead of making a call we know the endpoint will reject.

    Callers already handle exceptions from these paths, so raising keeps their error
    handling intact while skipping the network round-trip that would extend the ban.
    """

    def __init__(self, key: str, remaining: float) -> None:
        super().__init__(
            f"Skipping request: '{key}' is rate-limit banned for another "
            f"{remaining:.0f}s (calling now would extend the ban)"
        )
        self.key = key
        self.remaining = remaining


# Process-wide instance.
guard = RateLimitGuard()
