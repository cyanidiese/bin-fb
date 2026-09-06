from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from config.settings import Settings
from config.risk_config import load_risk_config
from bot.rate_limit_guard import guard as rl_guard
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


class MarketConditionError(Exception):
    """Wraps transient exchange rejections due to market conditions (e.g. PERCENT_PRICE filter).
    Does not count as a failure — signal will be retried next candle."""


class BotHaltError(BaseException):
    """Raised when all symbols are disabled and the bot cannot continue."""


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
    sl_order_id: str | None = None
    open_time: str | None = None
    balance_at_open: float = 0.0
    signal_level: int = 0
    precision_score: float = 0.0
    scenario: str = ''
    # Average price the entry leg actually filled at, read back from the exchange
    # after placement. entry_price above is the *intended* (signal) price and can
    # differ materially: on 2026-08-18 INJUSDT was signalled at 4.052 and filled at
    # 4.0670, so PnL computed off entry_price overstated the result by 5.57 USDT.
    # 0.0 means "not reconciled" — use _effective_entry(), never this field raw.
    fill_entry_price: float = 0.0
    # USDT wallet balance read immediately before this order was submitted. This is
    # the "Before" figure in the trade-close notification; balance_at_open above is
    # the allocated per-symbol trade cap, which is a different (much smaller) number.
    wallet_at_open: float = 0.0


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
        project_root: 'Path | None' = None,
    ) -> None:
        self._mode = mode
        self._settings = settings
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._feed = data_feed
        self._symbol_registry = symbol_registry
        self._project_root = project_root
        self._last_opened_preset: dict[str, str] = {}

        self._states: dict[str, OrderState] = {}
        self._open_orders: dict[str, OpenOrder] = {}
        self._fake_orders: dict[str, FakeOrder] = {}  # software TP/SL/trailing tracking
        # Exits we decided on but could not execute (e.g. the endpoint banned us).
        # symbol -> (result, software_close_price). Retried every candle until the
        # exchange confirms. Booking these as closed would write a trade that never
        # happened into the preset record and free the symbol while the position is
        # still live — see _finalize_close.
        self._pending_close: dict[str, tuple[str, float]] = {}
        self._pending_close_logged: dict[str, str] = {}
        self._placing_locks: dict[str, asyncio.Lock] = {}
        self._failure_counts: dict[str, int] = {}
        self._lot_cache: dict[str, dict] = {}  # {symbol: {step_size, min_qty, min_notional}}
        self._bracket_max: dict[str, int] = {}  # symbol → max leverage from first bracket
        self._candle_index: int = 0  # used by check_all_orders (legacy, single-symbol tests)
        self._symbol_candle_index: dict[str, int] = {}  # per-symbol candle counter for check_symbol_candle
        self._closing: set[str] = set()

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
        max_losing_pct: float = 0.0,
        max_losing_amount_usdt: float = 0.0,
        max_losing_candles: int = 0,
        trail_activation_pct: float = 0.0,
        trail_min_distance_pct: float = 0.0,
        level: Optional[int] = None,
        signal_type: str = '',
        balance_at_open: float = 0.0,
        signal_level: int = 0,
        precision_score: float = 0.0,
        scenario: str = '',
        wallet_at_open: float = 0.0,
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

                # C2: verify notional after rounding; bump one step if needed.
                if entry > 0:
                    lot_c2 = await self._ensure_lot_size(symbol)
                    min_notional_c2 = lot_c2.get('min_notional', 0.0)
                    if min_notional_c2 > 0 and rounded_qty * entry < min_notional_c2:
                        step_c2 = Decimal(str(lot_c2['step_size']))
                        bumped = float(Decimal(str(rounded_qty)) + step_c2)
                        if bumped * entry >= min_notional_c2:
                            logger.info(f"[{symbol}] Bumped qty {rounded_qty} → {bumped} to meet min_notional {min_notional_c2}")
                            rounded_qty = bumped
                        else:
                            raise FundsError(f"notional {rounded_qty * entry:.4f} < min_notional {min_notional_c2:.4f} even after bump")
                    # Cap notional before creating OpenOrder so the stored quantity matches
                    # what the exchange actually fills (prevents phantom PnL from uncapped qty).
                    _rl_notional = load_risk_config()
                    _max_notional = _rl_notional.get("max_order_notional_usdt", 0.0)
                    if _max_notional > 0 and rounded_qty * entry > _max_notional:
                        _capped_qty = float(
                            Decimal(str(_max_notional / entry)).quantize(
                                Decimal(str(lot_c2['step_size'])), rounding=ROUND_DOWN
                            )
                        )
                        logger.warning(
                            f"[{symbol}] Notional cap: qty {rounded_qty:.4f} → {_capped_qty:.4f} "
                            f"(notional {rounded_qty * entry:.2f} > cap {_max_notional:.2f} USDT)"
                        )
                        rounded_qty = _capped_qty

                order_id = await asyncio.wait_for(
                    self._submit_to_exchange(symbol, side, rounded_qty, leverage),
                    timeout=self.PLACING_TIMEOUT,
                )
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name=preset_name, side=side,
                    entry_price=entry, tp_price=tp, sl_price=sl,
                    quantity=rounded_qty, leverage=leverage,
                    partial_take_pct=partial_take_pct,
                    trailing_stop_pct=trailing_stop_pct,
                    exchange_order_id=order_id,
                    balance_at_open=balance_at_open,
                    signal_level=signal_level,
                    precision_score=precision_score,
                    scenario=scenario,
                    wallet_at_open=wallet_at_open,
                )
                # Create software FakeOrder for trailing stop / TP / SL monitoring
                _early_loss_sl = 0.0
                if max_losing_amount_usdt > 0 and rounded_qty > 0:
                    if side == 'BUY':
                        _early_loss_sl = entry - max_losing_amount_usdt / rounded_qty
                    else:
                        _early_loss_sl = entry + max_losing_amount_usdt / rounded_qty
                # Apply global max-loss cap from risk config (tighter of preset and global wins)
                _rl = load_risk_config()
                _base_cap = _rl.get("max_loss_usdt", 0.0)
                _sym_cap = _rl.get("max_loss_usdt_per_symbol", {}).get(symbol, _base_cap)
                _tp_ratio = _rl.get("max_loss_tp_ratio", 0.0)
                _effective_cap = _sym_cap
                if _tp_ratio > 0 and _effective_cap > 0 and rounded_qty > 0:
                    _tp_usdt = abs(tp - entry) * rounded_qty
                    if _tp_usdt > 0:
                        _effective_cap = min(_effective_cap, _tp_ratio * _tp_usdt)
                if _effective_cap > 0 and rounded_qty > 0:
                    if side == 'BUY':
                        _g_sl = entry - _effective_cap / rounded_qty
                        _early_loss_sl = max(_early_loss_sl, _g_sl) if _early_loss_sl > 0 else _g_sl
                    else:
                        _g_sl = entry + _effective_cap / rounded_qty
                        _early_loss_sl = min(_early_loss_sl, _g_sl) if _early_loss_sl > 0 else _g_sl
                self._fake_orders[symbol] = FakeOrder(
                    side=side,
                    entry_price=entry,
                    tp=tp,
                    sl=sl if sl else entry * (0.99 if side == 'BUY' else 1.01),
                    level=level,
                    signal_type=signal_type,
                    candle_index=self._symbol_candle_index.get(symbol, 0),
                    partial_take_pct=partial_take_pct,
                    trailing_stop_pct=trailing_stop_pct,
                    max_losing_pct=max_losing_pct,
                    max_losing_candles=max_losing_candles,
                    early_loss_sl=_early_loss_sl,
                    trail_activation_pct=trail_activation_pct,
                    trail_min_distance_pct=trail_min_distance_pct,
                )
                self._states[symbol] = OrderState.OPEN
                self._open_orders[symbol].open_time = datetime.now(timezone.utc).isoformat()
                self._last_opened_preset[symbol] = preset_name
                # Place SL stop-market order on exchange as crash protection.
                # If the bot dies the position is protected; software FakeOrder still manages TP/trailing.
                if sl > 0:
                    sl_order_id = await self._place_sl_on_exchange(symbol, side, rounded_qty, sl)
                    self._open_orders[symbol].sl_order_id = sl_order_id
                self._record_success(symbol)
                logger.info(
                    f"Order placed: {symbol} {side} qty={rounded_qty} "
                    f"entry={entry} TP={tp} SL={sl} preset={preset_name}"
                )
                # Reconcile the entry against the real fill so every downstream money
                # figure (PnL, fee, notification, efficiency ranking) is computed off the
                # price we actually paid rather than the price we asked for. Runs last:
                # it polls the exchange for up to ~0.5s and must never delay the
                # protective SL above. Deliberately does NOT feed the FakeOrder or the
                # SL/TP geometry — those stay on the signalled entry so trigger levels
                # are unchanged by this fix.
                _fill = await self._reconcile_entry_fill(symbol, order_id)
                if _fill > 0:
                    self._open_orders[symbol].fill_entry_price = _fill
                    # DIRECTIONAL slippage: only an adverse fill matters. A BUY filled
                    # above the signal (or a SELL below it) means price moved away
                    # between signal and fill.
                    _adverse_pct = (
                        (_fill - entry) / entry * 100 if side == 'BUY'
                        else (entry - _fill) / entry * 100
                    ) if entry > 0 else 0.0
                    if abs(_adverse_pct) >= 0.05:
                        logger.warning(
                            f"[{symbol}] Entry slippage: signalled {entry} → filled {_fill:.6f} "
                            f"({_adverse_pct:+.3f}% {'adverse' if _adverse_pct > 0 else 'favourable'}) "
                            f"— PnL computed off the fill"
                        )
                    else:
                        logger.info(f"[{symbol}] Entry fill reconciled: {_fill:.6f}")

                    # Stale-entry abort. A fill materially worse than the signal means
                    # the move we wanted to catch already started without us, so the
                    # entry premise is gone. Measured over Aug 19-30: every trade with
                    # >=0.30% adverse slippage lost (4/4, -142.28 USDT, avg -35.57),
                    # while trades filling within 0.05% netted +139.17. Closing here
                    # costs a round-trip fee (~0.08%) instead of riding it to the stop.
                    # Off by default (0.0); n=4 is thin, so this must be opted into.
                    _max_slip = float(load_risk_config().get("max_entry_slippage_pct", 0.0))
                    if _max_slip > 0 and _adverse_pct > _max_slip:
                        logger.warning(
                            f"[{symbol}] ABORTING — entry slipped {_adverse_pct:.3f}% "
                            f"(limit {_max_slip}%): signalled {entry}, filled {_fill:.6f}. "
                            f"Closing immediately rather than trading a stale entry."
                        )
                        self._notifier.notify(
                            "warning", f"{symbol} entry aborted on slippage",
                            f"filled {_fill:.6f} vs signalled {entry} "
                            f"({_adverse_pct:.3f}% > {_max_slip}%)", "order_executor",
                        )
                        await self.close_order(symbol)
                        return False
                else:
                    logger.info(
                        f"[{symbol}] Entry fill unavailable — PnL falls back to signalled entry {entry}"
                    )
                return True
            except FundsError as exc:
                self._states[symbol] = OrderState.IDLE
                logger.warning(f"[{symbol}] Order skipped — insufficient funds: {exc}")
                return False
            except MarketConditionError as exc:
                self._states[symbol] = OrderState.IDLE
                logger.warning(f"[{symbol}] Order skipped — market condition: {exc}")
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

    async def _finalize_close(
        self, symbol: str, open_order: 'OpenOrder', result: str, software_close_price: float,
    ) -> dict | None:
        """Close on the exchange and book the result.

        Returns None when the exchange close did NOT execute. In that case the position,
        its FakeOrder and its non-IDLE state are all left intact so the exit is retried
        on the next candle.

        Booking a failed close would be actively harmful: the fabricated PnL flows into
        virtual_tracker.record_closed_trade(), and preset ranking is sum(recent_trades),
        so the bot would promote a preset on profit it never earned — and the symbol
        would go IDLE while the position is still open on the exchange, allowing a
        second position on top of the first.
        """
        try:
            actual_close_price = await self._market_close(
                symbol, open_order, fallback=software_close_price)
        except Exception as exc:
            self._pending_close[symbol] = (result, software_close_price)
            # Log/notify once per distinct exit reason, not once per retry.
            if self._pending_close_logged.get(symbol) != result:
                self._pending_close_logged[symbol] = result
                logger.error(
                    f"Market close failed for {symbol} ({result}): {exc} — "
                    f"position kept OPEN and will be retried; exchange SL still stands"
                )
                self._notifier.notify(
                    "warning", f"Could not close {symbol}",
                    f"{exc}\nPosition left OPEN and will be retried. Nothing was recorded.",
                    "order_executor",
                )
            return None

        self._pending_close.pop(symbol, None)
        self._pending_close_logged.pop(symbol, None)

        pnl = self._calc_pnl(open_order, actual_close_price)
        self._record_real_order_close(symbol, open_order, actual_close_price, result, pnl)
        info = {
            "symbol": symbol,
            "preset_name": open_order.preset_name,
            "result": result,
            "pnl_usdt": pnl,
            "side": open_order.side,
            "entry_price": open_order.entry_price,
            "fill_entry_price": self._effective_entry(open_order),
            "close_price": actual_close_price,
            "fee_usdt": self._order_fee(
                open_order.quantity, self._effective_entry(open_order), actual_close_price),
            "wallet_at_open": open_order.wallet_at_open,
            "leverage": open_order.leverage,
        }
        self._open_orders.pop(symbol, None)
        self._fake_orders.pop(symbol, None)
        self._states[symbol] = OrderState.IDLE
        self._record_success(symbol)
        logger.info(
            f"Order closed: {symbol} result={result} "
            f"entry={open_order.entry_price} close={actual_close_price:.4f} pnl={pnl:.2f} USDT"
        )
        return info

    async def retry_pending_closes(self, only_symbol: str | None = None) -> list[dict]:
        """Re-attempt exits whose exchange close previously failed.

        Called at the top of check_symbol_candle (the live per-candle path) so a
        position stranded by a ban is retried on every candle until it clears.
        """
        out: list[dict] = []
        items = list(self._pending_close.items())
        if only_symbol is not None:
            items = [kv for kv in items if kv[0] == only_symbol]
        for symbol, (result, sw_price) in items:
            open_order = self._open_orders.get(symbol)
            if open_order is None:
                # Position vanished (reconciled elsewhere) — drop the intent.
                self._pending_close.pop(symbol, None)
                self._pending_close_logged.pop(symbol, None)
                continue
            if symbol in self._closing:
                continue
            self._closing.add(symbol)
            try:
                info = await self._finalize_close(symbol, open_order, result, sw_price)
            finally:
                self._closing.discard(symbol)
            if info is not None:
                logger.info(f"[{symbol}] Pending close finally executed on retry")
                out.append(info)
        return out

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
        # Retry any exit whose exchange close failed earlier (e.g. during a ban)
        # before evaluating fresh triggers.
        closed = await self.retry_pending_closes()
        for symbol, fake_order in list(self._fake_orders.items()):
            if symbol in self._pending_close:
                continue  # already handled by the retry above
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
            info = await self._finalize_close(symbol, open_order, result, software_close_price)
            if info is not None:
                closed.append(info)
        return closed

    async def check_symbol_price(self, symbol: str, current_price: float) -> list[dict]:
        """Call on every price tick for a specific symbol. Checks that symbol's FakeOrder only."""
        if self._states.get(symbol) == OrderState.PLACING:
            return []
        fake_order = self._fake_orders.get(symbol)
        if fake_order is None:
            return []

        result = fake_order.check_price(current_price)
        if result is None:
            return []

        if symbol in self._closing or symbol in self._pending_close:
            return []
        self._closing.add(symbol)
        try:
            open_order = self._open_orders.get(symbol)
            if open_order is None:
                del self._fake_orders[symbol]
                return []

            software_close_price = fake_order.close_price or open_order.entry_price
            info = await self._finalize_close(symbol, open_order, result, software_close_price)
            return [info] if info is not None else []
        finally:
            self._closing.discard(symbol)

    async def check_symbol_candle(
        self,
        symbol: str,
        high: float,
        low: float,
        candle_open: float,
        candle_close: float,
    ) -> list[dict]:
        """Call once per closed candle for a specific symbol. Handles gap scenarios
        (price jumped through SL/TP at candle open) that per-tick checks miss."""
        if self._states.get(symbol) == OrderState.PLACING:
            return []
        # An exit we decided on earlier but could not execute (a ban, a transient
        # rejection) is retried here first — this is the live per-candle entry point.
        retried = await self.retry_pending_closes(only_symbol=symbol)
        if retried:
            return retried
        if symbol in self._pending_close:
            return []  # still stranded; do not re-evaluate or double-handle
        fake_order = self._fake_orders.get(symbol)
        if fake_order is None:
            return []

        self._symbol_candle_index[symbol] = self._symbol_candle_index.get(symbol, 0) + 1
        candle_idx = self._symbol_candle_index[symbol]

        result = fake_order.check(
            high, low, candle_idx,
            candle_open=candle_open,
            candle_close=candle_close,
        )
        if result is None:
            return []

        if symbol in self._closing or symbol in self._pending_close:
            return []
        self._closing.add(symbol)
        try:
            open_order = self._open_orders.get(symbol)
            if open_order is None:
                del self._fake_orders[symbol]
                return []

            software_close_price = fake_order.close_price or open_order.entry_price
            info = await self._finalize_close(symbol, open_order, result, software_close_price)
            return [info] if info is not None else []
        finally:
            self._closing.discard(symbol)

    # ------------------------------------------------------------------ #
    # Bulk close                                                           #
    # ------------------------------------------------------------------ #

    async def close_all_orders_at_market(self) -> list[dict]:
        results = []
        for symbol, order in list(self._open_orders.items()):
            try:
                close_price = await self._market_close(symbol, order)
                pnl = self._calc_pnl(order, close_price)
                self._record_real_order_close(symbol, order, close_price, 'market_close', pnl)
                self._notifier.notify_trade_close(
                    symbol=symbol,
                    side=order.side,
                    pnl_usdt=pnl,
                    entry_price=self._effective_entry(order),
                    close_price=close_price,
                    preset_name=order.preset_name,
                    fee_usdt=self._order_fee(
                        order.quantity, self._effective_entry(order), close_price),
                    balance_before=order.wallet_at_open,
                    balance_after=await self.fetch_account_balance(),
                )
                results.append({
                    "symbol": symbol,
                    "side": order.side,
                    "entry_price": order.entry_price,
                    "close_price": close_price,
                    "pnl_usdt": pnl,
                    "leverage": order.leverage,
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

    def save_open_positions(self, path: Path) -> int:
        """Persist open positions to disk for recovery on next restart."""
        if not self._open_orders:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return 0
        import dataclasses as _dc
        state = {}
        for symbol, order in self._open_orders.items():
            fake = self._fake_orders.get(symbol)
            state[symbol] = {
                'open_order': _dc.asdict(order),
                'fake_order': fake.get_state() if fake else None,
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(state))
        tmp.replace(path)
        logger.info(f"Saved {len(state)} open position(s) for restart recovery")
        return len(state)

    def restore_open_positions(self, path: Path) -> int:
        """Re-register positions saved before a restart. Deletes path after restore."""
        if not path.exists():
            return 0
        try:
            state = json.loads(path.read_text())
        except Exception as exc:
            logger.warning(f"Failed to load restart positions from {path}: {exc}")
            return 0
        restored = 0
        for symbol, data in state.items():
            try:
                order = OpenOrder(**data['open_order'])
                self._open_orders[symbol] = order
                self._states[symbol] = OrderState.OPEN
                fake_state = data.get('fake_order')
                if fake_state:
                    self._fake_orders[symbol] = FakeOrder.from_state(fake_state)
                logger.info(
                    f"[{symbol}] Restored open position: {order.side} entry={order.entry_price} "
                    f"SL={order.sl_price} TP={order.tp_price} preset={order.preset_name}"
                )
                restored += 1
            except Exception as exc:
                logger.warning(f"[{symbol}] Failed to restore position: {exc}")
        try:
            path.unlink()
        except Exception:
            pass
        if restored:
            logger.info(f"Restored {restored} position(s) — resuming monitoring without forced close")
        return restored

    async def close_order(self, symbol: str) -> dict | None:
        """Close a single open order at market. Returns result dict or None if not open."""
        order = self._open_orders.get(symbol)
        if order is None:
            return None
        try:
            close_price = await self._market_close(symbol, order)
            pnl = self._calc_pnl(order, close_price)
            self._record_real_order_close(symbol, order, close_price, 'market_close', pnl)
            self._notifier.notify_trade_close(
                symbol=symbol,
                side=order.side,
                pnl_usdt=pnl,
                entry_price=self._effective_entry(order),
                close_price=close_price,
                preset_name=order.preset_name,
                fee_usdt=self._order_fee(
                    order.quantity, self._effective_entry(order), close_price),
                balance_before=order.wallet_at_open,
                balance_after=await self.fetch_account_balance(),
            )
            result = {
                "symbol": symbol,
                "side": order.side,
                "entry_price": order.entry_price,
                "close_price": close_price,
                "pnl_usdt": pnl,
                "leverage": order.leverage,
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
    # Real order recording                                                 #
    # ------------------------------------------------------------------ #

    def _record_real_order_close(
        self,
        symbol: str,
        order: OpenOrder,
        close_price: float,
        result: str,
        pnl_usdt: float,
    ) -> None:
        if self._project_root is None:
            return
        path: Path = self._project_root / 'data' / f'real_orders_{symbol}_{self._mode}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        records: list = []
        if path.exists():
            try:
                records = json.loads(path.read_text())
            except Exception:
                records = []
        records.append({
            'preset_name': order.preset_name,
            'side': order.side,
            'entry_price': order.entry_price,
            'fill_entry_price': self._effective_entry(order),
            'close_price': close_price,
            'tp': order.tp_price,
            'sl': order.sl_price,
            'quantity': order.quantity,
            'leverage': order.leverage,
            'open_time': order.open_time,
            'close_time': datetime.now(timezone.utc).isoformat(),
            'pnl_usdt': pnl_usdt,
            'fee_usdt': self._order_fee(
                order.quantity, self._effective_entry(order), close_price),
            'result': result,
            'balance_at_open': order.balance_at_open,
            'wallet_at_open': order.wallet_at_open,
            'signal_level': order.signal_level,
            'precision_score': order.precision_score,
            'scenario': order.scenario,
        })
        if len(records) > 1000:
            records = records[-1000:]
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(records))
        tmp.replace(path)

    # ------------------------------------------------------------------ #
    # Exchange integration                                                 #
    # ------------------------------------------------------------------ #

    async def _place_sl_on_exchange(
        self, symbol: str, side: str, quantity: float, sl_price: float
    ) -> str | None:
        """Place a STOP_MARKET algo order as crash-safe SL protection.
        Binance migrated all conditional order types to /fapi/v1/algoOrder on 2025-12-09.
        Uses MARK_PRICE to avoid wick-triggered false SLs."""
        if self._feed is None or sl_price <= 0:
            return None
        sl_price = await self.round_price(symbol, sl_price)
        client = self._feed.client
        close_side = 'SELL' if side == 'BUY' else 'BUY'
        if not hasattr(client, 'futures_create_algo_order'):
            logger.info(f"[{symbol}] Exchange SL skipped: futures_create_algo_order not available in installed python-binance — software SL active")
            return None
        lot = await self._ensure_lot_size(symbol)
        qty_str = self._qty_str(quantity, lot['step_size'])
        try:
            result = await asyncio.to_thread(
                client.futures_create_algo_order,
                symbol=symbol,
                side=close_side,
                type='STOP_MARKET',
                triggerPrice=self._price_str(sl_price, lot['tick_size']),
                quantity=qty_str,
                reduceOnly='true',
                workingType='MARK_PRICE',
            )
            sl_id = str(result.get('algoId'))
            logger.info(f"[{symbol}] SL algo order placed: algoId={sl_id} triggerPrice={sl_price}")
            return sl_id
        except Exception as exc:
            logger.warning(f"[{symbol}] Failed to place SL algo order (no crash protection): {exc}")
            return None

    async def _cancel_exchange_order(self, symbol: str, order_id: str | None) -> None:
        """Cancel a SL algo order by algoId. No-op if order_id is None or not found."""
        if self._feed is None or not order_id:
            return
        client = self._feed.client
        if not hasattr(client, 'futures_cancel_algo_order'):
            logger.warning(f"[{symbol}] python-binance too old — futures_cancel_algo_order missing")
            return
        try:
            await asyncio.to_thread(
                client.futures_cancel_algo_order,
                symbol=symbol,
                algoId=int(order_id),
            )
            logger.info(f"[{symbol}] SL algo order cancelled: algoId={order_id}")
        except Exception as exc:
            logger.warning(f"[{symbol}] Failed to cancel algo order {order_id}: {exc}")

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
            # Wrong leverage = wrong margin calc. Abort placement rather than trade at unexpected risk.
            logger.error(f"[{symbol}] Leverage change to {leverage}x failed — aborting order: {exc}")
            raise

        lot = await self._ensure_lot_size(symbol)
        qty_str = self._qty_str(quantity, lot['step_size'])
        try:
            result = await asyncio.to_thread(
                client.futures_create_order,
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=qty_str,
            )
            return str(result.get('orderId'))
        except Exception as exc:
            exc_str = str(exc).lower()
            exc_code = getattr(exc, 'code', None)

            if exc_code in (-2019, -1013):
                raise FundsError(str(exc)) from exc

            # -4131: PERCENT_PRICE filter — best market price too far from mark price (liquidity issue)
            if exc_code == -4131:
                raise MarketConditionError(str(exc)) from exc

            # -4005: MARKET_LOT_SIZE.maxQty exceeded — update cache and retry once with capped qty
            if exc_code == -4005:
                try:
                    info = await asyncio.to_thread(self._feed.client.futures_exchange_info)
                    for sym_info in info.get('symbols', []):
                        if sym_info['symbol'] != symbol:
                            continue
                        for f in sym_info.get('filters', []):
                            if f.get('filterType') == 'MARKET_LOT_SIZE':
                                mkt_max = float(f.get('maxQty', 0) or 0)
                                if mkt_max > 0:
                                    self._lot_cache[symbol]['max_qty'] = mkt_max
                                    capped = self._qty_str(
                                        float(Decimal(str(mkt_max)).quantize(
                                            Decimal(str(lot['step_size'])), rounding=ROUND_DOWN)),
                                        lot['step_size'])
                                    logger.warning(
                                        f"[{symbol}] -4005 retry with MARKET_LOT_SIZE cap={mkt_max} qty={capped}")
                                    result2 = await asyncio.to_thread(
                                        client.futures_create_order,
                                        symbol=symbol, side=side, type='MARKET', quantity=capped)
                                    return str(result2.get('orderId'))
                except Exception as retry_exc:
                    logger.error(f"[{symbol}] -4005 retry failed: {retry_exc}")
                raise  # still fails → caller increments failure count

            is_symbol_error = (
                exc_code == -1121
                or (exc_code == -2010 and "perpetual" in exc_str)
                or "is not available" in exc_str
            )
            if is_symbol_error:
                raise SymbolError(symbol, str(exc)) from exc

            raise  # other errors → caller records failure

    async def _market_close(self, symbol: str, order: OpenOrder, fallback: float | None = None) -> float:
        # The exchange SL is cancelled AFTER a confirmed close, not before.
        #
        # Cancelling first left a window where the cancel succeeded but the close then
        # failed — an open position with no stop. That window is reachable whenever the
        # endpoint rejects us mid-sequence (a rate-limit ban does exactly this), and it
        # matters more now that a failed close keeps the position open for retry.
        #
        # Leaving the SL live during the close is safe: both legs are reduceOnly and
        # point the same way. If the SL fills first, our market order returns -2022,
        # which is handled below by recovering the actual SL fill price.
        if self._feed is None:
            return order.entry_price
        client = self._feed.client
        close_side = 'SELL' if order.side == 'BUY' else 'BUY'
        lot = await self._ensure_lot_size(symbol)
        qty_str = self._qty_str(order.quantity, lot['step_size'])
        try:
            result = await asyncio.to_thread(
                client.futures_create_order,
                symbol=symbol,
                side=close_side,
                type='MARKET',
                quantity=qty_str,
                reduceOnly=True,
            )
        except Exception as exc:
            # -2022: position already closed by the exchange SL order — not an error.
            # Try to recover the actual SL fill price from trade records before falling back.
            if getattr(exc, 'code', None) == -2022:
                sl_fill_price = 0.0
                try:
                    start_ms: int | None = None
                    if order.open_time:
                        start_ms = int(datetime.fromisoformat(order.open_time).timestamp() * 1000)
                    kw: dict = dict(symbol=symbol, limit=10)
                    if start_ms:
                        kw['startTime'] = start_ms
                    sl_trades = await asyncio.to_thread(client.futures_account_trades, **kw)
                    close_side = 'SELL' if order.side == 'BUY' else 'BUY'
                    matching = [t for t in sl_trades if t.get('side') == close_side]
                    if matching:
                        sl_fill_price = float(matching[-1]['price'])
                except Exception as te:
                    logger.debug(f"[{symbol}] SL trade record lookup failed: {te}")
                if sl_fill_price > 0:
                    logger.info(
                        f"[{symbol}] Position closed by exchange SL — "
                        f"recovered actual fill price {sl_fill_price:.6f}"
                    )
                    return sl_fill_price
                fb = fallback if fallback and fallback > 0 else order.entry_price
                logger.info(
                    f"[{symbol}] Position already closed on exchange (SL filled) — "
                    f"using software close price {fb:.6f}"
                )
                return fb
            raise
        # Close confirmed — now retire the protective SL.
        await self._cancel_exchange_order(symbol, order.sl_order_id)
        avg_price = float(result.get('avgPrice', 0) or 0)
        # Testnet often returns avgPrice="0" in the immediate response for market orders.
        # Retry-query the order to get the actual fill price before falling back to entry.
        if avg_price <= 0:
            order_id = result.get('orderId')
            if order_id:
                for _ in range(4):
                    await asyncio.sleep(0.25)
                    try:
                        filled = await asyncio.to_thread(
                            client.futures_get_order,
                            symbol=symbol,
                            orderId=order_id,
                        )
                        avg_price = float(filled.get('avgPrice', 0) or 0)
                        if avg_price > 0:
                            break
                    except Exception:
                        break
        if avg_price <= 0 and order_id:
            # futures_get_order avgPrice can lag the matching engine. The trade records
            # endpoint is populated immediately from the fill — use it as a second fallback.
            try:
                trades = await asyncio.to_thread(
                    client.futures_account_trades,
                    symbol=symbol,
                    orderId=order_id,
                )
                if trades:
                    avg_price = self._fill_price_from_trades(trades)
                    if avg_price > 0:
                        logger.info(
                            f"[{symbol}] avgPrice recovered from trade records: {avg_price:.6f}"
                        )
            except Exception as te:
                logger.debug(f"[{symbol}] Trade records fallback failed: {te}")
        if avg_price <= 0:
            fb = fallback if fallback and fallback > 0 else order.entry_price
            logger.warning(
                f"[{symbol}] avgPrice still 0 after retry — using fallback={fb} as close price"
            )
            self._notifier.notify(
                "warning",
                f"{symbol} close price unavailable",
                f"avgPrice=0 after retry — used software price {fb:.6f}. PnL estimate may be inaccurate.",
                "order_executor",
            )
            return fb
        return avg_price

    # ------------------------------------------------------------------ #
    # Account / position management                                        #
    # ------------------------------------------------------------------ #

    async def reconcile_with_exchange(self) -> None:
        """
        On startup: close any open exchange positions the bot has no record of.
        Parking them as unmanaged orders (sl=0, no FakeOrder) creates zombies that
        block future signals with no exit path. Closing and re-entering is safer.
        """
        if self._feed is None:
            return
        try:
            positions = await asyncio.to_thread(self._feed.client.futures_position_information)
            closed_count = 0
            for pos in positions:
                symbol = pos['symbol']
                amt = float(pos.get('positionAmt', 0))
                if amt == 0:
                    continue
                if symbol in self._open_orders:
                    continue  # bot already tracking this one
                entry_price = float(pos.get('entryPrice', 0))
                side = 'BUY' if amt > 0 else 'SELL'
                quantity = abs(amt)
                leverage = int(float(pos.get('leverage', 1)))
                # Register temporarily so _market_close can send the reduce-only order
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name='reconciled', side=side,
                    entry_price=entry_price, tp_price=0.0, sl_price=0.0,
                    quantity=quantity, leverage=leverage,
                )
                self._states[symbol] = OrderState.OPEN
                try:
                    close_price = await self._market_close(symbol, self._open_orders[symbol])
                    pnl = self._calc_pnl(self._open_orders[symbol], close_price)
                    logger.warning(
                        f"[{symbol}] Orphan position closed on startup: {side} "
                        f"qty={quantity} entry={entry_price:.6f} close={close_price:.6f} pnl={pnl:+.4f}"
                    )
                except Exception as exc:
                    logger.error(f"[{symbol}] Failed to close orphan position: {exc}")
                finally:
                    self._open_orders.pop(symbol, None)
                    self._fake_orders.pop(symbol, None)
                    self._states.pop(symbol, None)
                closed_count += 1
            if closed_count == 0:
                logger.info("Reconciliation complete: no orphan positions found")
            else:
                self._notifier.notify(
                    "warning",
                    f"Closed {closed_count} orphan position(s) on startup",
                    "Bot restarted with open positions — closed immediately to prevent unmanaged exposure",
                    "order_executor",
                )
        except Exception as exc:
            logger.warning(f"Reconciliation failed: {exc}")

    async def sync_positions_with_exchange(self) -> None:
        """Detect positions that were closed externally (e.g. exchange SL fired while bot ran).
        Clears stale _open_orders entries so the symbol becomes IDLE again."""
        if self._feed is None or not self._open_orders:
            return
        try:
            positions = await asyncio.to_thread(self._feed.client.futures_position_information)
            live = {
                pos['symbol']
                for pos in positions
                if float(pos.get('positionAmt', 0)) != 0
            }
            for symbol in list(self._open_orders.keys()):
                if symbol not in live:
                    logger.warning(
                        f"[{symbol}] Position closed externally — clearing bot state"
                    )
                    self._notifier.notify(
                        "warning",
                        f"{symbol} position closed externally",
                        "Exchange shows no position but bot had one open — state cleared",
                        "order_executor",
                    )
                    self._open_orders.pop(symbol, None)
                    self._fake_orders.pop(symbol, None)
                    self._states.pop(symbol, None)
        except Exception as exc:
            logger.warning(f"sync_positions_with_exchange failed: {exc}")

    # Quote asset every symbol we trade is margined in. The USDT wallet entry is
    # the only figure that reliably reflects our tradable capital.
    _QUOTE_ASSET = 'USDT'

    async def fetch_account_balance(self) -> float:
        """Returns the USDT wallet balance from the futures account, or 0.0 on error.

        Reads assets[USDT].walletBalance rather than totalWalletBalance.
        totalWalletBalance is not reliably USDT-only — a non-USDT holding can
        surface there (this account also carries USDC and BTC), and on
        2026-08-18 it returned exactly 5000.0 (the USDC balance) for ~35
        minutes while real USDT was 3043.94. That poisoned the risk manager's
        peak balance, latched the drawdown hard stop on a phantom 39% loss, and
        oversized two live orders by ~36%. Falls back to totalWalletBalance only
        if no USDT asset entry is present.
        """
        if self._feed is None:
            return 0.0
        # futures_account is our most expensive call (weight 5). When the trading
        # endpoint has banned us, skip the round-trip: it would fail anyway and each
        # attempt while banned extends the ban.
        _key = 'testnet' if getattr(self._feed, '_is_testnet', False) else 'production'
        _wait = rl_guard.blocked_for(_key)
        if _wait > 0:
            logger.warning(
                f"Skipping balance fetch: '{_key}' rate-limit banned for another "
                f"{_wait:.0f}s"
            )
            return 0.0
        try:
            account = await asyncio.to_thread(self._feed.client.futures_account)
            for asset in account.get('assets', []) or []:
                if asset.get('asset') == self._QUOTE_ASSET:
                    return float(asset.get('walletBalance', 0) or 0)
            total = float(account.get('totalWalletBalance', 0) or 0)
            logger.warning(
                f"No {self._QUOTE_ASSET} asset entry in futures_account — "
                f"falling back to totalWalletBalance={total:.2f}"
            )
            return total
        except Exception as exc:
            rl_guard.note_exception(_key, exc)
            logger.warning(f"Failed to fetch account balance: {exc}")
            return 0.0

    def reset_for_mode_switch(self, new_mode: str) -> None:
        """Call after close_all_orders_at_market() when switching modes."""
        self._mode = new_mode
        self._lot_cache.clear()  # re-fetch lot sizes for the new endpoint
        logger.info(f"OrderExecutor mode reset to {new_mode}")

    # TRADIFI_PERPETUAL = gold/silver (XAUUSDT, XAGUSD) — behave like regular perps
    _VALID_CONTRACT_TYPES = {'PERPETUAL', 'TRADIFI_PERPETUAL'}

    async def check_symbols_on_exchange(self, symbols: list[str]) -> None:
        """Startup check: disable any symbol that is not TRADING or is not a supported perpetual type."""
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
                if status != 'TRADING' or contract_type not in self._VALID_CONTRACT_TYPES:
                    reason = f"status={status} contractType={contract_type}"
                    await self._auto_disable(symbol, reason)
        except Exception as exc:
            logger.warning(f"check_symbols_on_exchange failed: {exc}")

    async def _auto_disable(self, symbol: str, reason: str) -> None:
        """Disable symbol, close any open order, notify. Exit if all symbols are now disabled."""
        logger.error(f"Auto-disabling {symbol}: {reason}")
        await self.close_order(symbol)
        self._notifier.notify("emergency", f"Symbol {symbol} disabled", reason, "order_executor")
        if self._symbol_registry is not None:
            self._symbol_registry.disable(symbol, reason)
            if self._symbol_registry.all_disabled():
                self._notifier.notify(
                    "emergency",
                    "All symbols disabled — bot cannot continue",
                    reason,
                    "order_executor",
                )
                raise BotHaltError("All symbols disabled — bot cannot continue")

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

    async def prefetch_lot_sizes(self, symbol: str = 'BTCUSDT') -> None:
        """Pre-warm the lot cache for all symbols via a single exchange-info call."""
        try:
            await self._ensure_lot_size(symbol)
            logger.info(f"Lot cache pre-warmed: {len(self._lot_cache)} symbols")
        except Exception as e:
            logger.warning(f"Lot cache prefetch failed: {e}")

    async def round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity DOWN to the symbol's LOT_SIZE step, capped by maxQty."""
        lot = await self._ensure_lot_size(symbol)
        step = lot['step_size']
        min_qty = lot['min_qty']
        max_qty = lot.get('max_qty', 0.0)
        d_qty = Decimal(str(quantity))
        d_step = Decimal(str(step))
        rounded = float(d_qty.quantize(d_step, rounding=ROUND_DOWN))
        if max_qty > 0 and rounded > max_qty:
            logger.warning(f"[{symbol}] quantity {rounded} exceeds maxQty {max_qty} — capping")
            rounded = float(Decimal(str(max_qty)).quantize(d_step, rounding=ROUND_DOWN))
        return max(rounded, min_qty) if rounded >= min_qty else 0.0

    @staticmethod
    def _qty_str(quantity: float, step_size: str) -> str:
        """Format quantity as an exchange-safe string without scientific notation.
        f"{qty:g}" switches to scientific for values >= 1e6, which Binance rejects."""
        if '.' in step_size:
            decimals = len(step_size.rstrip('0').split('.')[1])
        else:
            decimals = 0
        return f"{quantity:.{decimals}f}"

    @staticmethod
    def _price_str(price: float, tick_size: str) -> str:
        """Format price as an exchange-safe string with exactly the precision Binance requires.
        tick_size is the PRICE_FILTER tickSize string (e.g. '0.001', '0.00001').
        Using str(float) risks wrong decimal count when the lot cache fell back to defaults."""
        if '.' in tick_size:
            decimals = len(tick_size.rstrip('0').split('.')[1])
        else:
            decimals = 0
        return f"{price:.{decimals}f}"

    async def round_price(self, symbol: str, price: float) -> float:
        """Round price to the symbol's PRICE_FILTER tick size."""
        lot = await self._ensure_lot_size(symbol)
        tick = lot['tick_size']  # always a string now
        d_price = Decimal(str(price))
        d_tick = Decimal(tick)
        return float(d_price.quantize(d_tick, rounding=ROUND_DOWN))

    async def _ensure_lot_size(self, symbol: str) -> dict:
        if symbol in self._lot_cache:
            return self._lot_cache[symbol]
        default = {'step_size': '0.001', 'min_qty': 0.001, 'max_qty': 0.0, 'min_notional': 0.0, 'tick_size': '0.00001'}
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
                        entry['step_size'] = f['stepSize']
                        entry['min_qty'] = float(f['minQty'])
                        lot_max = float(f.get('maxQty', 0) or 0)
                        if lot_max > 0:
                            entry['max_qty'] = lot_max
                    elif ft == 'MARKET_LOT_SIZE':
                        # Market orders use MARKET_LOT_SIZE limits, not LOT_SIZE.
                        # Take the stricter (smaller) maxQty of both filters.
                        mkt_max = float(f.get('maxQty', 0) or 0)
                        if mkt_max > 0:
                            existing = entry.get('max_qty', 0.0)
                            entry['max_qty'] = min(existing, mkt_max) if existing > 0 else mkt_max
                    elif ft == 'PRICE_FILTER':
                        ts = f.get('tickSize', '') or ''
                        if ts and float(ts) > 0:
                            entry['tick_size'] = ts  # keep as original string for exact decimal formatting
                    elif ft == 'MIN_NOTIONAL':
                        entry['min_notional'] = float(f.get('notional') or f.get('minNotional') or 0)
                self._lot_cache[sym] = {
                    'step_size':    entry.get('step_size', '0.001'),
                    'min_qty':      entry.get('min_qty', 0.001),
                    'max_qty':      entry.get('max_qty', 0.0),
                    'min_notional': entry.get('min_notional', 0.0),
                    'tick_size':    entry.get('tick_size', '0.00001'),  # string for exact formatting
                }
        except Exception as exc:
            logger.warning(f"Failed to fetch exchange info: {exc}")
        cached = self._lot_cache.get(symbol, default)
        logger.info(
            f"[{symbol}] lot filters: step={cached['step_size']} "
            f"minQty={cached['min_qty']} maxQty={cached.get('max_qty', 0)} tick={cached['tick_size']}"
        )
        return cached

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_placing_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._placing_locks:
            self._placing_locks[symbol] = asyncio.Lock()
        return self._placing_locks[symbol]

    # Binance Futures taker fee rate (both sides). 0.04% = 0.0004.
    # Applied to entry notional (open) and close notional (close).
    _TAKER_FEE_RATE: float = 0.0004

    @classmethod
    def _order_fee(cls, quantity: float, entry_price: float, close_price: float) -> float:
        """Total Binance taker fee paid for this order (entry + exit legs).
        Both legs are MARKET orders (taker), so this equals the actual commission
        and matches the fee already deducted inside _calc_pnl."""
        return (entry_price + close_price) * quantity * cls._TAKER_FEE_RATE

    async def _reconcile_entry_fill(self, symbol: str, order_id: str | None) -> float:
        """Read the entry leg's actual average fill price back from the exchange.

        The MARKET order response carries avgPrice="0" until the matching engine
        settles, so the trade-records endpoint (populated immediately from the fill)
        is the reliable source — the same approach _market_close already uses for the
        exit leg. Returns 0.0 if unavailable; callers then keep the intended price.
        """
        if self._feed is None or not order_id:
            return 0.0
        client = self._feed.client
        for attempt in range(3):
            try:
                trades = await asyncio.to_thread(
                    client.futures_account_trades, symbol=symbol, orderId=order_id,
                )
            except Exception as exc:
                logger.debug(f"[{symbol}] Entry fill lookup failed: {exc}")
                return 0.0
            if trades:
                avg = self._fill_price_from_trades(trades)
                if avg > 0:
                    return avg
            if attempt < 2:
                await asyncio.sleep(0.25)
        return 0.0

    @staticmethod
    def _fill_price_from_trades(trades: list) -> float:
        """Weighted average fill price from a list of Binance trade records."""
        total_qty = sum(float(t.get('qty', 0)) for t in trades)
        if total_qty <= 0:
            return 0.0
        return sum(float(t.get('price', 0)) * float(t.get('qty', 0)) for t in trades) / total_qty

    @staticmethod
    def _effective_entry(order: OpenOrder) -> float:
        """The entry price to compute money against: the real fill when we have it.

        Falls back to the intended/signal price when reconciliation did not run or
        failed, which keeps behaviour identical to before for those cases.
        """
        return order.fill_entry_price if order.fill_entry_price > 0 else order.entry_price

    @staticmethod
    def _calc_pnl(order: OpenOrder, close_price: float) -> float:
        entry = OrderExecutor._effective_entry(order)
        if order.side == 'BUY':
            raw = (close_price - entry) * order.quantity
        else:
            raw = (entry - close_price) * order.quantity
        fees = (entry + close_price) * order.quantity * OrderExecutor._TAKER_FEE_RATE
        return raw - fees

    def _record_failure(self, symbol: str) -> bool:
        """Increment failure counter. Returns True when consecutive threshold is reached or exceeded."""
        self._failure_counts[symbol] = self._failure_counts.get(symbol, 0) + 1
        count = self._failure_counts[symbol]
        if count == self._consecutive_failure_threshold:
            self._notifier.notify(
                "emergency",
                f"Order placement threshold reached: {symbol}",
                f"{count} consecutive failures",
                "order_executor",
            )
        return count >= self._consecutive_failure_threshold

    def _record_success(self, symbol: str) -> None:
        self._failure_counts[symbol] = 0
