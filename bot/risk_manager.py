import asyncio
import logging

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, max_total_pct: float = 80.0, max_per_symbol_pct: float = 40.0):
        self._max_total_pct = max_total_pct
        self._max_per_symbol_pct = max_per_symbol_pct
        self._allocated: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def can_open(self, symbol: str, size_usdt: float, total_capital: float) -> bool:
        """Returns True if opening a position of `size_usdt` for `symbol` stays within caps."""
        async with self._lock:
            if total_capital <= 0:
                return True
            per_sym = self._allocated.get(symbol, 0.0) + size_usdt
            total = sum(self._allocated.values()) + size_usdt
            if per_sym / total_capital * 100 > self._max_per_symbol_pct:
                logger.info(
                    f"RiskManager: {symbol} would exceed per-symbol cap "
                    f"({per_sym:.0f}/{total_capital:.0f} USDT, limit {self._max_per_symbol_pct}%)"
                )
                return False
            if total / total_capital * 100 > self._max_total_pct:
                logger.info(
                    f"RiskManager: total exposure would exceed cap "
                    f"({total:.0f}/{total_capital:.0f} USDT, limit {self._max_total_pct}%)"
                )
                return False
            return True

    async def open(self, symbol: str, size_usdt: float) -> None:
        """Record that `size_usdt` has been allocated to `symbol`."""
        async with self._lock:
            self._allocated[symbol] = self._allocated.get(symbol, 0.0) + size_usdt
            logger.info(
                f"RiskManager.open: {symbol} +{size_usdt:.0f} USDT, "
                f"total={sum(self._allocated.values()):.0f}"
            )

    async def close(self, symbol: str) -> None:
        """Release all allocation for `symbol`."""
        async with self._lock:
            removed = self._allocated.pop(symbol, 0.0)
            logger.info(
                f"RiskManager.close: {symbol} -{removed:.0f} USDT, "
                f"total={sum(self._allocated.values()):.0f}"
            )

    def get_allocated(self) -> dict[str, float]:
        """Snapshot of current allocations (not lock-protected, for logging only)."""
        return dict(self._allocated)
