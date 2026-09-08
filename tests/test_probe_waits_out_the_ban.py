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
import time

import pytest

from bot import rate_limit_guard as rlg
from bot.rate_limit_guard import RateLimitGuard, _PROBE_AFTER_FRAC, _PROBE_FIRST_S

KEY = 'testnet'


@pytest.fixture
def clock(monkeypatch):
    state = {'t': 10_000.0}
    monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
    return state


def _ban(g, secs: float, key=KEY):
    ms = int((time.time() + secs) * 1000)
    g.note_exception(key, Exception(
        f"APIError(code=-1003): Way too many requests; IP(15.158.242.97) "
        f"banned until {ms}."))


class TestTheGate:
    def test_no_probe_early_in_the_ban(self, clock):
        """The observed failure: a probe at ~6 min into a 60 min ban."""
        g = RateLimitGuard()
        _ban(g, 3600)
        clock['t'] += 6 * 60
        assert g.blocked_for(KEY) > 0, 'probed 6 minutes into an hour-long ban'

    def test_no_probe_even_at_half_way(self, clock):
        g = RateLimitGuard()
        _ban(g, 3600)
        clock['t'] += 1800
        assert g.blocked_for(KEY) > 0

    def test_a_probe_is_offered_once_most_of_the_ban_has_passed(self, clock):
        """The capability is kept — Binance lifted one ban 41 min early on 2026-09-06."""
        g = RateLimitGuard()
        _ban(g, 3600)
        clock['t'] += 3600 * _PROBE_AFTER_FRAC + _PROBE_FIRST_S + 1
        assert g.blocked_for(KEY) == 0.0, 'never probes, so an early lift is never found'

    def test_the_gate_scales_with_the_ban_length(self, clock):
        """A short ban must not wait as long as a long one."""
        g = RateLimitGuard()
        _ban(g, 600)
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
        _ban(g, 3600)
        clock['t'] += 3600 * _PROBE_AFTER_FRAC + _PROBE_FIRST_S + 1
        assert g.blocked_for(KEY) == 0.0, 'premise: a probe is offered near the end'
        assert g.is_blocked(KEY) is True, 'premise: the ban has NOT expired yet'

        _ban(g, 3600)                 # the probe was rejected; the edge extends the ban
        clock['t'] += 60
        assert g.blocked_for(KEY) > 0, 'probed straight into the extended ban'

        clock['t'] += 1800            # still shut well into the new window
        assert g.blocked_for(KEY) > 0

    def test_the_longer_expiry_wins(self, clock):
        """Two edges report different expiries; resuming on the nearer one walks into
        the further one. .71 said 17:34:57 while .97 said 17:44:57."""
        g = RateLimitGuard()
        _ban(g, 3600)
        far = g.blocked_for(KEY)
        _ban(g, 600)                              # a nearer expiry from another edge
        assert g.blocked_for(KEY) >= far - 1, 'a nearer expiry shortened the block'


class TestReplayOfTheRealEpisode:
    def test_the_16_07_loop_cannot_happen_again(self, clock):
        """Replays the measured sequence. Under the old behaviour the probe at +5m44s
        was offered, cleared the block, and the rejection at +7m32s extended the ban."""
        g = RateLimitGuard()
        _ban(g, 3600)                 # 16:07:30 ARMED until 17:44:57
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
        _ban(g, 600)
        clock['t'] += 601
        assert g.blocked_for(KEY) == 0.0
        assert g.is_blocked(KEY) is False

    def test_settling_still_completes_once_started(self, clock):
        g = RateLimitGuard()
        _ban(g, 600)
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
