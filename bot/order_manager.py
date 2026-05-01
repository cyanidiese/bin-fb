import logging
import time
from typing import Optional

from binance.client import Client

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages a single open futures position on Binance.

    Three-order structure (entry → TP + SL placed simultaneously):
      - Main:   MARKET entry
      - TP:     TAKE_PROFIT_MARKET with closePosition=True
      - SL:     STOP_MARKET with closePosition=True

    When either TP or SL fills → cancel all remaining orders, verify position closed.

    In testnet mode all live API calls are skipped; the manager tracks state in-memory
    only so the rest of the bot can call it uniformly in both modes.
    """

    def __init__(self, client: Client, symbol: str, leverage: int, testnet: bool = True):
        self._client = client
        self._symbol = symbol
        self._leverage = leverage
        self._testnet = testnet

        self._processing = False
        self._order_main: Optional[dict] = None
        self._order_tp: Optional[dict] = None
        self._order_sl: Optional[dict] = None

        self._entry_side: Optional[str] = None
        self._entry_price: Optional[float] = None
        self._trade_qty: Optional[float] = None

    # ------------------------------------------------------------------ #
    # State queries                                                        #
    # ------------------------------------------------------------------ #

    def has_position(self) -> bool:
        return self._order_main is not None

    def is_processing(self) -> bool:
        return self._processing

    # ------------------------------------------------------------------ #
    # Startup reconciliation                                               #
    # ------------------------------------------------------------------ #

    def reconcile_on_startup(self) -> bool:
        """
        Check Binance for any open position left from a previous run.
        If found, closes it immediately (safe default — no SL/TP orders exist for it).
        Returns True if a stale position was found and closed.
        """
        if self._testnet:
            return False
        try:
            infos = self._client.futures_position_information(symbol=self._symbol)
            if not infos:
                return False
            info = infos[0]
            amt = float(info['positionAmt'])
            if amt == 0.0:
                return False
            side_str = 'LONG' if amt > 0 else 'SHORT'
            logger.warning(
                f"Stale {side_str} position found on startup "
                f"(amt={amt}, entry={info.get('entryPrice')}) — closing for safety"
            )
            self._close_position_if_open()
            return True
        except Exception as e:
            logger.error(f"Startup reconciliation error: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Open                                                                 #
    # ------------------------------------------------------------------ #

    def open_position(
        self,
        side: str,
        quantity: float,
        tp_price: float,
        sl_price: float,
    ) -> bool:
        """
        Open a leveraged position with simultaneous TP and SL orders.

        side     — 'BUY' or 'SELL'
        quantity — base asset quantity (before leverage multiplier)
        tp_price — take-profit trigger price
        sl_price — stop-loss trigger price

        Returns True on success, False if blocked or API error.
        """
        if self.has_position() or self.is_processing():
            logger.warning("open_position: blocked — position already open or processing")
            return False

        tp_price = round(tp_price, 2)
        sl_price = round(sl_price, 2)
        opposite = 'SELL' if side == 'BUY' else 'BUY'
        trade_qty = round(quantity * self._leverage, 3)
        ts = time.time()

        logger.info(
            f"Opening {side} | qty={trade_qty} (×{self._leverage}) "
            f"| tp={tp_price} | sl={sl_price}"
        )

        if self._testnet:
            # In testnet mode track in-memory only; no real API orders.
            self._order_main = {'orderId': 'testnet_main', 'status': 'FILLED'}
            self._order_tp   = {'orderId': 'testnet_tp',   'status': 'NEW'}
            self._order_sl   = {'orderId': 'testnet_sl',   'status': 'NEW'}
            self._entry_side  = side
            self._entry_price = None  # filled from next price tick in test flow
            self._trade_qty   = trade_qty
            return True

        self._processing = True
        try:
            self._order_main = self._client.futures_create_order(
                symbol=self._symbol,
                side=side,
                type=Client.FUTURE_ORDER_TYPE_MARKET,
                quantity=trade_qty,
                newClientOrderId=f'entry_{ts:.0f}',
            )
            self._order_tp = self._client.futures_create_order(
                symbol=self._symbol,
                side=opposite,
                type=Client.FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                quantity=trade_qty,
                stopPrice=tp_price,
                closePosition=True,
                newClientOrderId=f'tp_{ts:.0f}',
            )
            self._order_sl = self._client.futures_create_order(
                symbol=self._symbol,
                side=opposite,
                type=Client.FUTURE_ORDER_TYPE_STOP_MARKET,
                quantity=trade_qty,
                stopPrice=sl_price,
                closePosition=True,
                newClientOrderId=f'sl_{ts:.0f}',
            )
            self._entry_side  = side
            self._trade_qty   = trade_qty
            self._processing  = False
            return True

        except Exception as e:
            logger.error(f"Failed to open position: {e}")
            self._processing = False
            self.close_all()
            return False

    # ------------------------------------------------------------------ #
    # Monitor                                                              #
    # ------------------------------------------------------------------ #

    def check_if_closed(self) -> Optional[str]:
        """
        Poll Binance to see if the TP or SL order has been filled.
        Call this on each candle open in live mode.

        Returns 'win' (TP filled), 'loss' (SL filled or order canceled),
        or None (still open / testnet / nothing to do).
        """
        if not self.has_position() or self.is_processing() or self._testnet:
            return None

        try:
            tp_status = self._fetch_order_status(self._order_tp)
            sl_status = self._fetch_order_status(self._order_sl)

            if tp_status == 'FILLED':
                logger.info("TP order filled — closing position")
                self.close_all()
                return 'win'

            if sl_status == 'FILLED':
                logger.info("SL order filled — closing position")
                self.close_all()
                return 'loss'

            terminal = ('CANCELED', 'EXPIRED', 'REJECTED')
            if tp_status in terminal or sl_status in terminal:
                logger.warning(
                    f"Order in terminal state — tp={tp_status} sl={sl_status}. Closing."
                )
                self.close_all()
                return 'loss'

        except Exception as e:
            logger.error(f"check_if_closed error: {e}")

        return None

    # ------------------------------------------------------------------ #
    # Close                                                                #
    # ------------------------------------------------------------------ #

    def close_all(self) -> None:
        """Cancel all open orders and close any open position on the exchange."""
        self._processing = True
        if not self._testnet:
            try:
                self._client.futures_cancel_all_open_orders(symbol=self._symbol)
            except Exception as e:
                logger.error(f"cancel_all_open_orders error: {e}")
            self._close_position_if_open()

        self._order_main  = None
        self._order_tp    = None
        self._order_sl    = None
        self._entry_side  = None
        self._entry_price = None
        self._trade_qty   = None
        self._processing  = False
        logger.info("Position closed, all orders cleared")

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _close_position_if_open(self) -> None:
        try:
            infos = self._client.futures_position_information(symbol=self._symbol)
            if not infos:
                return
            amt = float(infos[0]['positionAmt'])
            if amt == 0.0:
                return
            close_side = 'SELL' if amt > 0 else 'BUY'
            self._client.futures_create_order(
                symbol=self._symbol,
                side=close_side,
                type=Client.FUTURE_ORDER_TYPE_MARKET,
                quantity=abs(amt),
                newClientOrderId=f'close_{time.time():.0f}',
            )
            logger.info(f"Closed position: amt={amt}")
        except Exception as e:
            logger.error(f"_close_position_if_open error: {e}")

    def _fetch_order_status(self, order: Optional[dict]) -> Optional[str]:
        if order is None:
            return None
        try:
            remote = self._client.futures_get_order(
                symbol=self._symbol,
                orderId=order['orderId'],
            )
            return remote.get('status')
        except Exception:
            return None
