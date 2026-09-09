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
import json
import time

import pytest

from bot import rate_limit_guard as rlg
from bot.rate_limit_guard import RateLimitGuard, _MAX_BLOCK_S, _PROBE_AFTER_FRAC

KEY = 'testnet'
BAN_3H24 = 3 * 3600 + 24 * 60          # the ban actually seen on 2026-09-09


@pytest.fixture
def clock(monkeypatch):
    state = {'t': 10_000.0}
    monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
    return state


def _ban(g, secs, key=KEY):
    g.note_exception(key, Exception(
        f"APIError(code=-1003): Way too many requests; IP(15.158.242.97) "
        f"banned until {int((time.time() + secs) * 1000)}."))


class TestTheWholeBanIsCovered:
    def test_a_three_hour_ban_is_not_cut_to_one(self, clock):
        g = RateLimitGuard()
        _ban(g, BAN_3H24)
        clock['t'] += 3600 + 60          # just past where the old clamp expired
        assert g.is_blocked(KEY) is True, 'the guard un-blocked an hour into a 3h24m ban'

    def test_the_measured_1907_request_no_longer_goes_out(self, clock):
        """Replays 17:22:30 -> 19:07:30 exactly."""
        g = RateLimitGuard()
        _ban(g, BAN_3H24)
        clock['t'] += 105 * 60           # 17:22:30 -> 19:07:30
        assert g.blocked_for(KEY) > 0, \
            'the balance prefetch would reach the network and re-discover the ban'

    def test_the_hourly_rediscovery_cycle_is_gone(self, clock):
        """The seven self-inflicted arms were all at ~1h multiples."""
        g = RateLimitGuard()
        _ban(g, BAN_3H24)
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
        _ban(g, BAN_3H24)
        clock['t'] += BAN_3H24 * _PROBE_AFTER_FRAC + rlg._PROBE_FIRST_S + 1
        assert g.blocked_for(KEY) == 0.0, 'no probe offered, so an early lift is missed'

    def test_the_gate_is_late_in_the_ban_not_hourly(self, clock):
        """0.9 x 3h24m is ~3h04m in, i.e. ~20 min before expiry."""
        g = RateLimitGuard()
        _ban(g, BAN_3H24)
        clock['t'] += 2 * 3600           # two hours in — still well before the gate
        assert g.blocked_for(KEY) > 0


class TestAbsurdExpiriesAreStillRejected:
    def test_a_year_long_expiry_is_clamped(self, clock):
        g = RateLimitGuard()
        _ban(g, 86400 * 365)
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
            _ban(g, BAN_3H24)
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
        _ban(g, BAN_3H24)
        assert len([t for t in sent if 'ban started' in t]) == 1, sent
