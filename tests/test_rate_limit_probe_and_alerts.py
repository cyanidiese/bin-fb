"""The stated ban expiry is an upper bound, and bans must be announced.

Measured 2026-09-06: Binance reported a ban until 18:48:59, but futures_account
already succeeded at 18:07 — 41 minutes early. Waiting out the stated window would
have paused balance and kline reads for nothing, so the guard probes periodically.
"""
import time

import pytest

from bot import rate_limit_guard as rlg
from bot.rate_limit_guard import RateLimitGuard


def _ban(seconds_ahead: float) -> Exception:
    ms = int((time.time() + seconds_ahead) * 1000)
    return Exception(f"APIError(code=-1003): Way too many requests; "
                     f"IP(15.158.242.76) banned until {ms}.")


@pytest.fixture
def g():
    return RateLimitGuard()


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock."""
    state = {'t': 10_000.0}
    monkeypatch.setattr(rlg.time, 'monotonic', lambda: state['t'])
    return state


def _to_probe_window(clock, ban_secs: float) -> None:
    """Advance to where a probe becomes permissible.

    Since 2026-09-08 probing does not begin until _PROBE_AFTER_FRAC of the stated ban has
    elapsed: a probe fired minutes into an hour-long ban proves only that the CloudFront
    edge it reached is clear, and the traffic that follows walks into a banned edge and
    extends it (+336 min measured in one day). These tests are about what happens once a
    probe IS offered; the gate itself is covered in tests/test_probe_waits_out_the_ban.py.
    """
    clock['t'] += ban_secs * rlg._PROBE_AFTER_FRAC + rlg._PROBE_FIRST_S + 1


# ── half-open probing ────────────────────────────────────────────────────────

def test_blocks_immediately_after_a_ban(g, clock):
    g.note_exception('testnet', _ban(3000))
    assert g.blocked_for('testnet') > 0


def test_lets_one_probe_through_after_the_probe_interval(g, clock):
    g.note_exception('testnet', _ban(3000))
    _to_probe_window(clock, 3000)
    assert g.blocked_for('testnet') == 0.0, 'no probe was allowed'
    # and the very next caller is blocked again — one probe, not an open floodgate
    assert g.blocked_for('testnet') > 0


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


def test_successful_probes_clear_the_block_early(g, clock):
    g.note_exception('testnet', _ban(3000))
    _recover(g, clock)
    assert g.is_blocked('testnet') is False


def test_a_single_successful_probe_is_not_enough(g, clock):
    """The stampede guard: one success must not re-open the endpoint for 15 symbols."""
    g.note_exception('testnet', _ban(3000))
    _to_probe_window(clock, 3000)
    assert g.blocked_for('testnet') == 0.0
    g.note_success('testnet')
    assert g.is_blocked('testnet') is True


def test_failed_probe_backs_off(g, clock):
    g.note_exception('testnet', _ban(3000))
    first = g._probe_delay['testnet']
    clock['t'] += first + 1
    g.blocked_for('testnet')
    g.note_exception('testnet', _ban(3000))   # probe failed
    assert g._probe_delay['testnet'] == first * 2


def test_backoff_is_capped(g, clock):
    g.note_exception('testnet', _ban(3600))
    for _ in range(20):
        clock['t'] += g._probe_delay['testnet'] + 1
        g.blocked_for('testnet')
        g.note_exception('testnet', _ban(3600))
    assert g._probe_delay['testnet'] <= rlg._PROBE_MAX_S


def test_note_success_on_a_clear_endpoint_is_harmless(g, clock):
    g.note_success('testnet')
    assert g.is_blocked('testnet') is False


# ── notifications ────────────────────────────────────────────────────────────

def _capture(g, mode='test'):
    sent = []
    g.set_notifier(lambda lvl, title, body, src: sent.append((lvl, title, body, src)), mode=mode)
    return sent


def test_messages_carry_no_html_markup(g, clock):
    """Notifier.notify() html-escapes the body, so markup here renders literally."""
    sent = _capture(g)
    g.note_exception('testnet', _ban(3000))
    _recover(g, clock)
    assert len(sent) == 2
    for _lvl, title, body, _src in sent:
        for tag in ('<b>', '</b>', '<i>', '<code>', '&lt;'):
            assert tag not in body, f'{tag!r} would render literally in Telegram'
            assert tag not in title


def test_ban_start_is_announced_with_expiry_and_mode(g, clock):
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


def test_ban_end_is_announced(g, clock):
    sent = _capture(g)
    g.note_exception('testnet', _ban(3000))
    _recover(g, clock)
    assert len(sent) == 2
    lvl, title, body, _ = sent[1]
    assert lvl == 'info'
    assert 'ended' in title.lower()
    assert 'early' in body, 'should report recovering before the stated expiry'


def test_only_one_start_alert_per_ban(g, clock):
    """A ban spans many candles — one alert, not one per blocked call."""
    sent = _capture(g)
    for _ in range(5):
        g.note_exception('testnet', _ban(3000))
        clock['t'] += 1
    assert sum(1 for s in sent if 'started' in s[1].lower()) == 1


def test_expiry_lapse_also_announces_the_end(g, clock):
    sent = _capture(g)
    g.note_exception('testnet', _ban(120))
    clock['t'] += 200
    g.blocked_for('testnet')
    assert any('ended' in s[1].lower() for s in sent)


def test_unrelated_error_announces_nothing(g, clock):
    sent = _capture(g)
    g.note_exception('testnet', Exception('APIError(code=-2019): Margin is insufficient'))
    assert sent == []


def test_a_broken_notifier_never_breaks_trading(g, clock):
    def boom(*a, **k):
        raise RuntimeError('telegram down')
    g.set_notifier(boom, mode='test')
    g.note_exception('testnet', _ban(3000))     # must not raise
    assert g.is_blocked('testnet') is True
