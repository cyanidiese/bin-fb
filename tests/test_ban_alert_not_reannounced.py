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
import time

import pytest

from bot import rate_limit_guard as rlg
from bot.rate_limit_guard import RateLimitGuard

KEY = 'testnet'


@pytest.fixture
def clock(monkeypatch):
    state = {'t': 10_000.0}
    monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
    return state


@pytest.fixture
def g():
    return RateLimitGuard()


def _capture(guard, mode='test'):
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


class TestTheSameBanAnnouncesOnce:
    def test_a_rediscovery_two_hours_later_does_not_realert(self, g, clock):
        """The measured case: identical expiry at 17:22:30 and 19:07:30."""
        sent = _capture(g)
        expiry = time.time() + 3 * 3600 + 24 * 60      # 3h24m out, as on the day
        _ban_at(g, expiry)
        assert len(_started(sent)) == 1, 'premise: the first ban alerts'

        clock['t'] += 105 * 60                          # 17:22:30 -> 19:07:30
        _ban_at(g, expiry)                              # same epoch, same ban
        assert len(_started(sent)) == 1, \
            'the same ban was announced twice — this is the reported bug'

    def test_it_holds_across_several_rediscoveries(self, g, clock):
        sent = _capture(g)
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
        sent = _capture(g)
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
        sent = _capture(g)
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
        sent = _capture(g)
        _ban_at(g, time.time() + 300)
        clock['t'] += 400                        # first ban expires
        g.note_success(KEY)
        clock['t'] += rlg._SETTLE_S + 1
        assert g.blocked_for(KEY) == 0.0, 'premise: recovered through _clear()'
        _ban_at(g, time.time() + 7200)           # a later expiry — genuinely new
        assert len(_started(sent)) == 2

    def test_a_message_with_no_parseable_expiry_still_announces(self, g, clock):
        """Falls back to the old deadline key; those are short assumed blocks."""
        sent = _capture(g)
        g.note_exception(KEY, Exception('APIError(code=-1003): Way too many requests'))
        assert len(_started(sent)) == 1

    def test_reset_lets_the_next_ban_announce(self, g, clock):
        sent = _capture(g)
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
        sent = _capture(g)
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
