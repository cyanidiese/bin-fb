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

# Binance's stated expiry is an upper bound, not a promise. Measured 2026-09-06: it
# reported a ban until 18:48:59, but futures_account already succeeded again at 18:07 —
# 41 minutes early. Blocking blindly for the stated window would have cost us balance
# and kline updates for nothing, so treat the endpoint as HALF-OPEN periodically and let
# exactly one probe through. If the probe works, clear the block early.
#
# The probe interval backs off, because a probe that fails does extend the real ban
# (~2 min per call, measured). Starting at 60s recovers a short ban quickly; the cap
# keeps a genuinely long ban from costing many wasted calls.
_PROBE_FIRST_S = 60.0
_PROBE_MAX_S = 600.0


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
        self._next_probe: dict[str, float] = {}      # key -> monotonic time of next probe
        self._probe_delay: dict[str, float] = {}     # key -> current backoff
        self._banned_until_wall: dict[str, float] = {}  # key -> epoch seconds, for messages
        self._notify = None                          # set via set_notifier()
        self._mode: str = ''                         # trading mode, for message context

    def set_notifier(self, notify, mode: str = '') -> None:
        """Register a callback used to announce ban start/end.

        Signature matches Notifier.notify(level, title, body, source).
        """
        self._notify = notify
        self._mode = mode

    def _announce(self, level: str, title: str, body: str) -> None:
        if self._notify is None:
            return
        try:
            self._notify(level, title, body, 'rate_limit_guard')
        except Exception as exc:  # never let a notification failure affect trading
            logger.debug(f"Rate-limit guard notification failed: {exc}")

    @staticmethod
    def _fmt_wall(epoch_s: float) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch_s, timezone.utc).strftime('%H:%M:%S UTC')

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

        now = time.monotonic()
        deadline = now + remaining
        was_blocked = self._blocked_until.get(key, 0.0) > now

        # A failed probe means the ban is still real: back the probe interval off so we
        # stop paying for attempts that only extend it.
        if was_blocked:
            self._probe_delay[key] = min(
                self._probe_delay.get(key, _PROBE_FIRST_S) * 2, _PROBE_MAX_S)
        else:
            self._probe_delay[key] = _PROBE_FIRST_S
        self._next_probe[key] = now + self._probe_delay[key]

        # Never shorten an existing block — a later message may report a nearer expiry
        # for a different endpoint while the longer one still stands.
        if deadline > self._blocked_until.get(key, 0.0):
            self._blocked_until[key] = deadline
            if expiry_ms is not None:
                self._banned_until_wall[key] = expiry_ms / 1000.0
            if self._announced.get(key) != deadline:
                self._announced[key] = deadline
                until = self._banned_until_wall.get(key)
                until_txt = self._fmt_wall(until) if until else 'unknown (assumed 60s)'
                logger.warning(
                    f"Rate-limit guard ARMED for '{key}': suppressing requests for "
                    f"{remaining:.0f}s (until {until_txt}). Calling while banned extends "
                    f"the ban, so we wait."
                )
                if not was_blocked:
                    self._announce(
                        'warning',
                        f"API ban started — {key}",
                        (f"Endpoint: <b>{key}</b>\n"
                         f"Trading mode: <b>{self._mode or 'unknown'}</b>\n"
                         f"Banned until: <b>{until_txt}</b> "
                         f"(~{remaining / 60:.0f} min)\n"
                         f"Reason: {msg[:160]}\n\n"
                         f"Kline and balance reads are paused — calling while banned "
                         f"extends it. Open positions keep their exchange stop-loss, and "
                         f"an exit that cannot execute is retried rather than recorded. "
                         f"The endpoint is re-probed periodically and will resume as soon "
                         f"as it actually works."),
                    )
        return True

    def blocked_for(self, key: str) -> float:
        """Seconds still to wait for this endpoint. 0.0 when clear.

        Returns 0.0 either when the stated ban has expired, or when it is time to send a
        single probe. The stated expiry is an upper bound — Binance lifted one 41 minutes
        early on 2026-09-06 — so we test rather than wait it out.
        """
        deadline = self._blocked_until.get(key)
        if deadline is None:
            return 0.0
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            self._clear(key, 'stated ban expired')
            return 0.0
        # Half-open: let exactly one request through, then reset the probe timer so the
        # next caller is blocked again until either it succeeds (clearing the block) or
        # note_exception re-arms with a longer backoff.
        next_probe = self._next_probe.get(key)
        if next_probe is not None and now >= next_probe:
            self._next_probe[key] = now + self._probe_delay.get(key, _PROBE_FIRST_S)
            logger.info(
                f"Rate-limit guard probing '{key}' (stated ban has "
                f"{remaining / 60:.0f} min left) — letting one request through"
            )
            return 0.0
        return remaining

    def note_success(self, key: str) -> None:
        """A request got through. If we thought we were banned, we were wrong — clear it."""
        if self._blocked_until.get(key):
            self._clear(key, 'probe succeeded — endpoint is working again')

    def _clear(self, key: str, reason: str) -> None:
        stated = self._banned_until_wall.pop(key, None)
        self._blocked_until.pop(key, None)
        self._announced.pop(key, None)
        self._next_probe.pop(key, None)
        self._probe_delay.pop(key, None)
        logger.info(f"Rate-limit guard CLEARED for '{key}' ({reason}) — resuming requests.")
        early = ''
        if stated and stated > time.time():
            early = (f"\nRecovered <b>{(stated - time.time()) / 60:.0f} min early</b> "
                     f"— Binance had stated {self._fmt_wall(stated)}.")
        self._announce(
            'info',
            f"API ban ended — {key}",
            (f"Endpoint: <b>{key}</b>\n"
             f"Trading mode: <b>{self._mode or 'unknown'}</b>\n"
             f"Resolution: {reason}{early}\n\n"
             f"Normal kline and balance reads have resumed."),
        )

    def is_blocked(self, key: str) -> bool:
        return self.blocked_for(key) > 0.0

    def reset(self, key: Optional[str] = None) -> None:
        """Clear state. Used by tests and on a deliberate mode switch."""
        if key is None:
            self._blocked_until.clear()
            self._announced.clear()
            self._next_probe.clear()
            self._probe_delay.clear()
            self._banned_until_wall.clear()
        else:
            self._blocked_until.pop(key, None)
            self._announced.pop(key, None)
            self._next_probe.pop(key, None)
            self._probe_delay.pop(key, None)
            self._banned_until_wall.pop(key, None)


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
