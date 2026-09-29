"""Rate-limit guard: arming, probing, recovery, ban persistence and alerts, and the call
sites it protects.

Sections (one class per original test module):
  TestRateLimitIntegration              the guard really stops the network call
  TestRateLimitGuard                    parsing, arming, capping, endpoint independence
  TestRateLimitProbeAndAlerts           half-open probing and ban start/end notifications
  TestRateLimitStagedRecovery           single flight during a ban, time-based settling
  TestProbeWaitsOutTheBan               no probing until most of the stated ban elapsed
  TestBanWindowNotClampedToAnHour       multi-hour bans are covered in full
  TestBanStatePersisted                 ban expiry survives a restart
  TestBanAlertClosure                   a restart closes a dangling "ban started" alert
  TestBanAlertNotReannounced            one ban, one "ban started" alert
  TestBanMessageFormatting              alert text: no markup, no raw epochs
  TestWatchdogRateLimitGuard            watchdogs make no calls while banned
  TestKlineRefreshRetriesAfterSettling  gap refresh retried after the settling window
  TestTestnetRestHost                   test-mode REST goes to demo-fapi
"""
import asyncio
import dataclasses
import html
import json
import re
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from bot import rate_limit_guard as rlg
from bot.data_feed import _FUTURES_REST_TESTNET, DataFeed, _trading_client
from bot.rate_limit_guard import (
    _MAX_BLOCK_S, _PROBE_AFTER_FRAC, _PROBE_FIRST_S, _SETTLE_PROBE_S, _SETTLE_S,
    RateLimited, RateLimitGuard, _fmt_wall, guard, humanize_epochs,
    looks_like_rate_limit, parse_ban_expiry_ms, unresolved_ban_endpoints,
)
from bot.rate_limit_guard import guard as rl_guard
from config.settings import load_settings
from tests.factories import src, run_coro


# ── test_rate_limit_integration.py ────────────────────────────────────

_run = run_coro  # shared order-safe loop helper (tests/factories.py)


BAN = ("APIError(code=-1003): Way too many requests; IP(15.158.242.76) banned until "
       "{ms}. Please use the websocket for live updates to avoid bans.")


def _feed():
    s = dataclasses.replace(load_settings(), trading_mode='test',
                            api_key='k', api_secret='s')
    with patch('bot.data_feed.Client', side_effect=lambda *a, **k: MagicMock()):
        return DataFeed(s, live_klines=False)


class TestRateLimitIntegration:
    """The guard must actually stop the network call, not just record state."""

    @pytest.fixture(autouse=True)
    def clean(self):
        guard.reset()
        yield
        guard.reset()

    def test_a_ban_arms_the_guard_and_the_next_fetch_skips_the_network(self):
        f = _feed()
        future_ms = int((time.time() + 300) * 1000)
        f._klines_client.futures_klines.side_effect = Exception(BAN.format(ms=future_ms))

        with pytest.raises(Exception):
            f._fetch('EIGENUSDT', '15m', limit=20)
        assert f._klines_client.futures_klines.call_count == 1
        assert guard.is_blocked('testnet') is True

        # The second attempt must not reach the API — that call is what extends the ban.
        with pytest.raises(RateLimited):
            f._fetch('EIGENUSDT', '15m', limit=20)
        assert f._klines_client.futures_klines.call_count == 1, \
            'a banned endpoint was called again — this is what turned 4-minute bans into 82'

    def test_ordinary_errors_do_not_suppress_later_fetches(self):
        """Only rate-limit errors arm the guard; a normal failure must not stop trading."""
        f = _feed()
        f._klines_client.futures_klines.side_effect = Exception('APIError(code=-2019)')
        for _ in range(3):
            with pytest.raises(Exception):
                f._fetch('EIGENUSDT', '15m', limit=20)
        assert f._klines_client.futures_klines.call_count == 3
        assert guard.is_blocked('testnet') is False

    def test_balance_fetch_skips_the_call_while_banned(self):
        from bot.order_executor import OrderExecutor

        ex = OrderExecutor.__new__(OrderExecutor)
        ex._feed = MagicMock()
        ex._feed._is_testnet = True
        ex._QUOTE_ASSET = 'USDT'

        guard.note_exception('testnet', Exception(BAN.format(ms=int((time.time() + 300) * 1000))))
        assert _run(ex.fetch_account_balance()) == 0.0
        ex._feed.client.futures_account.assert_not_called()

    def test_balance_fetch_works_normally_when_clear(self):
        from bot.order_executor import OrderExecutor

        ex = OrderExecutor.__new__(OrderExecutor)
        ex._feed = MagicMock()
        ex._feed._is_testnet = True
        ex._QUOTE_ASSET = 'USDT'
        ex._feed.client.futures_account.return_value = {
            'assets': [{'asset': 'USDT', 'walletBalance': '3142.50'}]
        }
        assert _run(ex.fetch_account_balance()) == pytest.approx(3142.50)


# ── test_rate_limit_guard.py ──────────────────────────────────────────

REAL = ("APIError(code=-1003): Way too many requests; IP(15.158.242.76) banned until "
        "1788591715275. Please use the websocket for live updates to avoid bans.")


class TestRateLimitGuard:
    """The guard must stop us calling an endpoint that has banned us.

    Calling while banned extends the ban. From the 2026-09-06 log:
        04:30:00  banned until 05:09
        04:30:01  banned until 05:35   (one second later, 26 minutes worse)
    Bans left alone lasted ~4 minutes; bans we kept knocking on ran to 82.
    """

    @pytest.fixture
    def g(self):
        return RateLimitGuard()

    def test_parses_the_expiry_from_a_real_message(self):
        assert parse_ban_expiry_ms(REAL) == 1788591715275

    def test_parse_returns_none_without_an_expiry(self):
        assert parse_ban_expiry_ms('APIError(code=-1003): Way too many requests') is None
        assert parse_ban_expiry_ms('') is None

    @pytest.mark.parametrize('msg', [
        REAL,
        'APIError(code=-1003): Way too many requests',
        'APIError(code=-1015): Too many new orders',
        'HTTP 418 I am a teapot',
    ])
    def test_recognises_rate_limit_messages(self, msg):
        assert looks_like_rate_limit(msg) is True

    @pytest.mark.parametrize('msg', [
        'APIError(code=-2019): Margin is insufficient',
        'Connection reset by peer',
        'APIError(code=-4164): Order notional too small',
        '',
    ])
    def test_ignores_unrelated_errors(self, msg):
        """An ordinary failure must never suppress traffic."""
        assert looks_like_rate_limit(msg) is False

    def test_arms_from_a_ban_and_blocks(self, g):
        future_ms = int((time.time() + 120) * 1000)
        assert g.note_exception('testnet', Exception(f'-1003 banned until {future_ms}')) is True
        assert g.is_blocked('testnet') is True
        assert 100 < g.blocked_for('testnet') <= 120

    def test_unrelated_error_does_not_arm(self, g):
        assert g.note_exception('testnet', Exception('APIError(code=-2019)')) is False
        assert g.is_blocked('testnet') is False

    def test_expired_ban_does_not_block(self, g):
        past_ms = int((time.time() - 60) * 1000)
        g.note_exception('testnet', Exception(f'-1003 banned until {past_ms}'))
        assert g.is_blocked('testnet') is False

    def test_endpoints_are_independent(self, g):
        """A testnet ban must not silence production."""
        future_ms = int((time.time() + 120) * 1000)
        g.note_exception('testnet', Exception(f'-1003 banned until {future_ms}'))
        assert g.is_blocked('testnet') is True
        assert g.is_blocked('production') is False

    def test_message_without_expiry_uses_a_bounded_default(self, g):
        g.note_exception('testnet', Exception('APIError(code=-1003): Way too many requests'))
        assert 0 < g.blocked_for('testnet') <= 60

    def test_absurd_expiry_is_capped(self, g):
        """A malformed message must not silence an endpoint for days.

        Asserted against the constant rather than a literal: _MAX_BLOCK_S is a sanity
        ceiling, not a policy on how long to wait, and it was raised from 1h to 6h on
        2026-09-09 because it was also clamping the multi-hour bans Binance really issues.
        The second assertion is what actually pins this test's intent.
        """
        g.note_exception('testnet', Exception(f'-1003 banned until {int((time.time()+10**7)*1000)}'))
        assert g.blocked_for('testnet') <= _MAX_BLOCK_S + 1
        assert _MAX_BLOCK_S < 86400, 'the cap has drifted into "days" — the thing it forbids'

    def test_a_nearer_expiry_never_shortens_an_active_block(self, g):
        far = int((time.time() + 600) * 1000)
        near = int((time.time() + 30) * 1000)
        g.note_exception('testnet', Exception(f'-1003 banned until {far}'))
        g.note_exception('testnet', Exception(f'-1003 banned until {near}'))
        assert g.blocked_for('testnet') > 500

    def test_reset_clears(self, g):
        g.note_exception('testnet', Exception(f'-1003 banned until {int((time.time()+120)*1000)}'))
        g.reset('testnet')
        assert g.is_blocked('testnet') is False

    def test_ratelimited_exception_carries_context(self):
        exc = RateLimited('testnet', 42.0)
        assert exc.key == 'testnet' and exc.remaining == 42.0
        assert 'extend the ban' in str(exc)


