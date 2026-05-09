from __future__ import annotations

import asyncio
import logging
import math
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum, auto
from typing import TYPE_CHECKING, Literal, Optional

from config.settings import Settings
from config.risk_config import load_risk_config
from bot.risk_manager import RiskManager
from bot.notifier import Notifier
from bot.fake_order import FakeOrder

if TYPE_CHECKING:
    from bot.data_feed import DataFeed
    from bot.symbol_registry import SymbolRegistry

logger = logging.getLogger(__name__)


class OrderState(Enum):
    IDLE = auto()
    PLACING = auto()
    OPEN = auto()
    PARTIAL_EXIT = auto()
    CLOSED = auto()


class FundsError(Exception):
    """Wraps exchange errors due to insufficient margin/balance. Does not count as a failure."""


class SymbolError(Exception):
    """Wraps exchange errors that mean the symbol should be disabled."""
    def __init__(self, symbol: str, reason: str) -> None:
        super().__init__(reason)
        self.symbol = symbol
        self.reason = reason


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
    partial_take_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    exchange_order_id: str | None = None


class OrderExecutor:
    PLACING_TIMEOUT = 30.0

    def __init__(
        self,
        mode: Literal["test", "live"],
        settings: Settings,
        risk_manager: RiskManager,
        notifier: Notifier,
        data_feed: 'DataFeed | None' = None,
        symbol_registry: 'SymbolRegistry | None' = None,
    ) -> None:
        self._mode = mode
        self._settings = settings
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._feed = data_feed
        self._symbol_registry = symbol_registry

        self._states: dict[str, OrderState] = {}
        self._open_orders: dict[str, OpenOrder] = {}
        self._fake_orders: dict[str, FakeOrder] = {}  # software TP/SL/trailing tracking
        self._placing_locks: dict[str, asyncio.Lock] = {}
        self._failure_counts: dict[str, int] = {}
        self._lot_cache: dict[str, dict] = {}  # {symbol: {step_size, min_qty, min_notional}}
        self._bracket_max: dict[str, int] = {}  # symbol → max leverage from first bracket
        self._candle_index: int = 0  # incremented on each check_all_orders call

        cfg = load_risk_config()
        self._consecutive_failure_threshold: int = cfg.get("consecutive_failure_threshold", 3)

    # ------------------------------------------------------------------ #
    # Public read interface                                                #
    # ------------------------------------------------------------------ #

    def get_state(self, symbol: str) -> OrderState:
        return self._states.get(symbol, OrderState.IDLE)

    def get_open_orders(self) -> dict[str, OpenOrder]:
        return dict(self._open_orders)

    def get_unrealised_pnl(self, current_prices: dict[str, float]) -> float:
        total = 0.0
        for symbol, order in self._open_orders.items():
            price = current_prices.get(symbol, order.entry_price)
            if order.side == 'BUY':
                pnl = (price - order.entry_price) * order.quantity
            else:
                pnl = (order.entry_price - price) * order.quantity
            total += pnl
        return total

    # ------------------------------------------------------------------ #
    # Order placement                                                      #
    # ------------------------------------------------------------------ #

    async def place_order(
        self,
        symbol: str,
        preset_name: str,
        side: str,
        entry: float,
        tp: float,
        sl: float,
        quantity: float,
        leverage: int,
        partial_take_pct: float = 0.0,
        trailing_stop_pct: float = 0.0,
        level: Optional[int] = None,
        signal_type: str = '',
    ) -> bool:
        lock = self._get_placing_lock(symbol)
        async with lock:
            self._states[symbol] = OrderState.PLACING
            try:
                rounded_qty = await self.round_quantity(symbol, quantity)
                if rounded_qty <= 0:
                    logger.warning(f"[{symbol}] Quantity rounds to 0 — skipping order")
                    self._states[symbol] = OrderState.IDLE
                    return False

                order_id = await self._submit_to_exchange(symbol, side, rounded_qty, leverage)
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name=preset_name, side=side,
                    entry_price=entry, tp_price=tp, sl_price=sl,
                    quantity=rounded_qty, leverage=leverage,
                    partial_take_pct=partial_take_pct,
                    trailing_stop_pct=trailing_stop_pct,
                    exchange_order_id=order_id,
                )
                # Create software FakeOrder for trailing stop / TP / SL monitoring
                self._fake_orders[symbol] = FakeOrder(
                    side=side,
                    entry_price=entry,
                    tp=tp,
                    sl=sl if sl else entry * (0.99 if side == 'BUY' else 1.01),
                    level=level,
                    signal_type=signal_type,
                    candle_index=self._candle_index,
                    partial_take_pct=partial_take_pct,
                    trailing_stop_pct=trailing_stop_pct,
                )
                self._states[symbol] = OrderState.OPEN
                self._record_success(symbol)
                logger.info(
                    f"Order placed: {symbol} {side} qty={rounded_qty} "
                    f"entry={entry} TP={tp} SL={sl} preset={preset_name}"
                )
                return True
            except FundsError as exc:
                self._states[symbol] = OrderState.IDLE
                logger.warning(f"[{symbol}] Order skipped — insufficient funds: {exc}")
                return False
            except SymbolError as sym_exc:
                self._states[symbol] = OrderState.IDLE
                await self._auto_disable(sym_exc.symbol, sym_exc.reason)
                return False
            except Exception as exc:
                self._states[symbol] = OrderState.IDLE
                threshold_hit = self._record_failure(symbol)
                if threshold_hit:
                    await self._auto_disable(symbol, f"consecutive_failures: {exc}")
                logger.error(f"Order placement failed for {symbol}: {exc}")
                return False

    # ------------------------------------------------------------------ #
    # Per-candle order monitoring (software TP/SL/trailing)               #
    # ------------------------------------------------------------------ #

    async def check_all_orders(
        self,
        high: float,
        low: float,
        candle_open: float,
        candle_close: float,
    ) -> list[dict]:
        """
        Call once per closed candle. Checks all open FakeOrders for TP/SL/trail triggers.
        Returns list of closed order result dicts for the caller to record in VirtualTracker.
        """
        self._candle_index += 1
        closed = []
        for symbol, fake_order in list(self._fake_orders.items()):
            result = fake_order.check(
                high, low, self._candle_index,
                candle_open=candle_open,
                candle_close=candle_close,
            )
            if result is None:
                continue

            open_order = self._open_orders.get(symbol)
            if open_order is None:
                del self._fake_orders[symbol]
                continue

            software_close_price = fake_order.close_price or open_order.entry_price
            # Attempt to close on the exchange at market
            try:
                actual_close_price = await self._market_close(symbol, open_order)
            except Exception as exc:
                logger.error(f"Market close failed for {symbol}: {exc}")
                self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
                actual_close_price = software_close_price

            pnl = self._calc_pnl(open_order, actual_close_price)
            closed.append({
                "symbol": symbol,
                "preset_name": open_order.preset_name,
                "result": result,
                "pnl_usdt": pnl,
                "side": open_order.side,
                "entry_price": open_order.entry_price,
                "close_price": actual_close_price,
            })
            del self._open_orders[symbol]
            del self._fake_orders[symbol]
            self._states[symbol] = OrderState.IDLE
            self._record_success(symbol)
            logger.info(
                f"Order closed: {symbol} result={result} "
                f"entry={open_order.entry_price} close={actual_close_price:.4f} pnl={pnl:.2f} USDT"
            )
        return closed

    async def check_symbol_price(self, symbol: str, current_price: float) -> list[dict]:
        """Call on every price tick for a specific symbol. Checks that symbol's FakeOrder only."""
        fake_order = self._fake_orders.get(symbol)
        if fake_order is None:
            return []

        result = fake_order.check_price(current_price)
        if result is None:
            return []

        open_order = self._open_orders.get(symbol)
        if open_order is None:
            del self._fake_orders[symbol]
            return []

        software_close_price = fake_order.close_price or open_order.entry_price
        try:
            actual_close_price = await self._market_close(symbol, open_order)
        except Exception as exc:
            logger.error(f"Market close failed for {symbol}: {exc}")
            self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
            actual_close_price = software_close_price

        pnl = self._calc_pnl(open_order, actual_close_price)
        del self._open_orders[symbol]
        del self._fake_orders[symbol]
        self._states[symbol] = OrderState.IDLE
        self._record_success(symbol)
        logger.info(
            f"Order closed (price tick): {symbol} result={result} "
            f"entry={open_order.entry_price} close={actual_close_price:.4f} pnl={pnl:.2f} USDT"
        )
        return [{
            "symbol": symbol,
            "preset_name": open_order.preset_name,
            "result": result,
            "pnl_usdt": pnl,
            "side": open_order.side,
            "entry_price": open_order.entry_price,
            "close_price": actual_close_price,
        }]

    # ------------------------------------------------------------------ #
    # Bulk close                                                           #
    # ------------------------------------------------------------------ #

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
                logger.info(
                    f"Bulk close: {symbol} entry={order.entry_price} "
                    f"close={close_price} pnl={pnl:.2f}"
                )
            except Exception as exc:
                logger.error(f"Failed to close {symbol} at market: {exc}")
                self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
            finally:
                self._open_orders.pop(symbol, None)
                self._fake_orders.pop(symbol, None)
                self._states[symbol] = OrderState.IDLE
        return results

    async def close_order(self, symbol: str) -> dict | None:
        """Close a single open order at market. Returns result dict or None if not open."""
        order = self._open_orders.get(symbol)
        if order is None:
            return None
        try:
            close_price = await self._market_close(symbol, order)
            pnl = self._calc_pnl(order, close_price)
            result = {
                "symbol": symbol,
                "side": order.side,
                "entry_price": order.entry_price,
                "close_price": close_price,
                "pnl_usdt": pnl,
            }
            logger.info(f"Closed {symbol}: entry={order.entry_price} close={close_price} pnl={pnl:.2f}")
            return result
        except Exception as exc:
            logger.error(f"Failed to close {symbol}: {exc}")
            self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
            return None
        finally:
            self._open_orders.pop(symbol, None)
            self._fake_orders.pop(symbol, None)
            self._states[symbol] = OrderState.IDLE

    # ------------------------------------------------------------------ #
    # Exchange integration                                                 #
    # ------------------------------------------------------------------ #

    async def _submit_to_exchange(self, symbol: str, side: str, quantity: float, leverage: int) -> str | None:
        if self._feed is None:
            return None
        client = self._feed.client
        try:
            await asyncio.to_thread(
                client.futures_change_leverage,
                symbol=symbol,
                leverage=leverage,
            )
        except Exception as exc:
            logger.warning(f"[{symbol}] Could not set leverage={leverage}: {exc}")

        try:
            result = await asyncio.to_thread(
                client.futures_create_order,
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=str(quantity),
            )
            return str(result.get('orderId'))
        except Exception as exc:
            exc_str = str(exc).lower()
            exc_code = getattr(exc, 'code', None)

            if exc_code in (-2019, -1013):
                raise FundsError(str(exc)) from exc

            is_symbol_error = (
                exc_code == -1121
                or (exc_code == -2010 and "perpetual" in exc_str)
                or "is not available" in exc_str
            )
            if is_symbol_error:
                raise SymbolError(symbol, str(exc)) from exc

            raise  # other errors → caller records failure

    async def _market_close(self, symbol: str, order: OpenOrder) -> float:
        if self._feed is None:
            return order.entry_price
        client = self._feed.client
        close_side = 'SELL' if order.side == 'BUY' else 'BUY'
        result = await asyncio.to_thread(
            client.futures_create_order,
            symbol=symbol,
            side=close_side,
            type='MARKET',
            quantity=str(order.quantity),
            reduceOnly=True,
        )
        avg_price = float(result.get('avgPrice', 0) or 0)
        return avg_price if avg_price > 0 else order.entry_price

    # ------------------------------------------------------------------ #
    # Account / position management                                        #
    # ------------------------------------------------------------------ #

    async def reconcile_with_exchange(self) -> None:
        """
        On startup: fetch open positions from exchange and rebuild _open_orders state.
        Reconciled orders have no FakeOrder so they won't be monitored for trailing stop —
        they will be closed via close_order() or close_all_orders_at_market() only.
        """
        if self._feed is None:
            return
        try:
            positions = await asyncio.to_thread(self._feed.client.futures_position_information)
            count = 0
            for pos in positions:
                symbol = pos['symbol']
                amt = float(pos.get('positionAmt', 0))
                if amt == 0:
                    continue
                entry_price = float(pos.get('entryPrice', 0))
                side = 'BUY' if amt > 0 else 'SELL'
                quantity = abs(amt)
                leverage = int(float(pos.get('leverage', 1)))
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name='reconciled', side=side,
                    entry_price=entry_price, tp_price=0.0, sl_price=0.0,
                    quantity=quantity, leverage=leverage,
                )
                self._states[symbol] = OrderState.OPEN
                count += 1
                logger.warning(
                    f"Reconciled open position: {symbol} {side} "
                    f"qty={quantity} @ {entry_price} lev={leverage}x"
                )
            if count == 0:
                logger.info("Reconciliation complete: no open positions found")
            else:
                self._notifier.notify(
                    "warning",
                    f"Reconciled {count} open position(s) from exchange",
                    "These positions have no software trailing stop — manage manually or let them close via normal flow",
                    "order_executor",
                )
        except Exception as exc:
            logger.warning(f"Reconciliation failed: {exc}")

    async def fetch_account_balance(self) -> float:
        """Returns totalWalletBalance from the futures account, or 0.0 on error."""
        if self._feed is None:
            return 0.0
        try:
            account = await asyncio.to_thread(self._feed.client.futures_account)
            return float(account.get('totalWalletBalance', 0) or 0)
        except Exception as exc:
            logger.warning(f"Failed to fetch account balance: {exc}")
            return 0.0

    def reset_for_mode_switch(self, new_mode: str) -> None:
        """Call after close_all_orders_at_market() when switching modes."""
        self._mode = new_mode
        self._lot_cache.clear()  # re-fetch lot sizes for the new endpoint
        logger.info(f"OrderExecutor mode reset to {new_mode}")

    async def check_symbols_on_exchange(self, symbols: list[str]) -> None:
        """Startup check: disable any symbol that is not TRADING/PERPETUAL on the exchange."""
        if self._feed is None or self._symbol_registry is None:
            return
        try:
            info = await asyncio.to_thread(self._feed.client.futures_exchange_info)
            exchange_map = {s['symbol']: s for s in info.get('symbols', [])}
            for symbol in list(symbols):
                sym_info = exchange_map.get(symbol)
                if sym_info is None:
                    await self._auto_disable(symbol, "symbol not found on exchange")
                    continue
                status = sym_info.get('status', '')
                contract_type = sym_info.get('contractType', '')
                if status != 'TRADING' or contract_type != 'PERPETUAL':
                    reason = f"status={status} contractType={contract_type}"
                    await self._auto_disable(symbol, reason)
        except Exception as exc:
            logger.warning(f"check_symbols_on_exchange failed: {exc}")

    async def _auto_disable(self, symbol: str, reason: str) -> None:
        """Disable symbol, close any open order, notify. Exit if all symbols are now disabled."""
        logger.error(f"Auto-disabling {symbol}: {reason}")
        if self._symbol_registry is not None:
            self._symbol_registry.disable(symbol, reason)
            if self._symbol_registry.all_disabled():
                self._notifier.notify(
                    "emergency",
                    "All symbols disabled — bot cannot continue",
                    reason,
                    "order_executor",
                )
                sys.exit(1)
        await self.close_order(symbol)
        self._notifier.notify("emergency", f"Symbol {symbol} disabled", reason, "order_executor")

    # ------------------------------------------------------------------ #
    # LOT_SIZE helpers                                                     #
    # ------------------------------------------------------------------ #

    async def get_min_notional(self, symbol: str) -> float:
        """Get the minimum notional value (price × quantity) for a symbol."""
        lot = await self._ensure_lot_size(symbol)
        return lot.get('min_notional', 0.0)

    def get_bracket_max(self, symbol: str) -> int:
        """Get the maximum leverage allowed for a symbol from cached brackets. Defaults to 20."""
        return self._bracket_max.get(symbol, 20)

    async def fetch_leverage_brackets(self, symbols: list[str]) -> None:
        """
        Fetch leverage brackets for each symbol from the exchange and cache the max leverage.
        Processes symbols individually to ensure one failure doesn't block the rest.
        """
        if self._feed is None:
            return
        for symbol in symbols:
            try:
                result = await asyncio.to_thread(
                    self._feed.client.futures_leverage_bracket,
                    symbol=symbol,
                )
                if result:
                    brackets = result[0].get('brackets', [])
                    if brackets:
                        self._bracket_max[symbol] = int(brackets[0]['initialLeverage'])
            except Exception as exc:
                logger.warning(f"[{symbol}] Failed to fetch leverage bracket: {exc}")

    async def round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity DOWN to the symbol's LOT_SIZE step, respecting minQty."""
        lot = await self._ensure_lot_size(symbol)
        step = lot['step_size']
        min_qty = lot['min_qty']
        # Use Decimal for exact rounding
        d_qty = Decimal(str(quantity))
        d_step = Decimal(str(step))
        rounded = float(d_qty.quantize(d_step, rounding=ROUND_DOWN))
        return max(rounded, min_qty) if rounded >= min_qty else 0.0

    async def _ensure_lot_size(self, symbol: str) -> dict:
        if symbol in self._lot_cache:
            return self._lot_cache[symbol]
        default = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}
        if self._feed is None:
            return default
        try:
            info = await asyncio.to_thread(self._feed.client.futures_exchange_info)
            for sym_info in info.get('symbols', []):
                sym = sym_info['symbol']
                entry: dict = {}
                for f in sym_info.get('filters', []):
                    ft = f.get('filterType')
                    if ft == 'LOT_SIZE':
                        entry['step_size'] = float(f['stepSize'])
                        entry['min_qty'] = float(f['minQty'])
                    elif ft == 'MIN_NOTIONAL':
                        entry['min_notional'] = float(f.get('notional') or f.get('minNotional') or 0)
                self._lot_cache[sym] = {
                    'step_size': entry.get('step_size', 0.001),
                    'min_qty': entry.get('min_qty', 0.001),
                    'min_notional': entry.get('min_notional', 0.0),
                }
        except Exception as exc:
            logger.warning(f"Failed to fetch exchange info: {exc}")
        return self._lot_cache.get(symbol, default)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_placing_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._placing_locks:
            self._placing_locks[symbol] = asyncio.Lock()
        return self._placing_locks[symbol]

    @staticmethod
    def _calc_pnl(order: OpenOrder, close_price: float) -> float:
        if order.side == 'BUY':
            return (close_price - order.entry_price) * order.quantity
        return (order.entry_price - close_price) * order.quantity

    def _record_failure(self, symbol: str) -> bool:
        """Increment failure counter. Returns True if consecutive threshold is reached."""
        self._failure_counts[symbol] = self._failure_counts.get(symbol, 0) + 1
        reached = self._failure_counts[symbol] >= self._consecutive_failure_threshold
        if reached:
            self._notifier.notify(
                "emergency",
                f"Order placement threshold reached: {symbol}",
                f"{self._failure_counts[symbol]} consecutive failures",
                "order_executor",
            )
        return reached

    def _record_success(self, symbol: str) -> None:
        self._failure_counts[symbol] = 0
