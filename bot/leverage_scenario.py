from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Protocol, runtime_checkable

from bot.leverage_tracker import LeverageTracker

logger = logging.getLogger(__name__)


@runtime_checkable
class LeverageScenario(Protocol):
    name: str
    uses_weight_allocation: bool  # False → scenario owns allocation; main.py uses get_deployable_budget

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int: ...

    def record_closed(self, symbol: str, leverage: int) -> None: ...
    def add_symbol(self, symbol: str) -> None: ...
    def remove_symbol(self, symbol: str) -> None: ...
    def reset_for_mode(self, new_mode: str, data_path: Path) -> None: ...
    def get_global_level(self) -> int: ...
    def get_symbol_level(self, symbol: str) -> int: ...


class DefaultScenario:
    """All active symbols must complete level N before any advances to N+1."""
    name = "default"
    uses_weight_allocation = True

    def __init__(
        self,
        mode: str,
        active_symbols: list[str],
        data_path: Path,
        max_level: int = 5,
    ) -> None:
        self._tracker = LeverageTracker(
            mode=mode,
            active_symbols=active_symbols,
            data_path=data_path,
            max_level=max_level,
        )

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        lev = min(self._tracker.get_current_level(), max_policy, bracket_max)
        return max(1, lev)

    def record_closed(self, symbol: str, leverage: int) -> None:
        self._tracker.record_closed(symbol, leverage)

    def add_symbol(self, symbol: str) -> None:
        self._tracker.add_symbol(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self._tracker.remove_symbol(symbol)

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        self._tracker.reset_for_mode(new_mode, data_path)

    def get_global_level(self) -> int:
        return self._tracker.get_current_level()

    def get_symbol_level(self, symbol: str) -> int:
        return self._tracker.get_current_level()


class AllocationScenario:
    """Each symbol advances its own level independently."""
    name = "allocation"
    uses_weight_allocation = True

    def __init__(
        self,
        mode: str,
        active_symbols: list[str],
        data_path: Path,
        max_level: int = 5,
        inherit_from_level: int = 0,
    ) -> None:
        self._data_path = data_path
        self._max_level = max_level
        self._symbol_levels: dict[str, int] = {s: 1 for s in active_symbols}
        self._completed: dict[str, set[int]] = {}
        self._load()
        # Seed symbols missing from saved state when inheriting progress from another scenario
        if inherit_from_level > 1:
            seed = max(1, inherit_from_level - 1)
            for sym in active_symbols:
                if sym not in self._symbol_levels:
                    self._symbol_levels[sym] = seed

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        lev = min(self._symbol_levels.get(symbol, 1), max_policy, bracket_max)
        return max(1, lev)

    def record_closed(self, symbol: str, leverage: int) -> None:
        self._completed.setdefault(symbol, set()).add(leverage)
        self._advance(symbol)
        self._save()

    def add_symbol(self, symbol: str) -> None:
        if symbol not in self._symbol_levels:
            self._symbol_levels[symbol] = 1

    def remove_symbol(self, symbol: str) -> None:
        self._symbol_levels.pop(symbol, None)
        self._completed.pop(symbol, None)

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        self._data_path = data_path
        self._symbol_levels = {}
        self._completed = {}
        self._load()

    def get_global_level(self) -> int:
        levels = list(self._symbol_levels.values())
        return min(levels) if levels else 1

    def get_symbol_level(self, symbol: str) -> int:
        return self._symbol_levels.get(symbol, 1)

    def _advance(self, symbol: str) -> None:
        current = self._symbol_levels.get(symbol, 1)
        done = self._completed.get(symbol, set())
        while current < self._max_level and current in done:
            current += 1
            logger.info(f"AllocationScenario: {symbol} advanced to level {current}")
        self._symbol_levels[symbol] = current

    def _save(self) -> None:
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbol_levels": self._symbol_levels,
            "completed": {s: sorted(ls) for s, ls in self._completed.items()},
        }
        tmp = self._data_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._data_path)

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text())
            self._symbol_levels = {k: int(v) for k, v in data.get("symbol_levels", {}).items()}
            for sym, levels in data.get("completed", {}).items():
                self._completed[sym] = set(int(l) for l in levels)
        except Exception as exc:
            logger.warning(f"AllocationScenario: failed to load state: {exc}")


class FirstHasMostScenario:
    """Leverage is derived immediately from efficiency score. No cross-symbol dependency."""
    name = "first_has_most"
    uses_weight_allocation = True

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        raw = base + math.floor(score * (max_policy - base))
        return min(max(base, raw), max_policy, bracket_max)

    def record_closed(self, symbol: str, leverage: int) -> None:
        pass

    def add_symbol(self, symbol: str) -> None:
        pass

    def remove_symbol(self, symbol: str) -> None:
        pass

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        pass

    def get_global_level(self) -> int:
        return 0

    def get_symbol_level(self, symbol: str) -> int:
        return 0


class BestGetsFirstScenario:
    """Best-scoring symbol gets the full deployable budget; each subsequent symbol gets the remainder.

    Allocation is sequential (score-descending order); no per-symbol weight split.
    Leverage is derived instantly from the efficiency score, same formula as FirstHasMost.
    """
    name = "best_gets_first"
    uses_weight_allocation = False  # main.py tracks remaining pool per candle batch

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        raw = base + math.floor(score * (max_policy - base))
        return min(max(base, raw), max_policy, bracket_max)

    def record_closed(self, symbol: str, leverage: int) -> None:
        pass

    def add_symbol(self, symbol: str) -> None:
        pass

    def remove_symbol(self, symbol: str) -> None:
        pass

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        pass

    def get_global_level(self) -> int:
        return 0

    def get_symbol_level(self, symbol: str) -> int:
        return 0


def create_scenario(
    name: str,
    mode: str,
    active_symbols: list[str],
    data_path: Path,
    max_level: int = 5,
    inherit_from_level: int = 0,
) -> LeverageScenario:  # type: ignore[return-value]
    if name == "allocation":
        return AllocationScenario(mode, active_symbols, data_path, max_level, inherit_from_level)
    if name == "first_has_most":
        return FirstHasMostScenario()
    if name == "best_gets_first":
        return BestGetsFirstScenario()
    if name != "default":
        logger.warning(f"Unknown scenario '{name}', falling back to 'default'")
    return DefaultScenario(mode, active_symbols, data_path, max_level)
