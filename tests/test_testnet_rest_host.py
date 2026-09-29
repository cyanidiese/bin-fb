"""Test-mode REST must go to demo-fapi.binance.com: the old testnet host sits behind
CloudFront, whose shared address carries other users' weight and gets us -1003 banned."""
from bot.data_feed import _FUTURES_REST_TESTNET, _trading_client


def test_testnet_futures_calls_use_demo_fapi():
    c = _trading_client('k', 's', True)
    assert c._create_futures_api_uri('account', 2).startswith('https://demo-fapi.binance.com/fapi/v2/')
    assert _FUTURES_REST_TESTNET == 'https://demo-fapi.binance.com/fapi'


def test_live_futures_calls_are_untouched():
    c = _trading_client('k', 's', False)
    assert c._create_futures_api_uri('account', 2).startswith('https://fapi.binance.com/fapi/v2/')
