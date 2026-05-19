from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_MIN_TRADES = 8  # combined real + virtual trades before live score overrides the backtest seed


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
            # Once a preset has enough live virtual trades, use the live score.
            # Otherwise fall back to the backtest-seeded score so the bot can
            # still pick a best preset before any runtime data accumulates.
            if stats.get("trade_count", 0) >= _MIN_TRADES:
                return stats.get("total_winning_usdt", 0.0)
            return stats.get("seeded_winning_usdt", 0.0)

        best = max(symbol_data, key=lambda n: _score(symbol_data[n]))
        return best if _score(symbol_data[best]) > 0 else None

    def get_efficiency(self, symbol: str, preset: str) -> dict:
        return self._efficiency.get(symbol, {}).get(preset, {"total_winning_usdt": 0.0, "trade_count": 0})

    def get_efficiency_score(self, symbol: str) -> float:
        symbol_data = self._efficiency.get(symbol, {})
        best = 0.0
        for stats in symbol_data.values():
            if stats.get('trade_count', 0) >= _MIN_TRADES:
                best = max(best, stats.get('total_winning_usdt', 0.0))
            else:
                best = max(best, stats.get('seeded_winning_usdt', 0.0))
        return best

    def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
        stats = self._efficiency.get(symbol, {}).get(preset_name, {})
        if stats.get('trade_count', 0) >= _MIN_TRADES:
            return stats.get('total_winning_usdt', 0.0)
        return stats.get('seeded_winning_usdt', 0.0)

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
        tmp = self._efficiency_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._efficiency, indent=2))
        tmp.replace(self._efficiency_path)