# ── test_rate_limit_probe_and_alerts.py ───────────────────────────────

def _ban(seconds_ahead: float) -> Exception:
    ms = int((time.time() + seconds_ahead) * 1000)
    return Exception(f"APIError(code=-1003): Way too many requests; "
                     f"IP(15.158.242.76) banned until {ms}.")


def _to_probe_window(clock, ban_secs: float) -> None:
    """Advance to where a probe becomes permissible.

    Since 2026-09-08 probing does not begin until _PROBE_AFTER_FRAC of the stated ban has
    elapsed: a probe fired minutes into an hour-long ban proves only that the CloudFront
    edge it reached is clear, and the traffic that follows walks into a banned edge and
    extends it (+336 min measured in one day). These tests are about what happens once a
    probe IS offered; the gate itself is covered in TestProbeWaitsOutTheBan.
    """
    clock['t'] += ban_secs * rlg._PROBE_AFTER_FRAC + rlg._PROBE_FIRST_S + 1


def _recover(g, clock, key='testnet'):
    """Drive a full recovery: one authorised probe that works, then settling elapses.

    A success no longer clears the block on its own — it starts a settling window, and
    blocked_for() lifts the block once that window passes with no further rejection.
    Settling is time-based, so nothing else has to call for it to finish.
    """
    _to_probe_window(clock, 3000)
    assert g.blocked_for(key) == 0.0, 'no probe slot offered'
    g.note_success(key)
    clock['t'] += rlg._SETTLE_S + 1
    assert g.blocked_for(key) == 0.0, 'settling did not complete'


# ── notifications ────────────────────────────────────────────────────────────

def _capture(g, mode='test'):
    sent = []
    g.set_notifier(lambda lvl, title, body, src: sent.append((lvl, title, body, src)), mode=mode)
    return sent


class TestRateLimitProbeAndAlerts:
    """The stated ban expiry is an upper bound, and bans must be announced.

    Measured 2026-09-06: Binance reported a ban until 18:48:59, but futures_account
    already succeeded at 18:07 — 41 minutes early. Waiting out the stated window would
    have paused balance and kline reads for nothing, so the guard probes periodically.
    """

    @pytest.fixture
    def g(self):
        return RateLimitGuard()

    @pytest.fixture
    def clock(self, monkeypatch):
        """Controllable monotonic clock."""
        state = {'t': 10_000.0}
        monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
        return state

    # ── half-open probing ────────────────────────────────────────────────────────

    def test_blocks_immediately_after_a_ban(self, g, clock):
        g.note_exception('testnet', _ban(3000))
        assert g.blocked_for('testnet') > 0

    def test_lets_one_probe_through_after_the_probe_interval(self, g, clock):
        g.note_exception('testnet', _ban(3000))
        _to_probe_window(clock, 3000)
        assert g.blocked_for('testnet') == 0.0, 'no probe was allowed'
        # and the very next caller is blocked again — one probe, not an open floodgate
        assert g.blocked_for('testnet') > 0

    def test_successful_probes_clear_the_block_early(self, g, clock):
        g.note_exception('testnet', _ban(3000))
        _recover(g, clock)
        assert g.is_blocked('testnet') is False

    def test_a_single_successful_probe_is_not_enough(self, g, clock):
        """The stampede guard: one success must not re-open the endpoint for 15 symbols."""
        g.note_exception('testnet', _ban(3000))
        _to_probe_window(clock, 3000)
        assert g.blocked_for('testnet') == 0.0
        g.note_success('testnet')
        assert g.is_blocked('testnet') is True

    def test_failed_probe_backs_off(self, g, clock):
        g.note_exception('testnet', _ban(3000))
        first = g._probe_delay['testnet']
        clock['t'] += first + 1
        g.blocked_for('testnet')
        g.note_exception('testnet', _ban(3000))   # probe failed
        assert g._probe_delay['testnet'] == first * 2

    def test_backoff_is_capped(self, g, clock):
        g.note_exception('testnet', _ban(3600))
        for _ in range(20):
            clock['t'] += g._probe_delay['testnet'] + 1
            g.blocked_for('testnet')
            g.note_exception('testnet', _ban(3600))
        assert g._probe_delay['testnet'] <= rlg._PROBE_MAX_S

    def test_note_success_on_a_clear_endpoint_is_harmless(self, g, clock):
        g.note_success('testnet')
        assert g.is_blocked('testnet') is False

    def test_messages_carry_no_html_markup(self, g, clock):
        """Notifier.notify() html-escapes the body, so markup here renders literally."""
        sent = _capture(g)
        g.note_exception('testnet', _ban(3000))
        _recover(g, clock)
        assert len(sent) == 2
        for _lvl, title, body, _src in sent:
            for tag in ('<b>', '</b>', '<i>', '<code>', '&lt;'):
                assert tag not in body, f'{tag!r} would render literally in Telegram'
                assert tag not in title

    def test_ban_start_is_announced_with_expiry_and_mode(self, g, clock):
        sent = _capture(g)
        g.note_exception('testnet', _ban(3000))
        assert len(sent) == 1
        lvl, title, body, src = sent[0]
        assert lvl == 'warning'
        assert 'testnet' in title
        assert 'Banned until' in body and 'UTC' in body, 'must say until when'
        assert 'Trading mode' in body and 'test' in body, 'must say the mode'
        assert '50 min' in body or 'min' in body
        assert src == 'rate_limit_guard'

    def test_ban_end_is_announced(self, g, clock):
        sent = _capture(g)
        g.note_exception('testnet', _ban(3000))
        _recover(g, clock)
        assert len(sent) == 2
        lvl, title, body, _ = sent[1]
        assert lvl == 'info'
        assert 'ended' in title.lower()
        assert 'early' in body, 'should report recovering before the stated expiry'

    def test_only_one_start_alert_per_ban(self, g, clock):
        """A ban spans many candles — one alert, not one per blocked call."""
        sent = _capture(g)
        for _ in range(5):
            g.note_exception('testnet', _ban(3000))
            clock['t'] += 1
        assert sum(1 for s in sent if 'started' in s[1].lower()) == 1

    def test_expiry_lapse_also_announces_the_end(self, g, clock):
        sent = _capture(g)
        g.note_exception('testnet', _ban(120))
        clock['t'] += 200
        g.blocked_for('testnet')
        assert any('ended' in s[1].lower() for s in sent)

    def test_unrelated_error_announces_nothing(self, g, clock):
        sent = _capture(g)
        g.note_exception('testnet', Exception('APIError(code=-2019): Margin is insufficient'))
        assert sent == []

    def test_a_broken_notifier_never_breaks_trading(self, g, clock):
        def boom(*a, **k):
            raise RuntimeError('telegram down')
        g.set_notifier(boom, mode='test')
        g.note_exception('testnet', _ban(3000))     # must not raise
        assert g.is_blocked('testnet') is True


# ── test_rate_limit_staged_recovery.py ────────────────────────────────

KEY = 'testnet'


