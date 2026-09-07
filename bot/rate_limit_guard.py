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
import threading
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

# A probe that works proves one request got through. It does not prove the ban has
# lifted for a burst of fifteen. Measured on the server 2026-09-07: after a probe
# succeeded and the block cleared, three kline fetches failed within 130ms and pushed
# the expiry out 4 minutes — because after a ban blackout every symbol has a kline gap,
# so main.py fires _refresh_klines_bg(stagger=0) for all 15 at once.
#
# So a successful probe starts a short SETTLING period instead of opening the gate: the
# block stays on, probes continue once a second, and the block lifts only if nothing
# fails for _SETTLE_S. A burst arriving during settling meets a closed gate.
#
# Deliberately time-based, not success-count-based. An earlier version required three
# successful probes and was strictly worse: between candle closes there is almost no
# traffic, so nothing arrived to consume the probe slots and recovery waited whole
# candles — replay measured up to +1800s of extra suppression to save 4 minutes of
# extension. Settling costs a fixed ~3s whether or not anything else is calling.
#
# Measured live on 2026-09-07 after the first deploy, which used 3.0s and still leaked:
#     13:15:00.86  settling starts (3s -> ends 13:15:03.86)
#     13:15:04.61  CLEARED
#     13:15:05.85  ARMED   <- kline burst landed 4.99s after settling started
#     13:15:05.98  ARMED      two calls, +4 min on the ban
# The burst is created by on_candle_close as create_task(_refresh_klines_bg, stagger=0)
# but only runs once the balance fetch and analyzer work have yielded, so it lands ~5s
# after the probe rather than immediately. 8s covers that with margin and still costs
# nothing when the ban has genuinely lifted.
_SETTLE_S = 8.0
_SETTLE_PROBE_S = 1.0


# Epoch timestamps Binance embeds in its error text. Bounded to a plausible window so
# an order id, a quantity or a price is never mistaken for a date: 13 digits is
# milliseconds, 10 is seconds.
_EPOCH_MS_RE = re.compile(r'\b(\d{13})\b')
_EPOCH_S_RE = re.compile(r'\b(\d{10})\b')
_PLAUSIBLE_FROM = 1577836800.0   # 2020-01-01
_PLAUSIBLE_TO = 2524608000.0     # 2050-01-01


def _fmt_wall(epoch_s: float) -> str:
    """An absolute instant, as a date and time in UTC.

    The date matters: a ban can cross midnight, and "00:41:58 UTC" on its own does not
    say which day it lifts.
    """
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch_s, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def humanize_epochs(text: str) -> str:
    """Rewrite epoch timestamps in an API message as readable UTC.

    Binance says "banned until 1788777598577", which tells a human reading a Telegram
    alert nothing at all. Only numbers that decode to a date between 2020 and 2050 are
    rewritten, so order ids, quantities and prices are left exactly as they are.
    """
    if not text:
        return ''

    def _sub(divisor):
        def inner(m):
            secs = int(m.group(1)) / divisor
            if _PLAUSIBLE_FROM <= secs <= _PLAUSIBLE_TO:
                return _fmt_wall(secs)
            return m.group(1)
        return inner

    text = _EPOCH_MS_RE.sub(_sub(1000.0), text)
    return _EPOCH_S_RE.sub(_sub(1.0), text)


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


# Titles the guard emits, parsed back on startup to spot a ban we never closed out.
_BAN_TITLE_RE = re.compile(r'API ban (started|ended)\s*[—-]\s*(\S+)')


def unresolved_ban_endpoints(entries) -> list[str]:
    """Endpoints whose most recent ban notification was a 'started' with no 'ended'.

    Guard state is in-memory, so a restart during a ban kills it before `_clear()` can
    send the "ban ended" alert. On 2026-09-07 the bot was restarted at 14:56 while a
    block was armed; the last alert anyone saw was "API ban started" at 14:30, for a ban
    that had expired around 14:48. It read as a 40-minute outage that was not happening,
    and every deploy during a ban would do the same.

    Sorted by timestamp rather than trusting append order, so a hand-edited or merged
    log cannot invert the result. Anything unparseable is ignored — this only ever adds
    an informational message, and getting it wrong must not be able to break startup.
    """
    latest: dict[str, tuple[str, str]] = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        title = e.get('title')
        if not isinstance(title, str):
            continue
        m = _BAN_TITLE_RE.search(title)
        if not m:
            continue
        state, key = m.group(1), m.group(2)
        ts = str(e.get('timestamp') or '')
        prev = latest.get(key)
        if prev is None or ts >= prev[0]:
            latest[key] = (ts, state)
    return sorted(k for k, (_ts, state) in latest.items() if state == 'started')


