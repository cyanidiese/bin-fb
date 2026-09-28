"""Test-wide safety net: no test may touch the network.

python-binance's Client.__init__ calls self.ping() — a live request to Binance. Several
tests construct a real DataFeed, so the suite made network calls and intermittently hung
past 10 minutes or failed (tests/test_symbol_hot_subscribe.py, 2026-09-28) whenever the
network or the testnet was slow. Tests that need exchange data use fakes.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_binance_ping(monkeypatch):
    from binance.client import Client
    monkeypatch.setattr(Client, 'ping', lambda self: {})