def _staged_ban(g, key=KEY, secs=900):
    """Arm the guard the way a real -1003 does."""
    ms = int((time.time() + secs) * 1000)
    g.note_exception(key, Exception(
        f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until {ms}."))


def _probe(g, key=KEY):
    """Take the probe slot if one is on offer. True when a request is authorised."""
    return g.blocked_for(key) == 0.0


def _offer_probe(g, key=KEY):
    """Make a probe available now.

    Two gates have to be satisfied since 2026-09-08: the backoff timer, and
    _probe_not_before — probing no longer starts until most of the stated ban has
    elapsed. These tests are about what happens *once* a probe is offered, so they
    fast-forward both rather than re-testing the gate. The gate itself is covered in
    TestProbeWaitsOutTheBan.
    """
    g._next_probe[key] = time.monotonic() - 1
    g._probe_not_before[key] = time.monotonic() - 1


class TestRateLimitStagedRecovery:
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

    # --------------------------------------------------------------------------- #
    # 1. a stray success must not un-arm the guard                                #
    # --------------------------------------------------------------------------- #

    def test_a_stray_success_does_not_clear_an_armed_block(self):
        """The 10:30:01 flap: a success from a call the guard never authorised."""
        g = RateLimitGuard()
        _staged_ban(g)
        assert g.is_blocked(KEY)
        g.note_success(KEY)
        assert g.is_blocked(KEY), 'a stray success un-armed the guard'

    def test_a_stray_success_after_a_failed_probe_does_not_clear(self):
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        assert _probe(g)
        _staged_ban(g)                       # the probe failed
        g.note_success(KEY)           # an unrelated success arrives
        assert g.is_blocked(KEY)

    # --------------------------------------------------------------------------- #
    # 2. a probe success starts settling, it does not open the gate               #
    # --------------------------------------------------------------------------- #

    def test_one_probe_success_does_not_open_the_gate(self):
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        assert _probe(g)
        g.note_success(KEY)
        assert g.is_blocked(KEY), 'one success opened the gate for the whole batch'
        assert KEY in g._settle_until, 'settling did not start'

    def test_settling_completes_with_no_further_traffic_at_all(self):
        """The property the success-count design got wrong: recovery must not depend on
        another caller arriving."""
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        assert _probe(g)
        g.note_success(KEY)
        g._settle_until[KEY] = time.monotonic() - 0.01     # only time passes
        assert g.blocked_for(KEY) == 0.0
        assert not g.is_blocked(KEY), 'settling never completed on its own'

    def test_a_failure_during_settling_cancels_it(self):
        """A rejection mid-settle means the ban is still real, so settling must not finish
        on the strength of the earlier success."""
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        _probe(g)
        g.note_success(KEY)
        assert KEY in g._settle_until
        _staged_ban(g)
        assert KEY not in g._settle_until, 'settling survived a rejection'
        assert g.is_blocked(KEY)

    def test_a_burst_arriving_during_settling_is_held_back(self):
        """The 10:15:01 stampede: 15 gap-refreshes land right after a probe succeeds."""
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        _probe(g)
        g.note_success(KEY)
        authorised = sum(1 for _ in range(15) if _probe(g))
        assert authorised == 0, f'{authorised} of 15 burst calls got through while settling'

    def test_settling_is_short_enough_not_to_matter(self):
        """Against a 2-minute penalty per leaked call a few seconds is cheap; a few
        candles is not."""
        assert _SETTLE_S <= 10.0, f'settling would hold traffic for {_SETTLE_S}s'
        assert _SETTLE_PROBE_S <= _SETTLE_S

    # --------------------------------------------------------------------------- #
    # 3. single flight, including across threads                                  #
    # --------------------------------------------------------------------------- #

    def test_only_one_caller_is_authorised_per_probe_window(self):
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        authorised = [_probe(g) for _ in range(15)]
        assert sum(authorised) == 1, f'{sum(authorised)} of 15 callers got through'

    def test_single_flight_holds_under_real_thread_contention(self):
        """main.py fires create_task(_refresh_klines_bg(stagger=0)) per symbol and each hops
        to a thread, so blocked_for() is genuinely called from 15 threads at once."""
        for attempt in range(30):
            g = RateLimitGuard()
            _staged_ban(g)
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

    def test_is_blocked_does_not_steal_the_probe_slot(self):
        """It used to call blocked_for(), so merely asking the question burned the probe a
        real caller needed."""
        g = RateLimitGuard()
        _staged_ban(g)
        _offer_probe(g)
        for _ in range(5):
            assert g.is_blocked(KEY)
        assert _probe(g), 'is_blocked() consumed the probe slot'

    # --------------------------------------------------------------------------- #
    # 5. the property that matters: bounded calls while banned                    #
    # --------------------------------------------------------------------------- #

    def test_a_full_candle_batch_makes_no_call_while_banned(self):
        g = RateLimitGuard()
        _staged_ban(g)
        calls = sum(1 for _ in range(16) if _probe(g))
        assert calls == 0, f'{calls} calls leaked immediately after arming'

    def test_settling_covers_the_measured_burst_delay(self):
        """The candle-close kline burst was measured landing 4.99s after the probe on
        2026-09-07, which a 3s window did not cover — it cleared, then two calls leaked
        and added 4 minutes to the ban."""
        assert _SETTLE_S >= 5.5, (
            f'_SETTLE_S={_SETTLE_S} does not cover the measured 4.99s burst delay')


# ── test_probe_waits_out_the_ban.py ───────────────────────────────────

def _gate_ban(g, secs: float, key=KEY):
    ms = int((time.time() + secs) * 1000)
    g.note_exception(key, Exception(
        f"APIError(code=-1003): Way too many requests; IP(15.158.242.97) "
        f"banned until {ms}."))


class TestProbeWaitsOutTheBan:
    """Probing must not start until most of the stated ban has elapsed.

    Bans are per-CloudFront-edge, not per-account. Three edges rejected us on 2026-09-08,
    each with its own expiry (.97 said 17:44:57 while .71 said 17:34:57 at the same time). A
    probe therefore proves only that the edge IT reached is clear; the next request routes
    elsewhere, gets rejected, and adds ~120s to that edge's ban. The guard then re-arms —
    longer — and the cycle repeats every candle:

        16:07:30  ARMED 3600s (until 17:44:57)
        16:13:14  probe with 54 min left -> SUCCEEDED -> block cleared
        16:15:02  REJECTED               -> expiry pushed out to 17:48:58
        16:22:30  ARMED again ...

    Measured cost across five episodes in one day: +336 minutes of ban time from 39 probes.
    The 15:37 episode alone went from "ends 15:46:54" to "ends 17:48:58" — 122 minutes added.

    Probing early would be worth that only if bans blocked trading. They do not: three real
    orders were placed INSIDE stated ban windows (TIAUSDT 09-07 16:15, AVAXUSDT 09-08 13:45,
    EIGENUSDT 09-08 15:45), and the log holds no order-placement failure at all — every -1003
    is a balance read, which falls back to the cached balance.
    """

    @pytest.fixture
    def clock(self, monkeypatch):
        state = {'t': 10_000.0}
        monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
        return state

    class TestTheGate:
        def test_no_probe_early_in_the_ban(self, clock):
            """The observed failure: a probe at ~6 min into a 60 min ban."""
            g = RateLimitGuard()
            _gate_ban(g, 3600)
            clock['t'] += 6 * 60
            assert g.blocked_for(KEY) > 0, 'probed 6 minutes into an hour-long ban'

        def test_no_probe_even_at_half_way(self, clock):
            g = RateLimitGuard()
            _gate_ban(g, 3600)
            clock['t'] += 1800
            assert g.blocked_for(KEY) > 0

        def test_a_probe_is_offered_once_most_of_the_ban_has_passed(self, clock):
            """The capability is kept — Binance lifted one ban 41 min early on 2026-09-06."""
            g = RateLimitGuard()
            _gate_ban(g, 3600)
            clock['t'] += 3600 * _PROBE_AFTER_FRAC + _PROBE_FIRST_S + 1
            assert g.blocked_for(KEY) == 0.0, 'never probes, so an early lift is never found'

        def test_the_gate_scales_with_the_ban_length(self, clock):
            """A short ban must not wait as long as a long one."""
            g = RateLimitGuard()
            _gate_ban(g, 600)
            clock['t'] += 600 * _PROBE_AFTER_FRAC + _PROBE_FIRST_S + 1
            assert g.blocked_for(KEY) == 0.0

    class TestExtensionPushesTheGateBack:
        def test_an_extended_ban_recomputes_the_gate(self, clock):
            """Otherwise a ban extended near its end is probed immediately, which is exactly
            the observed flap: reject -> re-arm -> probe -> reject.

            The extension must happen while the FIRST ban is still running. An earlier
            version of this test let a short ban expire first, so blocked_for() cleared it and
            popped the gate — which let a `setdefault` instead of an assignment on the
            recompute survive mutation testing unnoticed.
            """
            g = RateLimitGuard()
            _gate_ban(g, 3600)
            clock['t'] += 3600 * _PROBE_AFTER_FRAC + _PROBE_FIRST_S + 1
            assert g.blocked_for(KEY) == 0.0, 'premise: a probe is offered near the end'
            assert g.is_blocked(KEY) is True, 'premise: the ban has NOT expired yet'

            _gate_ban(g, 3600)                 # the probe was rejected; the edge extends the ban
            clock['t'] += 60
            assert g.blocked_for(KEY) > 0, 'probed straight into the extended ban'

            clock['t'] += 1800            # still shut well into the new window
            assert g.blocked_for(KEY) > 0

        def test_the_longer_expiry_wins(self, clock):
            """Two edges report different expiries; resuming on the nearer one walks into
            the further one. .71 said 17:34:57 while .97 said 17:44:57."""
            g = RateLimitGuard()
            _gate_ban(g, 3600)
            far = g.blocked_for(KEY)
            _gate_ban(g, 600)                              # a nearer expiry from another edge
            assert g.blocked_for(KEY) >= far - 1, 'a nearer expiry shortened the block'

    class TestReplayOfTheRealEpisode:
        def test_the_16_07_loop_cannot_happen_again(self, clock):
            """Replays the measured sequence. Under the old behaviour the probe at +5m44s
            was offered, cleared the block, and the rejection at +7m32s extended the ban."""
            g = RateLimitGuard()
            _gate_ban(g, 3600)                 # 16:07:30 ARMED until 17:44:57
            clock['t'] += 344             # 16:13:14 — where the probe used to fire
            assert g.blocked_for(KEY) > 0, 'the 16:13:14 probe would fire again'
            clock['t'] += 108             # 16:15:02 — where the rejection landed
            assert g.blocked_for(KEY) > 0, 'traffic would reach the network and extend the ban'
            # and still closed at each later re-arm point
            for extra in (452, 1352, 2252):
                clock['t'] += extra
                if clock['t'] - 10_000.0 < 3600 * _PROBE_AFTER_FRAC:
                    assert g.blocked_for(KEY) > 0

    class TestTheGateSurvivesARestart:
        """The gate lives in memory; the ban expiry is persisted. If a restart restores one
        without the other, the flap comes back — which is exactly what happened on
        2026-09-08 after I deployed the gate and then restarted three times."""

        def _restore(self, tmp_path, secs: float):
            import json, time as _t
            f = tmp_path / 'rl.json'
            f.write_text(json.dumps({KEY: _t.time() + secs}))
            g = RateLimitGuard()
            g.load_state(f)
            return g

        def test_load_state_sets_the_gate(self, tmp_path):
            g = self._restore(tmp_path, 3600)
            assert g._probe_not_before.get(KEY) is not None, \
                'a restored ban with no gate probes immediately'

        def test_a_restored_ban_is_not_probed_immediately(self, tmp_path, clock):
            g = self._restore(tmp_path, 3600)
            g._next_probe[KEY] = clock['t'] - 1
            assert g.blocked_for(KEY) > 0

        def test_a_blocked_key_with_no_gate_fails_closed(self, clock):
            """Any path that sets _blocked_until without going through _arm/load_state must
            not fall open — an absent gate used to mean 'probe now'."""
            g = RateLimitGuard()
            g._blocked_until[KEY] = clock['t'] + 3600
            g._next_probe[KEY] = clock['t'] - 1
            assert KEY not in g._probe_not_before        # premise
            assert g.blocked_for(KEY) > 0
            assert g._probe_not_before.get(KEY) is not None, 'it should derive and keep one'

    class TestNothingElseRegressed:
        def test_an_expired_ban_still_clears_without_a_probe(self, clock):
            g = RateLimitGuard()
            _gate_ban(g, 600)
            clock['t'] += 601
            assert g.blocked_for(KEY) == 0.0
            assert g.is_blocked(KEY) is False

        def test_settling_still_completes_once_started(self, clock):
            g = RateLimitGuard()
            _gate_ban(g, 600)
            clock['t'] += 600 * _PROBE_AFTER_FRAC + _PROBE_FIRST_S + 1
            assert g.blocked_for(KEY) == 0.0
            g.note_success(KEY)
            clock['t'] += rlg._SETTLE_S + 1
            assert g.blocked_for(KEY) == 0.0
            assert g.is_blocked(KEY) is False

        def test_an_unbanned_key_is_never_gated(self, clock):
            g = RateLimitGuard()
            assert g.blocked_for('production') == 0.0

    class TestPositionReadsAreGuardedToo:
        """A restart during a ban used to extend it through the startup reconciliation.

        Measured 2026-09-08:
            18:37:30  ARMED 3501s (ban until 19:35:51)
            18:56:29  Reconciliation failed: -1003   <- restart, unguarded read, +120s
        """

        @staticmethod
        def _src(name: str) -> str:
            import inspect
            from bot.order_executor import OrderExecutor
            return inspect.getsource(getattr(OrderExecutor, name))

        def test_startup_reconciliation_checks_the_guard(self):
            s = self._src('reconcile_with_exchange')
            assert 'rl_guard.blocked_for' in s
            assert s.index('rl_guard.blocked_for') < s.index('futures_position_information')

        def test_position_sync_checks_the_guard(self):
            s = self._src('sync_positions_with_exchange')
            assert 'rl_guard.blocked_for' in s
            assert s.index('rl_guard.blocked_for') < s.index('futures_position_information')

        def test_a_rejection_there_arms_the_guard(self):
            """Otherwise the next scheduled read walks into the same ban."""
            assert 'rl_guard.note_exception' in self._src('reconcile_with_exchange')

        def test_the_balance_read_is_still_guarded(self):
            assert 'rl_guard.blocked_for' in self._src('fetch_account_balance')


# ── test_ban_window_not_clamped_to_an_hour.py ─────────────────────────

BAN_3H24 = 3 * 3600 + 24 * 60          # the ban actually seen on 2026-09-09


def _window_ban(g, secs, key=KEY):
    g.note_exception(key, Exception(
        f"APIError(code=-1003): Way too many requests; IP(15.158.242.97) "
        f"banned until {int((time.time() + secs) * 1000)}."))


class TestBanWindowNotClampedToAnHour:
    """Suppress for the whole ban Binance states, not for one hour.

    `_MAX_BLOCK_S` exists to reject an absurd expiry from a malformed message — its own
    comment says so. At 3600s it also clamped *legitimate* multi-hour bans, and that is what
    produced the flap measured on 2026-09-09:

        17:22:30  ARMED for 3600s (until 20:46:56)   <- real ban is 3h24m, suppressed 1h
                  guard's own block lapses 18:22:30
                  probe gate opens  ~18:16:30  (0.9 x 3600)
        19:07:30  the mid-candle balance prefetch goes out and gets -1003
                  ARMED for 3600s (until 20:46:56)   <- identical expiry: same ban

    Tracing all 15 arming events that day against their stated expiries, 7 are rediscoveries
    caused purely by this clamp — 02:22, 05:22, 06:22, 07:52, 09:22, 12:37, 19:07. Roughly
    half of every ban-discovering request was self-inflicted, and each one is a call into a
    live ban, which is what extends it.

    With the true duration in place the probe gate does its intended job: 0.9 x 3h24m puts one
    probe about 20 minutes before expiry, which is what catches an early lift (Binance released
    a ban 41 minutes early on 2026-09-06) without hammering it hourly.

    Second defect, same family: `load_state` restored `_blocked_until`, `_banned_until_wall`,
    `_probe_delay`, `_next_probe` and `_probe_not_before` — but not `_announced`. So a restart
    during a ban wiped the alert-dedup memory and the next rediscovery sent a fresh
    "API ban started" for a ban that had never ended. `unresolved_ban_endpoints()` already
    tells the operator at startup about an unclosed ban, so the re-arm alert is redundant.
    """

    @pytest.fixture
    def clock(self, monkeypatch):
        state = {'t': 10_000.0}
        monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
        return state

    class TestTheWholeBanIsCovered:
        def test_a_three_hour_ban_is_not_cut_to_one(self, clock):
            g = RateLimitGuard()
            _window_ban(g, BAN_3H24)
            clock['t'] += 3600 + 60          # just past where the old clamp expired
            assert g.is_blocked(KEY) is True, 'the guard un-blocked an hour into a 3h24m ban'

        def test_the_measured_1907_request_no_longer_goes_out(self, clock):
            """Replays 17:22:30 -> 19:07:30 exactly."""
            g = RateLimitGuard()
            _window_ban(g, BAN_3H24)
            clock['t'] += 105 * 60           # 17:22:30 -> 19:07:30
            assert g.blocked_for(KEY) > 0, \
                'the balance prefetch would reach the network and re-discover the ban'

        def test_the_hourly_rediscovery_cycle_is_gone(self, clock):
            """The seven self-inflicted arms were all at ~1h multiples."""
            g = RateLimitGuard()
            _window_ban(g, BAN_3H24)
            for _ in range(3):
                clock['t'] += 3600
                if clock['t'] - 10_000.0 < BAN_3H24 * _PROBE_AFTER_FRAC:
                    assert g.blocked_for(KEY) > 0, 'probed mid-ban again'

        def test_the_max_is_high_enough_for_what_binance_actually_issues(self):
            assert _MAX_BLOCK_S >= BAN_3H24, (
                f'_MAX_BLOCK_S={_MAX_BLOCK_S} still clamps the 3h24m ban measured on '
                f'2026-09-09 ({BAN_3H24}s)')

    class TestTheProbeStillWorks:
        def test_one_probe_is_offered_near_the_true_expiry(self, clock):
            """Binance lifted a ban 41 min early on 2026-09-06 — that must stay findable."""
            g = RateLimitGuard()
            _window_ban(g, BAN_3H24)
            clock['t'] += BAN_3H24 * _PROBE_AFTER_FRAC + rlg._PROBE_FIRST_S + 1
            assert g.blocked_for(KEY) == 0.0, 'no probe offered, so an early lift is missed'

        def test_the_gate_is_late_in_the_ban_not_hourly(self, clock):
            """0.9 x 3h24m is ~3h04m in, i.e. ~20 min before expiry."""
            g = RateLimitGuard()
            _window_ban(g, BAN_3H24)
            clock['t'] += 2 * 3600           # two hours in — still well before the gate
            assert g.blocked_for(KEY) > 0

    class TestAbsurdExpiriesAreStillRejected:
        def test_a_year_long_expiry_is_clamped(self, clock):
            g = RateLimitGuard()
            _window_ban(g, 86400 * 365)
            assert g.blocked_for(KEY) <= _MAX_BLOCK_S + 1

        def test_a_year_long_expiry_from_disk_is_clamped(self, tmp_path):
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({KEY: time.time() + 86400 * 365}))
            g = RateLimitGuard()
            g.load_state(p)
            assert g.blocked_for(KEY) <= _MAX_BLOCK_S + 1

        def test_an_unparseable_message_still_uses_the_short_default(self, clock):
            g = RateLimitGuard()
            g.note_exception(KEY, Exception('APIError(code=-1003): Way too many requests'))
            assert 0 < g.blocked_for(KEY) <= rlg._DEFAULT_BLOCK_S + 1

    class TestARestartKeepsTheWholeWindow:
        def _restore(self, tmp_path, secs):
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({KEY: time.time() + secs}))
            g = RateLimitGuard()
            g.load_state(p)
            return g

        def test_load_state_restores_the_full_remaining(self, tmp_path, clock):
            g = self._restore(tmp_path, BAN_3H24)
            clock['t'] += 3600 + 60
            assert g.is_blocked(KEY) is True, \
                'a restart re-introduced the one-hour clamp'

        def test_load_state_seeds_the_announce_dedup(self, tmp_path):
            """Otherwise the first rediscovery after a restart re-alerts a ban that never
            ended — which is exactly what a deploy during a ban causes."""
            g = self._restore(tmp_path, BAN_3H24)
            assert g._announced.get(KEY) is not None, \
                'restart wiped the alert dedup, so the next re-arm announces again'

        def test_no_second_started_alert_after_a_restart(self, tmp_path, clock):
            g = self._restore(tmp_path, BAN_3H24)
            sent = []
            g.set_notifier(lambda l, t, b, s: sent.append(t), mode='test')
            # same ban rediscovered after the restart
            expiry = g._banned_until_wall[KEY]
            g.note_exception(KEY, Exception(
                f"APIError(code=-1003): banned until {int(expiry * 1000)}."))
            assert [t for t in sent if 'ban started' in t] == [], sent

        def test_a_later_expiry_after_a_restart_is_logged_but_not_re_alerted(
                self, tmp_path, clock, caplog):
            """THE RULE, stated once because I keep re-deriving it wrongly:

            "API ban started" fires ONLY on the unblocked -> blocked transition
            (`if not was_blocked`). A restored ban means we are already blocked, so a later
            expiry arriving on top of it is an EXTENSION — logged, because the new expiry is
            real information, but not re-alerted, because the outage was already announced.
            """
            g = self._restore(tmp_path, 600)
            sent = []
            g.set_notifier(lambda l, t, b, s: sent.append(t), mode='test')
            with caplog.at_level('WARNING'):
                _window_ban(g, BAN_3H24)
            assert 'ARMED' in caplog.text, 'the new expiry must still be logged'
            assert [t for t in sent if 'ban started' in t] == [], \
                'an extension of an already-announced ban is not a new outage'

        def test_a_new_ban_after_the_restored_one_lapsed_does_alert(self, tmp_path, clock):
            """The transition that genuinely matters."""
            g = self._restore(tmp_path, 600)
            clock['t'] += 700
            g.note_success(KEY)
            clock['t'] += rlg._SETTLE_S + 1
            assert g.blocked_for(KEY) == 0.0, 'premise: recovered'
            sent = []
            g.set_notifier(lambda l, t, b, s: sent.append(t), mode='test')
            _window_ban(g, BAN_3H24)
            assert len([t for t in sent if 'ban started' in t]) == 1, sent


