from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LeverageTracker:
    """
    Tracks the global leverage level. Starts at level 1 and advances when every
    active symbol has at least one closed real order recorded at the current level.
    Persists to data/leverage_state_{mode}.json (atomic write).
    """

    def __init__(
        self,
        mode: str,
        active_symbols: list[str],
        data_path: Path,
        max_level: int = 5,
    ) -> None:
        self._mode = mode
        self._active_symbols: list[str] = list(active_symbols)
        self._data_path = data_path
        self._max_level = max_level
        self._current_level: int = 1
        self._completed: dict[str, set[int]] = {}
        self._load()

    def get_current_level(self) -> int:
        return self._current_level

    def record_closed(self, symbol: str, leverage: int) -> bool:
        """Record a closed real order at the given leverage level.

        Returns True if the global level advanced as a result.
        """
        self._completed.setdefault(symbol, set()).add(leverage)
        advanced = self._check_advance()
        self._save()
        return advanced

    def add_symbol(self, symbol: str) -> bool:
        """Add a new active symbol. Returns True if this immediately triggers advancement."""
        if symbol not in self._active_symbols:
            self._active_symbols.append(symbol)
        advanced = self._check_advance()
        self._save()
        return advanced

    def remove_symbol(self, symbol: str) -> bool:
        """Remove a symbol. Returns True if removal triggers advancement."""
        self._active_symbols = [s for s in self._active_symbols if s != symbol]
        advanced = self._check_advance()
        self._save()
        return advanced

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        """Update mode after a live/test switch. Reloads persisted state for the new mode."""
        self._mode = new_mode
        self._data_path = data_path
        self._current_level = 1
        self._completed = {}
        self._load()

    def _check_advance(self) -> bool:
        if not self._active_symbols:
            return False
        advanced = False
        while self._current_level < self._max_level:
            all_graduated = all(
                self._current_level in self._completed.get(sym, set())
                for sym in self._active_symbols
            )
            if not all_graduated:
                break
            self._current_level += 1
            advanced = True
            logger.info(f"LeverageTracker: level advanced to {self._current_level}")
        return advanced

    def _save(self) -> None:
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'current_level': self._current_level,
            'completed': {sym: sorted(lev_set) for sym, lev_set in self._completed.items()},
        }
        tmp = self._data_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._data_path)

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text())
            self._current_level = int(data.get('current_level', 1))
            for sym, levels in data.get('completed', {}).items():
                self._completed[sym] = set(int(l) for l in levels)
        except Exception as exc:
            logger.warning(f"LeverageTracker: failed to load state, starting fresh: {exc}")
