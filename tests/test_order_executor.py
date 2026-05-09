import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from bot.order_executor import OrderExecutor, OrderState

def make_executor(mode='test'):
    settings = MagicMock()
    settings.trading_mode = mode
    risk = MagicMock()
    risk.can_open_sync.return_value = True
    notifier = MagicMock()
    notifier.notify = MagicMock()
    return OrderExecutor(mode=mode, settings=settings, risk_manager=risk, notifier=notifier)

def test_initial_state_is_idle():
    ex = make_executor()
    assert ex.get_state('BTCUSDT') == OrderState.IDLE

def test_consecutive_failure_increments():
    ex = make_executor()
    ex._record_failure('BTCUSDT')
    ex._record_failure('BTCUSDT')
    assert ex._failure_counts['BTCUSDT'] == 2

def test_failure_reset_on_success():
    ex = make_executor()
    ex._record_failure('BTCUSDT')
    ex._record_success('BTCUSDT')
    assert ex._failure_counts['BTCUSDT'] == 0

@pytest.mark.asyncio
async def test_close_all_returns_list():
    ex = make_executor()
    result = await ex.close_all_orders_at_market()
    assert isinstance(result, list)

def test_threshold_fires_notifier():
    ex = make_executor()
    ex._consecutive_failure_threshold = 2
    ex._record_failure('BTCUSDT')
    ex._record_failure('BTCUSDT')
    ex._notifier.notify.assert_called()