# ── test_ban_state_persisted.py ───────────────────────────────────────

def _ban_msg(secs):
    return (f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until "
            f"{int((time.time() + secs) * 1000)}.")


class TestBanStatePersisted:
    """The ban expiry must survive a restart.

    The guard held _blocked_until in memory only, so a restart during a ban started clean
    and immediately fired its startup calls — kline loads, leverage brackets, balance —
    into an active ban, each one extending it. Two restarts on 2026-09-07 (16:09, 16:35)
    landed inside the 15:47 ban window and only escaped because Binance had lifted it early.

    Persisted as WALL-CLOCK epoch seconds: monotonic time is meaningless across processes.
    """

    class TestSave:
        def test_an_armed_ban_is_written(self, tmp_path):
            g, p = RateLimitGuard(), tmp_path / 'rl.json'
            g.note_exception('testnet', Exception(_ban_msg(900)))
            g.save_state(p)
            d = json.loads(p.read_text())
            assert d['testnet'] > time.time(), 'must be a future wall-clock time'

        def test_a_cleared_ban_is_removed(self, tmp_path):
            g, p = RateLimitGuard(), tmp_path / 'rl.json'
            g.note_exception('testnet', Exception(_ban_msg(900)))
            g.save_state(p)
            g.reset('testnet')
            g.save_state(p)
            assert json.loads(p.read_text()) == {}, 'a stale expiry would suppress traffic'

        def test_saving_never_raises(self, tmp_path):
            """Called from the candle path — a disk problem must not stop trading."""
            g = RateLimitGuard()
            g.note_exception('testnet', Exception(_ban_msg(900)))
            g.save_state(tmp_path / 'no' / 'such' / 'dir' / 'rl.json')

    class TestLoad:
        def test_a_future_expiry_re_arms_the_guard(self, tmp_path):
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({'testnet': time.time() + 600}))
            g = RateLimitGuard()
            g.load_state(p)
            assert g.is_blocked('testnet'), 'a restart must not call into a known ban'

        def test_an_expired_ban_is_ignored(self, tmp_path):
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({'testnet': time.time() - 60}))
            g = RateLimitGuard()
            g.load_state(p)
            assert not g.is_blocked('testnet'), 'an old file must not suppress a clean start'

        def test_a_missing_or_corrupt_file_is_safe(self, tmp_path):
            g = RateLimitGuard()
            g.load_state(tmp_path / 'absent.json')
            assert not g.is_blocked('testnet')
            bad = tmp_path / 'bad.json'
            bad.write_text('{not json')
            g.load_state(bad)
            assert not g.is_blocked('testnet')

        def test_an_absurd_expiry_is_clamped(self, tmp_path):
            """A corrupt file must not disable the bot for a year."""
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({'testnet': time.time() + 86400 * 365}))
            g = RateLimitGuard()
            g.load_state(p)
            assert g.blocked_for('testnet') <= _MAX_BLOCK_S + 1

        def test_a_restored_ban_does_not_probe_immediately(self, tmp_path):
            """A restart must not reset the probe gate.

            Measured 2026-09-08: ban until 19:35:51, restart at 18:56:29, probe at 19:00:00
            with 36 minutes still to run — because load_state restored the expiry but not
            _probe_not_before, and an absent gate used to mean "probe now". That put the
            flap straight back after every restart during a ban.
            """
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({'testnet': time.time() + 600}))
            g = RateLimitGuard()
            g.load_state(p)
            g._next_probe['testnet'] = time.monotonic() - 1     # backoff satisfied
            assert g.blocked_for('testnet') > 0, 'probed straight after a restore'

        def test_a_restored_ban_still_probes_near_the_end(self, tmp_path):
            """Binance lifts bans early; a restored block must not be waited out blindly."""
            p = tmp_path / 'rl.json'
            p.write_text(json.dumps({'testnet': time.time() + 600}))
            g = RateLimitGuard()
            g.load_state(p)
            g._next_probe['testnet'] = time.monotonic() - 1
            g._probe_not_before['testnet'] = time.monotonic() - 1   # reached the gate
            assert g.blocked_for('testnet') == 0.0, 'no probe slot offered after a restore'

        def test_multiple_endpoints_round_trip(self, tmp_path):
            p = tmp_path / 'rl.json'
            g = RateLimitGuard()
            g.note_exception('testnet', Exception(_ban_msg(600)))
            g.note_exception('production', Exception(_ban_msg(900)))
            g.save_state(p)
            g2 = RateLimitGuard()
            g2.load_state(p)
            assert g2.is_blocked('testnet') and g2.is_blocked('production')

    def test_main_restores_before_the_first_api_call(self):
        main_src = src('main.py')
        assert 'load_state' in main_src, 'main.py never restores the ban state'
        assert main_src.index('rl_guard.load_state') < main_src.index('feed.load_klines'), \
            'the ban state must be restored before the first kline fetch'

    def test_placement_is_a_privileged_probe_not_a_gated_call(self):
        """Placement must stay ungated: a real order is the scarce resource (78 in 27 days,
        all 78 succeeded, and the real slot already fires at 86-100% of its virtual twin's
        rate). Refusing one on a possibly-stale ban flag would cost what we are protecting.
        But a rate-limited failure must arm the guard so the following reads back off."""
        oe_src = src('bot/order_executor.py')
        i = oe_src.index('Order placement failed for')
        assert 'rl_guard.note_exception' in oe_src[max(0, i - 1600):i], \
            'a rate-limited placement must arm the guard'
        j = oe_src.index('async def place_order')
        assert 'blocked_for' not in oe_src[j:i], \
            'placement must not be refused on a ban flag'

    def test_the_read_paths_that_start_a_ban_are_all_guarded(self):
        """klines, balance and leverage brackets are the calls that discover a ban."""
        feed = src('bot/data_feed.py')
        oe = src('bot/order_executor.py')
        assert 'rl_guard.blocked_for' in feed, 'kline fetch unguarded'
        for fn in ('fetch_account_balance', 'fetch_leverage_brackets'):
            i = oe.index(f'def {fn}')
            assert 'blocked_for' in oe[i:i + 1500], f'{fn} unguarded'

    def test_a_first_run_with_no_state_file_still_persists_later_bans(self, tmp_path):
        """The bug a runtime check caught and the unit tests missed: load_state() returned
        early on a missing file, leaving _state_path unset, so on a fresh install no ban was
        ever written and the feature was dead on first run."""
        p = tmp_path / 'rate_limit_state.json'
        g = RateLimitGuard()
        g.load_state(p)                      # file does not exist yet
        assert not p.exists()
        g.note_exception('testnet', Exception(_ban_msg(1800)))
        assert p.exists(), 'a ban after a clean start was not persisted'
        assert json.loads(p.read_text())['testnet'] > time.time()

    def test_a_corrupt_state_file_still_allows_later_persistence(self, tmp_path):
        p = tmp_path / 'rate_limit_state.json'
        p.write_text('{not json')
        g = RateLimitGuard()
        g.load_state(p)
        g.note_exception('testnet', Exception(_ban_msg(1800)))
        assert json.loads(p.read_text())['testnet'] > time.time()


