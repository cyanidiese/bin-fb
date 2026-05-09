from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal

from config.settings import Settings
from bot.risk_manager import RiskManager
from bot.notifier import Notifier

logger = logging.getLogger(__name__)


class OrderState(Enum):
    IDLE = auto()
    PLACING = auto()
    OPEN = auto()
    PARTIAL_EXIT = auto()
    CLOSED = auto()


@dataclass
class OpenOrder:
    symbol: str
    preset_name: str
    side: str
    entry_price: float
    tp_price: float
    sl_price: float
    quantity: float
    leverage: int
    exchange_order_id: str | None = None


class OrderExecutor:
    PLACING_TIMEOUT = 30.0

    def __init__(
        self,
        mode: Literal["test", "live"],
        settings: Settings,
        risk_manager: RiskManager,
        notifier: Notifier,
    ) -> None:
        self._mode = mode
        self._settings = settings
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._states: dict[str, OrderState] = {}
        self._open_orders: dict[str, OpenOrder] = {}
        self._placing_locks: dict[str, asyncio.Lock] = {}
        self._failure_counts: dict[str, int] = {}

        from config.risk_config import load_risk_config
        cfg = load_risk_config()
        self._consecutive_failure_threshold: int = cfg.get("consecutive_failure_threshold", 3)

    def get_state(self, symbol: str) -> OrderState:
        return self._states.get(symbol, OrderState.IDLE)

    def get_open_orders(self) -> dict[str, OpenOrder]:
        return dict(self._open_orders)

    def get_unrealised_pnl(self, current_prices: dict[str, float]) -> float:
        total = 0.0
        for symbol, order in self._open_orders.items():
            price = current_prices.get(symbol, order.entry_price)
            if order.side == 'BUY':
                pnl = (price - order.entry_price) / order.entry_price * 100 * order.quantity * order.entry_price
            else:
                pnl = (order.entry_price - price) / order.entry_price * 100 * order.quantity * order.entry_price
            total += pnl
        return total

    def _get_placing_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._placing_locks:
            self._placing_locks[symbol] = asyncio.Lock()
        return self._placing_locks[symbol]

    async def place_order(self, symbol: str, preset_name: str, side: str,
                          entry: float, tp: float, sl: float,
                          quantity: float, leverage: int) -> bool:
        lock = self._get_placing_lock(symbol)
        async with lock:
            self._states[symbol] = OrderState.PLACING
            try:
                order_id = await self._submit_to_exchange(symbol, side, quantity, tp, sl, leverage)
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name=preset_name, side=side,
                    entry_price=entry, tp_price=tp, sl_price=sl,
                    quantity=quantity, leverage=leverage, exchange_order_id=order_id,
                )
                self._states[symbol] = OrderState.OPEN
                self._record_success(symbol)
                logger.info(f"Order placed: {symbol} {side} @ {entry} TP={tp} SL={sl}")
                return True
            except Exception as exc:
                self._states[symbol] = OrderState.IDLE
                self._record_failure(symbol)
                logger.error(f"Order placement failed for {symbol}: {exc}")
                return False

    async def _submit_to_exchange(self, symbol: str, side: str, quantity: float,
                                   tp: float, sl: float, leverage: int) -> str | None:
        return None  # stub — wired in later

    async def close_all_orders_at_market(self) -> list[dict]:
        results = []
        for symbol, order in list(self._open_orders.items()):
            try:
                close_price = await self._market_close(symbol, order)
                pnl = self._calc_pnl(order, close_price)
                results.append({
                    "symbol": symbol,
                    "side": order.side,
                    "entry_price": order.entry_price,
                    "close_price": close_price,
                    "pnl_usdt": pnl,
                })
                del self._open_orders[symbol]
                self._states[symbol] = OrderState.IDLE
                logger.info(f"Closed {symbol} at market: entry={order.entry_price} close={close_price} pnl={pnl:.2f}")
            except Exception as exc:
                logger.error(f"Failed to close {symbol} at market: {exc}")
                self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
        return results

    async def _market_close(self, symbol: str, order: OpenOrder) -> float:
        return order.entry_price  # stub

    @staticmethod
    def _calc_pnl(order: OpenOrder, close_price: float) -> float:
        if order.side == 'BUY':
            return (close_price - order.entry_price) * order.quantity
        return (order.entry_price - close_price) * order.quantity

    def _record_failure(self, symbol: str) -> None:
        self._failure_counts[symbol] = self._failure_counts.get(symbol, 0) + 1
        if self._failure_counts[symbol] >= self._consecutive_failure_threshold:
            self._notifier.notify(
                "emergency",
                f"Order placement threshold reached: {symbol}",
                f"{self._failure_counts[symbol]} consecutive failures",
                "order_executor",
            )

    def _record_success(self, symbol: str) -> None:
        self._failure_counts[symbol] = 0
