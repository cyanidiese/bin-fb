"""fetch_account_balance must report the USDT wallet, not totalWalletBalance.

On 2026-08-18 totalWalletBalance returned exactly 5000.0 — the account's USDC
holding — while real USDT was 3043.94. See bot/order_executor.py for the full
incident note.
"""
import asyncio
import types

import pytest

from bot.order_executor import OrderExecutor


def _run(coro):
    """Drive a coroutine without disturbing the shared event loop.

    asyncio.run() closes the loop and clears the current-loop slot, which breaks
    sibling tests that still use the deprecated asyncio.get_event_loop() (e.g.
    tests/test_telegram_menu.py). Restore whatever was set before we started.
    """
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def _executor(account_payload):
    """OrderExecutor with a stub feed whose client returns account_payload."""
    ex = OrderExecutor.__new__(OrderExecutor)  # bypass __init__ / real client
    ex._feed = types.SimpleNamespace(
        client=types.SimpleNamespace(futures_account=lambda: account_payload)
    )
    return ex


# The exact shape observed on the live account during the incident.
_INCIDENT_PAYLOAD = {
    'totalWalletBalance': '5000.00000000',
    'assets': [
        {'asset': 'BTC', 'walletBalance': '0.01000000'},
        {'asset': 'USDT', 'walletBalance': '3043.94420611'},
        {'asset': 'USDC', 'walletBalance': '5000.00000000'},
    ],
}


def test_returns_usdt_wallet_not_total():
    bal = _run(_executor(_INCIDENT_PAYLOAD).fetch_account_balance())
    assert bal == pytest.approx(3043.94420611)


def test_ignores_larger_non_usdt_holdings():
    """A big USDC balance must not inflate the reading."""
    bal = _run(_executor(_INCIDENT_PAYLOAD).fetch_account_balance())
    assert bal != pytest.approx(5000.0)


def test_falls_back_to_total_when_no_usdt_entry():
    payload = {
        'totalWalletBalance': '1234.56',
        'assets': [{'asset': 'BTC', 'walletBalance': '0.01'}],
    }
    bal = _run(_executor(payload).fetch_account_balance())
    assert bal == pytest.approx(1234.56)


def test_missing_assets_key_falls_back():
    bal = _run(_executor({'totalWalletBalance': '900.0'}).fetch_account_balance())
    assert bal == pytest.approx(900.0)


def test_zero_on_api_error():
    def boom():
        raise RuntimeError("APIError(code=-1003): Way too many requests")

    ex = OrderExecutor.__new__(OrderExecutor)
    ex._feed = types.SimpleNamespace(client=types.SimpleNamespace(futures_account=boom))
    assert _run(ex.fetch_account_balance()) == 0.0


def test_zero_when_no_feed():
    ex = OrderExecutor.__new__(OrderExecutor)
    ex._feed = None
    assert _run(ex.fetch_account_balance()) == 0.0
