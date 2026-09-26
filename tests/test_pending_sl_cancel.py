# tests/test_pending_sl_cancel.py
#
# A SL algo-order cancel that fails for any reason other than -2011 leaves a live
# reduce-only STOP_MARKET on the exchange, which would fire against the next position on
# the symbol. It must be queued, persisted, retried, and must block new placement.
import json
from unittest.mock import MagicMock, patch

import pytest

from bot.order_executor import OrderExecutor
from bot.rate_limit_guard import guard as rl_guard


class FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"APIError(code={code}): {msg}")
        self.code = code


def make_executor(tmp_path):
    feed = MagicMock()
    feed._is_testnet = True
    with patch('bot.order_executor.load_risk_config', return_value={'consecutive_failure_threshold': 3}):
        return OrderExecutor('test', MagicMock(), MagicMock(), MagicMock(),
                             data_feed=feed, project_root=tmp_path)


@pytest.fixture(autouse=True)
def _clean_guard():
    rl_guard.reset()
    yield
    rl_guard.reset()


@pytest.mark.asyncio
async def test_unknown_order_counts_as_gone(tmp_path):
    ex = make_executor(tmp_path)
    ex._feed.client.futures_cancel_algo_order.side_effect = FakeAPIError(-2011, 'Unknown order sent.')
    assert await ex._cancel_exchange_order('INJUSDT', '123') is True
    assert ex._pending_sl_cancels == {}


@pytest.mark.asyncio
async def test_failed_cancel_is_queued_and_persisted(tmp_path):
    ex = make_executor(tmp_path)
    ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
    assert await ex._cancel_exchange_order('INJUSDT', '123') is False
    assert ex._pending_sl_cancels == {'INJUSDT': ['123']}
    saved = json.loads((tmp_path / 'data' / 'pending_sl_cancels_test.json').read_text())
    assert saved == {'INJUSDT': ['123']}

    # A restart picks it back up.
    ex2 = make_executor(tmp_path)
    assert ex2._pending_sl_cancels == {'INJUSDT': ['123']}


@pytest.mark.asyncio
async def test_retry_clears_queue_on_success(tmp_path):
    ex = make_executor(tmp_path)
    ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
    await ex._cancel_exchange_order('INJUSDT', '123')

    ex._feed.client.futures_cancel_algo_order.side_effect = None
    await ex.retry_pending_sl_cancels()
    assert ex._pending_sl_cancels == {}
    saved = json.loads((tmp_path / 'data' / 'pending_sl_cancels_test.json').read_text())
    assert saved == {}


@pytest.mark.asyncio
async def test_retry_waits_out_a_ban(tmp_path):
    ex = make_executor(tmp_path)
    ban = FakeAPIError(-1003, 'Way too many requests; IP(1.2.3.4) banned until 9999999999999.')
    ex._feed.client.futures_cancel_algo_order.side_effect = ban
    await ex._cancel_exchange_order('INJUSDT', '123')
    assert ex._pending_sl_cancels == {'INJUSDT': ['123']}
    calls = ex._feed.client.futures_cancel_algo_order.call_count

    await ex.retry_pending_sl_cancels()
    # Guard is armed: no call made while banned.
    assert ex._feed.client.futures_cancel_algo_order.call_count == calls
    assert ex._pending_sl_cancels == {'INJUSDT': ['123']}


@pytest.mark.asyncio
async def test_placement_blocked_while_old_sl_live(tmp_path):
    ex = make_executor(tmp_path)
    ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
    await ex._cancel_exchange_order('INJUSDT', '123')

    submitted = []

    async def fake_submit(*a, **k):
        submitted.append(a)
        return 'order1'

    ex._submit_to_exchange = fake_submit
    ok = await ex.place_order('INJUSDT', 'p', 'BUY', 10, 11, 9, 1.0, 5)
    assert ok is False
    assert submitted == []


@pytest.mark.asyncio
async def test_placement_proceeds_once_retry_succeeds(tmp_path):
    ex = make_executor(tmp_path)
    ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
    await ex._cancel_exchange_order('INJUSDT', '123')
    ex._feed.client.futures_cancel_algo_order.side_effect = None

    reached = []

    async def fake_round(symbol, qty):
        reached.append(symbol)
        return 0.0   # stop right after the gate: "rounds to 0" is a clean early exit

    ex.round_quantity = fake_round
    await ex.place_order('INJUSDT', 'p', 'BUY', 10, 11, 9, 1.0, 5)
    assert ex._pending_sl_cancels == {}
    assert reached == ['INJUSDT']
