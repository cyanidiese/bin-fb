"""During a ban, at most one request may be in flight — and one success must not
re-open the gate for everything.

Measured on the server 2026-09-07. Each call made while banned adds exactly 2 minutes
to the expiry, so leaked calls are the entire self-inflicted cost. Two defects let them
through:

1. note_success() cleared the block on ANY success, not just the probe we authorised:

     10:30:00.471  CLEARED (probe succeeded)
     10:30:01.541  ARMED  (597s)      <- a call failed
     10:30:01.660  CLEARED            <- 119ms later a stray success un-armed it
     10:30:01.730  klines failed again

2. A probe success fully opened the gate, and after a ban blackout every symbol has a
   kline gap, so main.py fires _refresh_klines_bg(stagger=0) for all 15 at once through
   asyncio.to_thread — 15 threads racing the probe gate:

     10:15:01.853  ARMED until 10:35:58
     10:15:01.856  ARMED until 10:39:58    <- +4 min
     10:15:01.865  ARMED until 10:39:58

Recovery is time-based on purpose. An earlier version of this fix required three
successful probes, which replay showed was strictly worse: between candle closes almost
nothing calls, so nothing arrived to consume the probe slots and recovery waited whole
candles — up to +1800s of suppression to save 4 minutes of extension.
"""
import threading
import time

import pytest

from bot.rate_limit_guard import RateLimitGuard, _SETTLE_S, _SETTLE_PROBE_S

KEY = 'testnet'


def _ban(g, key=KEY, secs=900):
    """Arm the guard the way a real -1003 does."""
    ms = int((time.time() + secs) * 1000)
    g.note_exception(key, Exception(
        f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until {ms}."))


def _probe(g, key=KEY):
    """Take the probe slot if one is on offer. True when a request is authorised."""
    return g.blocked_for(key) == 0.0


def _offer_probe(g, key=KEY):
    g._next_probe[key] = time.monotonic() - 1


# --------------------------------------------------------------------------- #
# 1. a stray success must not un-arm the guard                                #
# --------------------------------------------------------------------------- #

def test_a_stray_success_does_not_clear_an_armed_block():
    """The 10:30:01 flap: a success from a call the guard never authorised."""
    g = RateLimitGuard()
    _ban(g)
    assert g.is_blocked(KEY)
    g.note_success(KEY)
    assert g.is_blocked(KEY), 'a stray success un-armed the guard'


def test_a_stray_success_after_a_failed_probe_does_not_clear():
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    assert _probe(g)
    _ban(g)                       # the probe failed
    g.note_success(KEY)           # an unrelated success arrives
    assert g.is_blocked(KEY)


# --------------------------------------------------------------------------- #
# 2. a probe success starts settling, it does not open the gate               #
# --------------------------------------------------------------------------- #

def test_one_probe_success_does_not_open_the_gate():
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    assert _probe(g)
    g.note_success(KEY)
    assert g.is_blocked(KEY), 'one success opened the gate for the whole batch'
    assert KEY in g._settle_until, 'settling did not start'


def test_settling_completes_with_no_further_traffic_at_all():
    """The property the success-count design got wrong: recovery must not depend on
    another caller arriving."""
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    assert _probe(g)
    g.note_success(KEY)
    g._settle_until[KEY] = time.monotonic() - 0.01     # only time passes
    assert g.blocked_for(KEY) == 0.0
    assert not g.is_blocked(KEY), 'settling never completed on its own'


def test_a_failure_during_settling_cancels_it():
    """A rejection mid-settle means the ban is still real, so settling must not finish
    on the strength of the earlier success."""
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    _probe(g)
    g.note_success(KEY)
    assert KEY in g._settle_until
    _ban(g)
    assert KEY not in g._settle_until, 'settling survived a rejection'
    assert g.is_blocked(KEY)


def test_a_burst_arriving_during_settling_is_held_back():
    """The 10:15:01 stampede: 15 gap-refreshes land right after a probe succeeds."""
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    _probe(g)
    g.note_success(KEY)
    authorised = sum(1 for _ in range(15) if _probe(g))
    assert authorised == 0, f'{authorised} of 15 burst calls got through while settling'


def test_settling_is_short_enough_not_to_matter():
    """Against a 2-minute penalty per leaked call a few seconds is cheap; a few
    candles is not."""
    assert _SETTLE_S <= 10.0, f'settling would hold traffic for {_SETTLE_S}s'
    assert _SETTLE_PROBE_S <= _SETTLE_S


# --------------------------------------------------------------------------- #
# 3. single flight, including across threads                                  #
# --------------------------------------------------------------------------- #

def test_only_one_caller_is_authorised_per_probe_window():
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    authorised = [_probe(g) for _ in range(15)]
    assert sum(authorised) == 1, f'{sum(authorised)} of 15 callers got through'


def test_single_flight_holds_under_real_thread_contention():
    """main.py fires create_task(_refresh_klines_bg(stagger=0)) per symbol and each hops
    to a thread, so blocked_for() is genuinely called from 15 threads at once."""
    for attempt in range(30):
        g = RateLimitGuard()
        _ban(g)
        _offer_probe(g)
        results, barrier, lock = [], threading.Barrier(15), threading.Lock()

        def worker():
            barrier.wait()
            ok = _probe(g)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(15)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert sum(results) == 1, \
            f'attempt {attempt}: {sum(results)} of 15 threads authorised'


# --------------------------------------------------------------------------- #
# 4. is_blocked must not consume the probe                                    #
# --------------------------------------------------------------------------- #

def test_is_blocked_does_not_steal_the_probe_slot():
    """It used to call blocked_for(), so merely asking the question burned the probe a
    real caller needed."""
    g = RateLimitGuard()
    _ban(g)
    _offer_probe(g)
    for _ in range(5):
        assert g.is_blocked(KEY)
    assert _probe(g), 'is_blocked() consumed the probe slot'


# --------------------------------------------------------------------------- #
# 5. the property that matters: bounded calls while banned                    #
# --------------------------------------------------------------------------- #

def test_a_full_candle_batch_makes_no_call_while_banned():
    g = RateLimitGuard()
    _ban(g)
    calls = sum(1 for _ in range(16) if _probe(g))
    assert calls == 0, f'{calls} calls leaked immediately after arming'