class RateLimitGuard:
    """Tracks, per endpoint key, how long we must stay away.

    One instance is shared process-wide (see `guard` below). Keys are arbitrary strings
    — we use the endpoint name ('testnet' / 'production') so a ban on one does not
    silence the other.
    """

    def __init__(self) -> None:
        # main.py fires create_task(_refresh_klines_bg(..., stagger=0)) per symbol and
        # each hops to a worker thread via asyncio.to_thread, so every method here can
        # be called concurrently from 15 threads. Without this lock two of them read
        # the same probe deadline before either advanced it, and both went to the
        # network — which is how three ARMED lines landed in the same 130ms.
        self._lock = threading.RLock()
        self._probe_inflight: dict[str, bool] = {}   # key -> a probe we authorised is out
        self._settle_until: dict[str, float] = {}    # key -> monotonic end of settling
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

        with self._lock:
            return self._arm(key, msg, expiry_ms, remaining)

    def _arm(self, key: str, msg: str, expiry_ms, remaining: float) -> bool:
        now = time.monotonic()
        deadline = now + remaining
        was_blocked = self._blocked_until.get(key, 0.0) > now

        # This request failed, so any probe we had outstanding is answered — and the
        # answer is no. Reset recovery progress: a success arriving after this must not
        # be counted towards re-opening, or a half-working endpoint would flap.
        self._probe_inflight[key] = False
        self._settle_until.pop(key, None)

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
                until_txt = _fmt_wall(until) if until else 'unknown (assumed 60s)'
                logger.warning(
                    f"Rate-limit guard ARMED for '{key}': suppressing requests for "
                    f"{remaining:.0f}s (until {until_txt}). Calling while banned extends "
                    f"the ban, so we wait."
                )
                if not was_blocked:
                    # Plain text only: Notifier.notify() html-escapes the body (so an
                    # API error containing '<' cannot break the message), which would
                    # render any markup here literally.
                    self._announce(
                        'warning',
                        f"API ban started — {key}",
                        (f"Endpoint:     {key}\n"
                         f"Trading mode: {self._mode or 'unknown'}\n"
                         f"Banned until: {until_txt}  (~{remaining / 60:.0f} min)\n"
                         f"Reason:       {humanize_epochs(msg)[:200]}\n\n"
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
        # Fast path without the lock: the overwhelming majority of calls are made when
        # nothing is banned, and taking a lock 15 times a candle for that is waste.
        # A dict .get() on CPython is atomic, and a stale read here only means one
        # request is made a moment after a ban started — which note_exception then
        # records.
        if self._blocked_until.get(key) is None:
            return 0.0

        with self._lock:
            deadline = self._blocked_until.get(key)
            if deadline is None:
                return 0.0
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                self._clear(key, 'stated ban expired')
                return 0.0

            # Settling finished with no further rejection: the endpoint really works.
            settle = self._settle_until.get(key)
            if settle is not None and now >= settle:
                self._clear(
                    key,
                    f'probe succeeded and {_SETTLE_S:.0f}s settled with no further '
                    f'rejection — endpoint is working again')
                return 0.0

            # Half-open, single flight. Authorising a probe advances the deadline
            # inside the lock, so of fifteen threads arriving together exactly one is
            # let through — previously two could both read the old deadline and both
            # go to the network.
            next_probe = self._next_probe.get(key)
            if next_probe is not None and now >= next_probe:
                settling = settle is not None
                # While settling we are testing whether the ban really lifted, so the
                # next test comes in a second rather than after the failure backoff.
                interval = (_SETTLE_PROBE_S if settling
                            else self._probe_delay.get(key, _PROBE_FIRST_S))
                self._next_probe[key] = now + interval
                self._probe_inflight[key] = True
                stage = (f" (settling, {settle - now:.0f}s to go)" if settling else "")
                logger.info(
                    f"Rate-limit guard probing '{key}' (stated ban has "
                    f"{remaining / 60:.0f} min left) — letting one request "
                    f"through{stage}"
                )
                return 0.0
            return remaining

    def note_success(self, key: str) -> None:
        """A request got through.

        Only counts towards re-opening when it answers a probe we authorised. A success
        from a call the guard never let through proves nothing about the ban — on
        2026-09-07 one of those un-armed the guard 119ms after it armed, and the next
        request extended the ban again.

        Even an authorised probe is not enough on its own. One request succeeding does
        not mean the endpoint will serve a batch of fifteen, so a success starts a
        short settling window instead of opening the gate; blocked_for() lifts the
        block once that window passes without a rejection.
        """
        # Fast path: nothing blocked, nothing to do. Keeps the normal case lock-free.
        if not self._blocked_until.get(key):
            return

        with self._lock:
            if not self._blocked_until.get(key):
                return
            if not self._probe_inflight.get(key):
                logger.debug(
                    f"Rate-limit guard: unauthorised success on '{key}' ignored — "
                    f"only a probe can re-open the endpoint"
                )
                return
            self._probe_inflight[key] = False
            now = time.monotonic()
            if key not in self._settle_until:
                # Begin settling. The gate stays shut so a 15-symbol burst cannot pile
                # in behind this one success; probes continue once a second, and
                # blocked_for() lifts the block once the window passes without a
                # rejection. Time-based, so it completes even if nothing else calls.
                self._settle_until[key] = now + _SETTLE_S
                self._next_probe[key] = now + _SETTLE_PROBE_S
                logger.info(
                    f"Rate-limit guard: probe succeeded on '{key}' — settling for "
                    f"{_SETTLE_S:.0f}s before resuming normal traffic"
                )
                return
            self._next_probe[key] = now + _SETTLE_PROBE_S

    def _clear(self, key: str, reason: str) -> None:
        stated = self._banned_until_wall.pop(key, None)
        self._blocked_until.pop(key, None)
        self._announced.pop(key, None)
        self._next_probe.pop(key, None)
        self._probe_delay.pop(key, None)
        self._probe_inflight.pop(key, None)
        self._settle_until.pop(key, None)
        logger.info(f"Rate-limit guard CLEARED for '{key}' ({reason}) — resuming requests.")
        early = ''
        if stated and stated > time.time():
            early = (f"\nRecovered {(stated - time.time()) / 60:.0f} min early — "
                     f"Binance had stated {_fmt_wall(stated)}.")
        self._announce(
            'info',
            f"API ban ended — {key}",
            (f"Endpoint:     {key}\n"
             f"Trading mode: {self._mode or 'unknown'}\n"
             f"Resolution:   {reason}{early}\n\n"
             f"Normal kline and balance reads have resumed."),
        )

    def is_blocked(self, key: str) -> bool:
        """Whether traffic is currently held back.

        Read-only, unlike blocked_for(): it never consumes the probe slot and never
        clears an expired ban. blocked_for() has to have those side effects — it is how
        a single request gets authorised — which makes it the wrong thing to call just
        to ask a question. Nothing in production calls this today; keeping it pure means
        adding a monitoring or dashboard read later cannot silently steal probes from
        the caller that actually needs one.
        """
        with self._lock:
            deadline = self._blocked_until.get(key)
            return deadline is not None and deadline > time.monotonic()

    def reset(self, key: Optional[str] = None) -> None:
        """Clear state. Used by tests and on a deliberate mode switch."""
        if key is None:
            self._blocked_until.clear()
            self._announced.clear()
            self._next_probe.clear()
            self._probe_delay.clear()
            self._banned_until_wall.clear()
            self._probe_inflight.clear()
            self._settle_until.clear()
        else:
            self._blocked_until.pop(key, None)
            self._announced.pop(key, None)
            self._next_probe.pop(key, None)
            self._probe_delay.pop(key, None)
            self._banned_until_wall.pop(key, None)
            self._probe_inflight.pop(key, None)
            self._settle_until.pop(key, None)


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
