from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_MIN_TRADES = 4


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

    def seed_from_backtest(self, symbol: str, backtest_path: Path) -> None:
        if not backtest_path.exists():
            logger.warning(f"No backtest file for {symbol}: {backtest_path}")
            return
        try:
            data = json.loads(backtest_path.read_text())
            for preset_data in data.get("presets", []):
                name = preset_data.get("name", "")
                trades = preset_data.get("trades", [])
                winning_usdt = sum(t.get("profit_usdt", 0.0) for t in trades if t.get("profit_usdt", 0.0) > 0)
                self._set_efficiency(symbol, name, total_winning=winning_usdt, count=len(trades))
        except Exception as exc:
            logger.error(f"Failed to seed efficiency for {symbol}: {exc}")

    def best_preset(self, symbol: str) -> str | None:
        symbol_data = self._efficiency.get(symbol, {})
        eligible = {
            name: stats for name, stats in symbol_data.items()
            if stats.get("trade_count", 0) >= _MIN_TRADES
        }
        if not eligible:
            return None
        return max(eligible, key=lambda n: eligible[n].get("total_winning_usdt", 0.0))

    def get_efficiency(self, symbol: str, preset: str) -> dict:
        return self._efficiency.get(symbol, {}).get(preset, {"total_winning_usdt": 0.0, "trade_count": 0})

    def record_closed_trade(self, symbol: str, preset: str, profit_usdt: float) -> None:
        eff = self.get_efficiency(symbol, preset)
        new_winning = eff["total_winning_usdt"] + (profit_usdt if profit_usdt > 0 else 0.0)
        self._set_efficiency(symbol, preset, total_winning=new_winning, count=eff["trade_count"] + 1)

    def _set_efficiency(self, symbol: str, preset: str, total_winning: float, count: int) -> None:
        self._efficiency.setdefault(symbol, {})[preset] = {
            "total_winning_usdt": total_winning,
            "trade_count": count,
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
        tmp = self._efficiency_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._efficiency, indent=2))
        tmp.replace(self._efficiency_path)
