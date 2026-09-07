"""Locking a preset must apply to the instance whose table is on screen.

The Trades page reads locks for the VIEWED instance (Primary/Shadow toggle) but the
route wrote to whichever mode the bot happens to be running. So clicking the padlock in
the Shadow view silently locked the preset for test — the wrong instance — and the icon
never updated, because the page re-read live.

Ranks are already fully independent between instances: only 1 of 15 symbols shares a #1
preset between test and live. Locks have to follow the same separation.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE = (ROOT / 'dashboard/app/api/risk/lock-preset/route.ts').read_text()
PAGE = (ROOT / 'dashboard/app/trades/page.tsx').read_text()


def test_the_route_accepts_a_mode_from_the_caller():
    assert 'requestedMode' in ROUTE


def test_the_route_validates_the_mode():
    """An arbitrary string would create a junk key in locked_presets."""
    assert "=== 'test'" in ROUTE and "=== 'live'" in ROUTE


def test_the_route_falls_back_to_the_bot_mode():
    """Callers that do not specify an instance must keep working."""
    assert 'currentMode()' in ROUTE


def test_the_page_sends_the_viewed_instance():
    i = PAGE.index("'/api/risk/lock-preset'")
    body = PAGE[i:i + 700]
    assert 'mode: dataMode' in body, 'the lock would land on the wrong instance'


def test_the_page_reads_and_writes_the_same_instance():
    """Read and write must agree, or the icon lies about what happened."""
    assert 'lockedPresetsFor(config, dataMode)' in PAGE
    i = PAGE.index("'/api/risk/lock-preset'")
    assert 'dataMode' in PAGE[i:i + 700]