# ── test_ban_alert_closure.py ─────────────────────────────────────────

def _e(title, ts='2026-09-07T10:00:00'):
    return {'timestamp': ts, 'title': title, 'source': 'rate_limit_guard'}


class TestBanAlertClosure:
    """A restart during a ban must not leave a dangling "ban started" alert.

    Guard state is in-memory. On 2026-09-07 the bot was restarted at 14:56 while a block was
    armed, so `_clear()` never ran and no "API ban ended" notification was sent. The last
    thing Telegram said was "API ban started" at 14:30, for a ban that expired at ~14:48 —
    so it read as a 40-minute outage that was not happening. Every deploy during a ban
    reproduces this.
    """

    def test_a_started_with_no_ended_is_unresolved(self):
        entries = [_e('API ban started — testnet', '2026-09-07T14:30:05')]
        assert unresolved_ban_endpoints(entries) == ['testnet']

    def test_a_matched_pair_is_resolved(self):
        entries = [
            _e('API ban started — testnet', '2026-09-07T13:00:00'),
            _e('API ban ended — testnet', '2026-09-07T13:15:04'),
        ]
        assert unresolved_ban_endpoints(entries) == []

    def test_only_the_most_recent_state_counts(self):
        """The real 2026-09-07 sequence: many pairs, then a trailing 'started'."""
        entries = [
            _e('API ban started — testnet', '2026-09-07T13:00:00'),
            _e('API ban ended — testnet', '2026-09-07T13:15:04'),
            _e('API ban started — testnet', '2026-09-07T13:15:05'),
            _e('API ban ended — testnet', '2026-09-07T14:15:04'),
            _e('API ban started — testnet', '2026-09-07T14:30:05'),
        ]
        assert unresolved_ban_endpoints(entries) == ['testnet']

    def test_endpoints_are_tracked_independently(self):
        entries = [
            _e('API ban started — testnet', '2026-09-07T14:00:00'),
            _e('API ban started — production', '2026-09-07T14:05:00'),
            _e('API ban ended — production', '2026-09-07T14:10:00'),
        ]
        assert unresolved_ban_endpoints(entries) == ['testnet']

    def test_unrelated_entries_are_ignored(self):
        entries = [
            _e('Bot stopped'),
            _e('Running obligatory backtest'),
            _e('Low balance warning'),
            _e('BTCUSDT BUY — Win'),
        ]
        assert unresolved_ban_endpoints(entries) == []

    def test_entries_out_of_order_are_handled(self):
        """system_log is append-ordered, but do not depend on it."""
        entries = [
            _e('API ban ended — testnet', '2026-09-07T14:15:04'),
            _e('API ban started — testnet', '2026-09-07T13:15:05'),
        ]
        assert unresolved_ban_endpoints(entries) == []

    def test_empty_and_malformed_input_is_safe(self):
        assert unresolved_ban_endpoints([]) == []
        assert unresolved_ban_endpoints([{}, {'title': None}, {'title': 42}]) == []

    def test_main_sends_the_closing_notice_on_startup(self):
        main_src = src('main.py')
        assert 'unresolved_ban_endpoints(' in main_src, \
            'startup must close a dangling ban alert or the reader is left misinformed'
        # The CALL has to come after the notifier is wired, or nothing is sent. Compare
        # against the call site, not the import, which necessarily appears first.
        assert main_src.index('rl_guard.set_notifier') < main_src.index('unresolved_ban_endpoints(')


