from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_MIN_TRADES = 8  # combined real + virtual trades before live score overrides the backtest seed
_SENTINEL = object()  # distinguishes "never seen" from None in _last_best


class VirtualTracker:
    def __init__(
        self,
        mode: Literal["test", "live"],
        orders_path: Path,
        efficiency_path: Path,
    ) -> None:
        self._mode = mode
        self._orders_path = orders_path
        self._efficiency_path = efficiency_path
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
                self._efficiency.setdefault(symbol, {})[name] = {
                    "total_winning_usdt": 0.0,
                    "trade_count": 0,
                    "seeded_winning_usdt": seeded,
                }
            self._save_efficiency()
        except Exception as exc:
            logger.error(f"Failed to seed efficiency for {symbol}: {exc}")

    def best_preset(self, symbol: str) -> str | None:
        symbol_data = self._efficiency.get(symbol, {})
        if not symbol_data:
            return None

        def _score(stats: dict) -> float:
            count = stats.get("trade_count", 0)
            seeded = stats.get("seeded_winning_usdt", 0.0)
            live = stats.get("total_winning_usdt", 0.0)
            # Pure seeded until MIN_TRADES to prevent early trades from displacing an established preset
            if count >= _MIN_TRADES:
                return live
            return seeded

        best = max(symbol_data, key=lambda n: _score(symbol_data[n]))
        result = best if _score(symbol_data[best]) >= 0 else None

        prev = self._last_best.get(symbol, _SENTINEL)
        if prev is not _SENTINEL and prev != result:
            prev_stats = symbol_data.get(prev or '', {})
            new_stats = symbol_data.get(result or '', {})
            logger.info(
                f"[{symbol}] Best preset changed: {prev!r} -> {result!r} | "
                f"prev(cnt={prev_stats.get('trade_count', 0)}, "
                f"seeded={prev_stats.get('seeded_winning_usdt', 0.0):.2f}, "
                f"live={prev_stats.get('total_winning_usdt', 0.0):.2f}, "
                f"score={_score(prev_stats):.2f}) | "
                f"new(cnt={new_stats.get('trade_count', 0)}, "
                f"seeded={new_stats.get('seeded_winning_usdt', 0.0):.2f}, "
                f"live={new_stats.get('total_winning_usdt', 0.0):.2f}, "
                f"score={_score(new_stats):.2f})"
            )
        self._last_best[symbol] = result
        return result

    def get_efficiency(self, symbol: str, preset: str) -> dict:
        return self._efficiency.get(symbol, {}).get(preset, {"total_winning_usdt": 0.0, "trade_count": 0})

    def get_efficiency_score(self, symbol: str) -> float:
        symbol_data = self._efficiency.get(symbol, {})
        if not symbol_data:
            return 0.0
        best = float('-inf')
        for stats in symbol_data.values():
            count = stats.get('trade_count', 0)
            seeded = stats.get('seeded_winning_usdt', 0.0)
            live = stats.get('total_winning_usdt', 0.0)
            score = live if count >= _MIN_TRADES else seeded
            best = max(best, score)
        return best if best != float('-inf') else 0.0

    def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
        stats = self._efficiency.get(symbol, {}).get(preset_name, {})
        count = stats.get('trade_count', 0)
        seeded = stats.get('seeded_winning_usdt', 0.0)
        live = stats.get('total_winning_usdt', 0.0)
        return live if count >= _MIN_TRADES else seeded

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
