from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

def _score(stats: dict, min_trades: int) -> tuple[int, float]:
    """Two-tier score: Tier 1 (live-proven) always beats Tier 2 (seed-only).

    Tier 1: trade_count >= min_trades → ranked by live total_winning_usdt.
    Tier 2: trade_count <  min_trades → ranked by seeded_winning_usdt (backtest).
    Python tuple comparison ensures any (1, x) > any (0, y).
    """
    count = stats.get("trade_count", 0)
    if count >= min_trades:
        return (1, stats.get("total_winning_usdt", 0.0))
    return (0, stats.get("seeded_winning_usdt", 0.0))


_SENTINEL = object()  # distinguishes "never seen" from None in _last_best


class VirtualTracker:
    def __init__(
        self,
        mode: Literal["test", "live"],
        orders_path: Path,
        efficiency_path: Path,
        get_min_trades: Callable[[str], int] = lambda _: 3,
    ) -> None:
        self._mode = mode
        self._orders_path = orders_path
        self._efficiency_path = efficiency_path
        self._get_min_trades = get_min_trades
        self._efficiency: dict = self._load_efficiency()
        self._last_best: dict[str, str | None] = {}

    def clear_session_data(self, symbols: list[str]) -> None:
        """Wipe in-memory and on-disk efficiency so the new session starts clean."""
        self._efficiency = {}
        if self._efficiency_path.exists():
            try:
                self._efficiency_path.unlink()
            except Exception as exc:
                logger.warning(f"Could not delete efficiency file: {exc}")

    def seed_from_backtest(self, symbol: str, backtest_path: Path) -> None:
        """Seed efficiency scores from backtest results.

        Sets seeded_winning_usdt as a fallback score for preset selection but
        keeps trade_count at 0 so the UI never shows backtest history as if it
        were live virtual trades.
        """
        if not backtest_path.exists():
            logger.warning(f"No backtest file for {symbol}: {backtest_path}")
            return
        try:
            data = json.loads(backtest_path.read_text())
            for name, preset_data in data.get("presets", {}).items():
                balance_start = preset_data.get("balance_start", 1000.0)
                # Use net total_profit_pct (matches Backtest page Profit% column).
                # Falls back to summing all trade profits if preset-level field is absent.
                if "total_profit_pct" in preset_data:
                    seeded = preset_data["total_profit_pct"] / 100.0 * balance_start
                else:
                    trades = preset_data.get("trades", [])
                    seeded = sum(t.get("profit_pct", 0.0) / 100.0 * balance_start for t in trades)
                # Preserve live-accumulated data across restarts — only refresh the
                # seeded fallback score so rankings improve as live trades accumulate.
                existing = self._efficiency.setdefault(symbol, {}).get(name, {})
                self._efficiency[symbol][name] = {
                    "total_winning_usdt": existing.get("total_winning_usdt", 0.0),
                    "trade_count":        existing.get("trade_count", 0),
                    "seeded_winning_usdt": seeded,
                }
            self._save_efficiency()
        except Exception as exc:
            logger.error(f"Failed to seed efficiency for {symbol}: {exc}")

    def best_preset(self, symbol: str) -> str | None:
        symbol_data = self._efficiency.get(symbol, {})
        if not symbol_data:
            return None

        min_t = self._get_min_trades(symbol)
        best = max(symbol_data, key=lambda n: _score(symbol_data[n], min_t))
        result = best if _score(symbol_data[best], min_t)[1] >= 0 else None

        prev = self._last_best.get(symbol, _SENTINEL)
        if prev is not _SENTINEL and prev != result:
            prev_stats = symbol_data.get(prev or '', {})
            new_stats = symbol_data.get(result or '', {})
            logger.info(
                f"[{symbol}] Best preset changed: {prev!r} -> {result!r} | "
                f"prev(cnt={prev_stats.get('trade_count', 0)}, "
                f"seeded={prev_stats.get('seeded_winning_usdt', 0.0):.2f}, "
                f"live={prev_stats.get('total_winning_usdt', 0.0):.2f}, "
                f"score={_score(prev_stats, min_t)}) | "
                f"new(cnt={new_stats.get('trade_count', 0)}, "
                f"seeded={new_stats.get('seeded_winning_usdt', 0.0):.2f}, "
                f"live={new_stats.get('total_winning_usdt', 0.0):.2f}, "
                f"score={_score(new_stats, min_t)})"
            )
        self._last_best[symbol] = result
        return result

    def get_efficiency(self, symbol: str, preset: str) -> dict:
        return self._efficiency.get(symbol, {}).get(preset, {"total_winning_usdt": 0.0, "trade_count": 0})

    def get_efficiency_score(self, symbol: str) -> float:
        symbol_data = self._efficiency.get(symbol, {})
        if not symbol_data:
            return 0.0
        min_t = self._get_min_trades(symbol)
        best_tuple = max(_score(stats, min_t) for stats in symbol_data.values())
        return best_tuple[1]

    def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
        stats = self._efficiency.get(symbol, {}).get(preset_name, {})
        return _score(stats, self._get_min_trades(symbol))[1]

    def record_closed_trade(self, symbol: str, preset: str, profit_usdt: float) -> None:
        eff = self.get_efficiency(symbol, preset)
        self._set_efficiency(symbol, preset, total_winning=eff["total_winning_usdt"] + profit_usdt, count=eff["trade_count"] + 1)

    def _set_efficiency(self, symbol: str, preset: str, total_winning: float, count: int) -> None:
        existing = self._efficiency.get(symbol, {}).get(preset, {})
        self._efficiency.setdefault(symbol, {})[preset] = {
            "total_winning_usdt": total_winning,
            "trade_count": count,
            "seeded_winning_usdt": existing.get("seeded_winning_usdt", 0.0),
        }
        self._save_efficiency()

    def _load_efficiency(self) -> dict:
        if self._efficiency_path.exists():
            try:
                return json.loads(self._efficiency_path.read_text())
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return {}

    def _save_efficiency(self) -> None:
        self._efficiency_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._efficiency_path.with_name(f"{self._efficiency_path.stem}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self._efficiency, indent=2))
        tmp.replace(self._efficiency_path)