# ── test_ban_alert_not_reannounced.py ─────────────────────────────────

def _capture_guard(guard, mode='test'):
    sent = []
    guard.set_notifier(
        lambda lvl, title, body, src: sent.append((lvl, title, body, src)), mode=mode)
    return sent


def _ban_at(guard, expiry_epoch: float):
    """Arm from a message quoting an exact wall-clock expiry, as Binance does."""
    guard.note_exception(KEY, Exception(
        f"APIError(code=-1003): Way too many requests; IP(15.158.242.97) "
        f"banned until {int(expiry_epoch * 1000)}."))


def _started(sent):
    return [s for s in sent if 'ban started' in s[1]]


class TestBanAlertNotReannounced:
    """One ban must produce one "API ban started" alert, however often we rediscover it.

    Observed 2026-09-09. Binance reported the SAME expiry twice, two hours apart:

        17:22:30  ARMED for 3600s (until 20:46:56)   IP(15.158.242.97) banned until 1788986816410
        19:07:30  ARMED for 3600s (until 20:46:56)   IP(15.158.242.97) banned until 1788986816410

    Identical `banned until` epoch — one continuous ban, never lifted. Yet the second one sent
    a fresh "API ban started" Telegram alert, so it read as a new ban and prompted "why are we
    banned again?". Two separate defects combine:

    1. `remaining` is clamped to `_MAX_BLOCK_S` (3600s), so the guard suppressed for only an
       hour against a 3h24m ban, its probe gate reopened, and the mid-candle balance prefetch
       walked back into the live ban at 19:07:30. That request should never have happened.
       Fixed separately — this file does not cover it.

    2. The announcement deduped on `deadline`, which is `now + remaining` in MONOTONIC time.
       17:22:30+3600 and 19:07:30+3600 are different numbers, so the dedup never matched even
       though the wall-clock expiry was byte-identical. That is what this file fixes: dedup on
       the expiry Binance actually stated.

    `was_blocked` cannot carry this on its own — it is computed from the clamped
    `_blocked_until`, which had already lapsed by 19:07:30, so it was False and the alert
    passed straight through.

    An extension is still LOGGED — a pushed-out expiry is real news for the log — but sends no
    second "API ban started", because the alert fires only on the unblocked -> blocked
    transition and you were already told about that ban.
    """

    @pytest.fixture
    def clock(self, monkeypatch):
        state = {'t': 10_000.0}
        monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
        return state

    @pytest.fixture
    def g(self):
        return RateLimitGuard()

    class TestTheSameBanAnnouncesOnce:
        def test_a_rediscovery_two_hours_later_does_not_realert(self, g, clock):
            """The measured case: identical expiry at 17:22:30 and 19:07:30."""
            sent = _capture_guard(g)
            expiry = time.time() + 3 * 3600 + 24 * 60      # 3h24m out, as on the day
            _ban_at(g, expiry)
            assert len(_started(sent)) == 1, 'premise: the first ban alerts'

            clock['t'] += 105 * 60                          # 17:22:30 -> 19:07:30
            _ban_at(g, expiry)                              # same epoch, same ban
            assert len(_started(sent)) == 1, \
                'the same ban was announced twice — this is the reported bug'

        def test_it_holds_across_several_rediscoveries(self, g, clock):
            sent = _capture_guard(g)
            expiry = time.time() + 4 * 3600
            _ban_at(g, expiry)
            for _ in range(4):
                clock['t'] += 3700
                _ban_at(g, expiry)
            assert len(_started(sent)) == 1

        def test_the_log_line_is_also_suppressed(self, g, clock, caplog):
            expiry = time.time() + 3 * 3600
            with caplog.at_level('WARNING'):
                _ban_at(g, expiry)
                assert 'ARMED' in caplog.text, 'premise: the first arm logs'
                # caplog accumulates for the WHOLE test, not just the `with` block — an
                # earlier version of this assertion was reading arm 1's line back.
                caplog.clear()
                clock['t'] += 105 * 60
                _ban_at(g, expiry)
            assert 'ARMED' not in caplog.text, 'duplicate ARMED line for an unchanged ban'

    class TestRealNewsStillAnnounces:
        def test_an_extension_is_logged_but_sends_no_second_started_alert(self, g, clock, caplog):
            """A pushed-out expiry is new information for the LOG, but not a new outage.

            The Telegram alert sits behind `if not was_blocked`, so it fires only on the
            unblocked -> blocked transition. That is deliberate and stays: "API ban started"
            would be the wrong title for an extension of a ban you were already told about.
            An earlier version of this test asserted two alerts here — that was me encoding a
            requirement the design had already decided against.
            """
            sent = _capture_guard(g)
            base = time.time() + 1800
            _ban_at(g, base)
            assert len(_started(sent)) == 1
            clock['t'] += 60
            with caplog.at_level('WARNING'):
                _ban_at(g, base + 3600)             # Binance extended it, still blocked
            assert 'ARMED' in caplog.text, 'an extension must still be logged'
            assert len(_started(sent)) == 1, 'an extension is not a new outage'

        def test_a_new_ban_after_the_block_lapsed_announces(self, g, clock):
            """The transition that matters: not blocked -> blocked, with a different expiry."""
            sent = _capture_guard(g)
            _ban_at(g, time.time() + 300)
            clock['t'] += 400
            assert g.is_blocked(KEY) is False, 'premise: the block lapsed'
            _ban_at(g, time.time() + 7200)
            assert len(_started(sent)) == 2

        def test_a_genuinely_new_ban_after_recovery_announces(self, g, clock):
            """Through the real recovery path, and with a LATER expiry.

            An earlier version reused `time.time() + 300` for both bans. Only monotonic is
            mocked, so the wall clock barely moved and both bans carried the same stated
            expiry — deduping them was correct, and the test was wrong. A real second ban
            always expires later.
            """
            sent = _capture_guard(g)
            _ban_at(g, time.time() + 300)
            clock['t'] += 400                        # first ban expires
            g.note_success(KEY)
            clock['t'] += rlg._SETTLE_S + 1
            assert g.blocked_for(KEY) == 0.0, 'premise: recovered through _clear()'
            _ban_at(g, time.time() + 7200)           # a later expiry — genuinely new
            assert len(_started(sent)) == 2

        def test_a_message_with_no_parseable_expiry_still_announces(self, g, clock):
            """Falls back to the old deadline key; those are short assumed blocks."""
            sent = _capture_guard(g)
            g.note_exception(KEY, Exception('APIError(code=-1003): Way too many requests'))
            assert len(_started(sent)) == 1

        def test_reset_lets_the_next_ban_announce(self, g, clock):
            sent = _capture_guard(g)
            expiry = time.time() + 3600
            _ban_at(g, expiry)
            g.reset(KEY)
            _ban_at(g, expiry)
            assert len(_started(sent)) == 2

    class TestNothingElseRegressed:
        def test_the_block_is_still_armed_on_a_deduped_rediscovery(self, g, clock):
            """Suppressing the ALERT must not suppress the suppression."""
            expiry = time.time() + 3 * 3600
            _ban_at(g, expiry)
            clock['t'] += 105 * 60
            _ban_at(g, expiry)
            assert g.is_blocked(KEY) is True
            assert g.blocked_for(KEY) > 0

        def test_state_is_still_persisted_on_a_deduped_rediscovery(self, g, tmp_path, clock):
            """_persist() used to sit inside the announce branch, so a deduped re-arm would
            skip writing the file. The expiry is unchanged, so nothing is lost today — but a
            later change to what _persist writes must not silently stop happening."""
            import json
            p = tmp_path / 'rl.json'
            g.load_state(p)
            expiry = time.time() + 3 * 3600
            _ban_at(g, expiry)
            assert p.exists()
            p.unlink()
            clock['t'] += 105 * 60
            _ban_at(g, expiry)
            assert p.exists(), 'a deduped re-arm stopped persisting state'
            assert abs(json.loads(p.read_text())[KEY] - expiry) < 2

        def test_the_ban_ended_alert_still_pairs(self, g, clock):
            """unresolved_ban_endpoints() reads started/ended pairs out of the alert log.
            Two 'started' for one ban made that ambiguous; one each keeps it clean."""
            sent = _capture_guard(g)
            expiry = time.time() + 300
            _ban_at(g, expiry)
            clock['t'] += 105 * 60
            _ban_at(g, expiry)
            clock['t'] += 400
            g.note_success(KEY)
            clock['t'] += rlg._SETTLE_S + 1
            g.blocked_for(KEY)
            titles = [s[1] for s in sent]
            assert sum('ban started' in t for t in titles) == 1, titles


