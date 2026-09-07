"""Ban alerts must be readable in Telegram: no markup, no raw epoch numbers.

Two separate faults, both seen in production on 3822fff:

1. The body carried <b> tags. Notifier html-escapes the body deliberately — an API
   error containing '<' must not be able to break the message — so those tags rendered
   literally as "Endpoint: <b>testnet</b>".
2. Binance states the expiry as epoch milliseconds, and the Reason line passed its
   message through verbatim: "banned until 1788777598577". Unreadable in an alert.
"""
import html
import re
import time

import pytest

from bot.rate_limit_guard import (
    RateLimitGuard, humanize_epochs, _fmt_wall,
)

TAG = re.compile(r'</?[a-zA-Z][^>]{0,12}>')
RAW_EPOCH = re.compile(r'\b1[0-9]{9,12}\b')

REAL_MSG = ("APIError(code=-1003): Way too many requests; IP(15.158.242.71) "
            "banned until 1788777598577. Please use the websocket for live "
            "updates to avoid bans.")


# --------------------------------------------------------------------------- #
# humanize_epochs                                                             #
# --------------------------------------------------------------------------- #

def test_rewrites_the_ban_expiry_as_utc():
    out = humanize_epochs(REAL_MSG)
    assert '1788777598577' not in out
    assert '2026-09-07 10:39:58 UTC' in out


def test_rewrites_epoch_seconds_too():
    assert '2026-09-07 10:39:58 UTC' in humanize_epochs('until 1788777598')


def test_leaves_ordinary_numbers_alone():
    """An order id, a price or a quantity must survive untouched."""
    for text in ('orderId=4055123', 'qty=0.001', 'price=142.37',
                 'code=-1003', 'weight 2400', 'IP(15.158.242.71)'):
        assert humanize_epochs(text) == text, text


def test_leaves_implausible_epochs_alone():
    """Only values decoding to a sane date are rewritten."""
    assert humanize_epochs('1000000000000') == '1000000000000'   # year 2001
    assert humanize_epochs('9999999999999') == '9999999999999'   # year 2286


def test_is_idempotent():
    once = humanize_epochs(REAL_MSG)
    assert humanize_epochs(once) == once


def test_handles_empty_and_none_safely():
    assert humanize_epochs('') == ''
    assert humanize_epochs(None) == ''


# --------------------------------------------------------------------------- #
# _fmt_wall carries the date, not just a time                                 #
# --------------------------------------------------------------------------- #

def test_fmt_wall_includes_the_date():
    """A ban can cross midnight; a bare '00:41:58 UTC' is ambiguous."""
    out = _fmt_wall(1788777598.577)
    assert out == '2026-09-07 10:39:58 UTC', out


# --------------------------------------------------------------------------- #
# The announced messages, end to end                                          #
# --------------------------------------------------------------------------- #

def _capture(fn):
    sent = []
    g = RateLimitGuard()
    g.set_notifier(lambda lvl, title, body, src: sent.append((lvl, title, body)), mode='test')
    fn(g)
    return g, sent


def test_ban_started_body_has_no_markup_and_no_raw_epoch():
    future_ms = int((time.time() + 900) * 1000)
    msg = f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until {future_ms}."
    _, sent = _capture(lambda g: g.note_exception('testnet', Exception(msg)))
    assert sent, 'no ban-start notification was sent'
    level, title, body = sent[0]
    assert not TAG.search(body), f'markup in body: {body!r}'
    assert not TAG.search(title), f'markup in title: {title!r}'
    assert str(future_ms) not in body, 'raw epoch left in the body'
    assert 'UTC' in body


def test_ban_ended_body_has_no_markup():
    future_ms = int((time.time() + 900) * 1000)
    msg = f"APIError(code=-1003): banned until {future_ms}."

    def scenario(g):
        g.note_exception('testnet', Exception(msg))
        # A success starts a settling window rather than clearing outright, so drive
        # settling to completion to reach the ban-ended announcement.
        g._next_probe['testnet'] = time.monotonic() - 1
        assert g.blocked_for('testnet') == 0.0
        g.note_success('testnet')
        g._settle_until['testnet'] = time.monotonic() - 0.01
        assert g.blocked_for('testnet') == 0.0

    _, sent = _capture(scenario)
    ended = [s for s in sent if 'ended' in s[1]]
    assert ended, 'no ban-end notification was sent'
    for _, title, body in ended:
        assert not TAG.search(body), f'markup in body: {body!r}'
        assert not RAW_EPOCH.search(body), f'raw epoch in body: {body!r}'


def test_body_survives_notifier_escaping_unchanged():
    """The real end-to-end property: what the reader sees equals what we wrote."""
    future_ms = int((time.time() + 900) * 1000)
    msg = f"APIError(code=-1003): banned until {future_ms}."
    _, sent = _capture(lambda g: g.note_exception('testnet', Exception(msg)))
    _, _, body = sent[0]
    assert html.escape(body) == body, 'body changes when escaped — it contains markup'