# ── test_ban_message_formatting.py ────────────────────────────────────

TAG = re.compile(r'</?[a-zA-Z][^>]{0,12}>')


RAW_EPOCH = re.compile(r'\b1[0-9]{9,12}\b')


REAL_MSG = ("APIError(code=-1003): Way too many requests; IP(15.158.242.71) "
            "banned until 1788777598577. Please use the websocket for live "
            "updates to avoid bans.")


# --------------------------------------------------------------------------- #
# The announced messages, end to end                                          #
# --------------------------------------------------------------------------- #

def _capture_scenario(fn):
    sent = []
    g = RateLimitGuard()
    g.set_notifier(lambda lvl, title, body, src: sent.append((lvl, title, body)), mode='test')
    fn(g)
    return g, sent


class TestBanMessageFormatting:
    """Ban alerts must be readable in Telegram: no markup, no raw epoch numbers.

    Two separate faults, both seen in production on 3822fff:

    1. The body carried <b> tags. Notifier html-escapes the body deliberately — an API
       error containing '<' must not be able to break the message — so those tags rendered
       literally as "Endpoint: <b>testnet</b>".
    2. Binance states the expiry as epoch milliseconds, and the Reason line passed its
       message through verbatim: "banned until 1788777598577". Unreadable in an alert.
    """

    # --------------------------------------------------------------------------- #
    # humanize_epochs                                                             #
    # --------------------------------------------------------------------------- #

    def test_rewrites_the_ban_expiry_as_utc(self):
        out = humanize_epochs(REAL_MSG)
        assert '1788777598577' not in out
        assert '2026-09-07 10:39:58 UTC' in out

    def test_rewrites_epoch_seconds_too(self):
        assert '2026-09-07 10:39:58 UTC' in humanize_epochs('until 1788777598')

    def test_leaves_ordinary_numbers_alone(self):
        """An order id, a price or a quantity must survive untouched."""
        for text in ('orderId=4055123', 'qty=0.001', 'price=142.37',
                     'code=-1003', 'weight 2400', 'IP(15.158.242.71)'):
            assert humanize_epochs(text) == text, text

    def test_leaves_implausible_epochs_alone(self):
        """Only values decoding to a sane date are rewritten."""
        assert humanize_epochs('1000000000000') == '1000000000000'   # year 2001
        assert humanize_epochs('9999999999999') == '9999999999999'   # year 2286

    def test_is_idempotent(self):
        once = humanize_epochs(REAL_MSG)
        assert humanize_epochs(once) == once

    def test_handles_empty_and_none_safely(self):
        assert humanize_epochs('') == ''
        assert humanize_epochs(None) == ''

    # --------------------------------------------------------------------------- #
    # _fmt_wall carries the date, not just a time                                 #
    # --------------------------------------------------------------------------- #

    def test_fmt_wall_includes_the_date(self):
        """A ban can cross midnight; a bare '00:41:58 UTC' is ambiguous."""
        out = _fmt_wall(1788777598.577)
        assert out == '2026-09-07 10:39:58 UTC', out

    def test_ban_started_body_has_no_markup_and_no_raw_epoch(self):
        future_ms = int((time.time() + 900) * 1000)
        msg = f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until {future_ms}."
        _, sent = _capture_scenario(lambda g: g.note_exception('testnet', Exception(msg)))
        assert sent, 'no ban-start notification was sent'
        level, title, body = sent[0]
        assert not TAG.search(body), f'markup in body: {body!r}'
        assert not TAG.search(title), f'markup in title: {title!r}'
        assert str(future_ms) not in body, 'raw epoch left in the body'
        assert 'UTC' in body

    def test_ban_ended_body_has_no_markup(self):
        future_ms = int((time.time() + 900) * 1000)
        msg = f"APIError(code=-1003): banned until {future_ms}."

        def scenario(g):
            g.note_exception('testnet', Exception(msg))
            # A success starts a settling window rather than clearing outright, so drive
            # settling to completion to reach the ban-ended announcement.
            g._next_probe['testnet'] = time.monotonic() - 1
            # Probing waits for most of the stated ban to elapse; skip straight to it.
            g._probe_not_before['testnet'] = time.monotonic() - 1
            assert g.blocked_for('testnet') == 0.0
            g.note_success('testnet')
            g._settle_until['testnet'] = time.monotonic() - 0.01
            assert g.blocked_for('testnet') == 0.0

        _, sent = _capture_scenario(scenario)
        ended = [s for s in sent if 'ended' in s[1]]
        assert ended, 'no ban-end notification was sent'
        for _, title, body in ended:
            assert not TAG.search(body), f'markup in body: {body!r}'
            assert not RAW_EPOCH.search(body), f'raw epoch in body: {body!r}'

    def test_body_survives_notifier_escaping_unchanged(self):
        """The real end-to-end property: what the reader sees equals what we wrote."""
        future_ms = int((time.time() + 900) * 1000)
        msg = f"APIError(code=-1003): banned until {future_ms}."
        _, sent = _capture_scenario(lambda g: g.note_exception('testnet', Exception(msg)))
        _, _, body = sent[0]
        assert html.escape(body) == body, 'body changes when escaped — it contains markup'


# ── test_watchdog_rate_limit_guard.py ─────────────────────────────────

def _watchdog_feed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = MagicMock()
    s.trading_mode = 'test'
    s.api_key = ''
    s.api_secret = ''
    s.kline_cache_limit = 5000
    monkeypatch.setattr('bot.data_feed.Client', MagicMock())
    return DataFeed(s)


def _arm(monkeypatch, seconds=600.0):
    """Report every endpoint as banned, without depending on guard internals."""
    monkeypatch.setattr(rl_guard, 'blocked_for', lambda key: seconds)


class TestWatchdogRateLimitGuard:
    """The watchdogs must not issue requests while the endpoint has us banned.

    `_fetch()` consults the rate-limit guard and refuses to touch the network while banned.
    Both watchdogs bypassed it, calling `self._client` directly, and they updated their
    staleness timestamps only on success -- so a symbol that failed kept qualifying on every
    tick. Candles retried every 30s, prices every 5s: up to 192 requests/min across 16
    symbols, each one adding 120s to the ban. A self-sustaining amplifier, dormant while the
    WebSocket is healthy, which is why it went unnoticed.

    See docs/specs/2026-09-08-watchdog-guard-and-full-ws-klines.md.
    """

    @pytest.fixture(autouse=True)
    def _clean_guard(self):
        """The guard is process-wide; a leaked block would poison other tests."""
        rl_guard._blocked.clear() if hasattr(rl_guard, '_blocked') else None
        yield
        rl_guard._blocked.clear() if hasattr(rl_guard, '_blocked') else None

    class TestTickerIsGuarded:
        def test_no_network_call_while_banned(self, tmp_path, monkeypatch):
            feed = _watchdog_feed(tmp_path, monkeypatch)
            _arm(monkeypatch)
            with pytest.raises(RateLimited):
                feed._fetch_ticker('SOLUSDT')
            feed._client.futures_symbol_ticker.assert_not_called()

        def test_it_does_call_when_not_banned(self, tmp_path, monkeypatch):
            feed = _watchdog_feed(tmp_path, monkeypatch)
            monkeypatch.setattr(rl_guard, 'blocked_for', lambda key: 0.0)
            feed._client.futures_symbol_ticker.return_value = {'price': '102.5'}
            assert feed._fetch_ticker('SOLUSDT') == 102.5
            feed._client.futures_symbol_ticker.assert_called_once()

        def test_a_failure_arms_the_guard(self, tmp_path, monkeypatch):
            """So the next scheduled probe skips the network instead of extending the ban."""
            feed = _watchdog_feed(tmp_path, monkeypatch)
            monkeypatch.setattr(rl_guard, 'blocked_for', lambda key: 0.0)
            noted = []
            monkeypatch.setattr(rl_guard, 'note_exception', lambda k, e: noted.append(k))
            feed._client.futures_symbol_ticker.side_effect = RuntimeError('boom')
            with pytest.raises(RuntimeError):
                feed._fetch_ticker('SOLUSDT')
            assert noted, 'a failed ticker must arm the guard'

    class TestWatchdogMakesNoCallsWhileBanned:
        """Drives the real watchdog loop with time compressed, guard armed."""

        def _run(self, feed, monkeypatch, iterations=12):
            real_sleep = asyncio.sleep
            calls = {'n': 0}

            async def fast_sleep(_s):
                calls['n'] += 1
                if calls['n'] > iterations:
                    raise asyncio.CancelledError
                await real_sleep(0)

            monkeypatch.setattr('bot.data_feed.asyncio.sleep', fast_sleep)

            # Make every symbol look long-stale so BOTH branches fire. Both dicts must be
            # pre-populated: start_watchdog uses setdefault (so our values survive), but the
            # per-iteration init would reset the candle stamp for any symbol missing from
            # _last_price_ts. Compressed sleep barely advances the monotonic clock, so
            # without this the candle branch's 1.5x-timeframe gate (22.5 min) never opens and
            # the assertion below would pass vacuously.
            import time as _time
            _old = _time.monotonic() - 1_000_000.0
            feed._last_price_ts = {s: _old for s in ('SOLUSDT', 'INJUSDT', 'TIAUSDT')}
            feed._last_candle_ts = dict(feed._last_price_ts)

            # start_watchdog catches CancelledError and returns, so this completes normally
            # once fast_sleep raises -- that also exercises the real cancellation path.
            async def go():
                await feed.start_watchdog(
                    get_symbols=lambda: ['SOLUSDT', 'INJUSDT', 'TIAUSDT'],
                    timeframe='15m',
                    on_candle_close=lambda s, c: asyncio.sleep(0),
                    on_price_update=lambda s, p: asyncio.sleep(0),
                    stale_threshold_s=-1.0,   # stale the moment it is checked
                )

            asyncio.run(go())
            assert calls['n'] > iterations, 'the loop never ran'

        def test_neither_watchdog_touches_the_network(self, tmp_path, monkeypatch):
            feed = _watchdog_feed(tmp_path, monkeypatch)
            _arm(monkeypatch)
            self._run(feed, monkeypatch)
            assert feed._client.futures_symbol_ticker.call_count == 0, \
                'price watchdog called the API while banned'
            assert feed._client.futures_klines.call_count == 0, \
                'candle watchdog called the API while banned'

        def test_the_kline_client_is_untouched_too(self, tmp_path, monkeypatch):
            """_fetch uses _klines_client; it must be skipped as well."""
            feed = _watchdog_feed(tmp_path, monkeypatch)
            _arm(monkeypatch)
            self._run(feed, monkeypatch)
            assert feed._klines_client.futures_klines.call_count == 0

    class TestCandleWatchdogUsesTheKlineEndpoint:
        def test_it_goes_through_fetch_not_the_trading_client(self, tmp_path, monkeypatch):
            """The old direct call used the trading client. Under live_klines that would pull
            testnet candles into a cache the rest of the system fills from production."""
            feed_src = src('bot/data_feed.py')
            wd = feed_src.split('# Candle watchdog', 1)[1].split('except asyncio.CancelledError', 1)[0]
            assert 'self._fetch' in wd
            assert 'self._client.futures_klines' not in wd


# ── test_kline_refresh_retries_after_settling.py ──────────────────────

MAIN = src('main.py')


def _body() -> str:
    start = MAIN.index('async def _refresh_klines_bg')
    end = MAIN.index('async def on_candle_close')
    return MAIN[start:end]


class TestKlineRefreshRetriesAfterSettling:
    """A gap refresh suppressed by the guard's settling window must be retried, not lost.

    After a ban blackout every symbol has a kline gap, so on_candle_close creates a
    _refresh_klines_bg task per symbol (main.py:1267) — and those land within a second of
    the candle close, right inside the 3s settling window that follows a successful probe.
    Without a retry the gaps would stay unfilled for a whole candle.

    The retry is safe by construction: it goes back through the guard, so if the ban is
    still real it is suppressed again and no request is made.
    """

    def test_rate_limited_is_caught_separately_from_other_errors(self):
        """A generic `except Exception` would retry real failures too — a 500 or a bad
        symbol should not be retried, only a guard suppression."""
        body = _body()
        assert 'except RateLimited' in body, 'RateLimited must be handled on its own'
        assert body.index('except RateLimited') < body.index('except Exception'), \
            'the specific handler must come first or it is unreachable'

    def test_it_retries_at_most_once(self):
        body = _body()
        assert 'for _attempt in (1, 2)' in body, 'retry must be bounded'
        assert '_attempt == 2' in body, 'the last attempt must give up rather than loop'

    def test_the_retry_waits_past_the_settling_window(self):
        """Retrying inside the window would just be suppressed again."""
        import main
        from bot.rate_limit_guard import _SETTLE_S
        assert main._KLINE_RETRY_AFTER_S > _SETTLE_S, \
            'the retry would land inside settling and be suppressed again'

    def test_a_successful_refresh_returns_immediately(self):
        body = _body()
        first = body.index('await asyncio.to_thread(feed.refresh_klines')
        assert 'return' in body[first:first + 120], \
            'a successful refresh must not fall through into a second attempt'

    def test_other_exceptions_do_not_retry(self):
        body = _body()
        generic = body.index('except Exception')
        assert 'return' in body[generic:generic + 220], \
            'a non-rate-limit failure must not be retried'


# ── test_testnet_rest_host.py ─────────────────────────────────────────

class TestTestnetRestHost:
    """Test-mode REST must go to demo-fapi.binance.com: the old testnet host sits behind
    CloudFront, whose shared address carries other users' weight and gets us -1003 banned."""

    def test_testnet_futures_calls_use_demo_fapi(self):
        c = _trading_client('k', 's', True)
        assert c._create_futures_api_uri('account', 2).startswith('https://demo-fapi.binance.com/fapi/v2/')
        assert _FUTURES_REST_TESTNET == 'https://demo-fapi.binance.com/fapi'

    def test_live_futures_calls_are_untouched(self):
        c = _trading_client('k', 's', False)
        assert c._create_futures_api_uri('account', 2).startswith('https://fapi.binance.com/fapi/v2/')
