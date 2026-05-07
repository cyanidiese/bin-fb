"""
Single source of truth for active symbols.

Seed behaviour: if symbol_registry.json does not exist, it is created from
the symbols list passed as `seed_symbols` (typically loaded from .env).
After that, the file is the authority — the .env list is ignored.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Literal

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path('symbol_registry.json')

Event = Literal['added', 'removed']


class SymbolRegistry:
    def __init__(
        self,
        seed_symbols: list[str],
        registry_path: Path = _DEFAULT_PATH,
    ) -> None:
        self._path = registry_path
        self._lock = Lock()
        self._symbols: list[str] = []
        self._status: dict[str, dict] = {}
        self._subscribers: list[Callable[[Event, str], None]] = []
        self._load(seed_symbols)

    # ── public interface ────────────────────────────────────────────────

    def get_symbols(self) -> list[str]:
        with self._lock:
            return list(self._symbols)

    def add_symbol(self, symbol: str) -> tuple[bool, str]:
        symbol = symbol.upper().strip()
        if not symbol:
            return False, 'Symbol name is empty'
        with self._lock:
            if symbol in self._symbols:
                return False, f'{symbol} is already active'
            self._symbols.append(symbol)
            self._status[symbol] = {'backtest': 'none', 'pid': None}
            self._persist()
        self._fire('added', symbol)
        return True, ''

    def remove_symbol(self, symbol: str) -> tuple[bool, str]:
        symbol = symbol.upper().strip()
        with self._lock:
            if symbol not in self._symbols:
                return False, f'{symbol} is not active'
            self._symbols.remove(symbol)
            self._status.pop(symbol, None)
            self._persist()
        self._fire('removed', symbol)
        return True, ''

    def subscribe(self, callback: Callable[[Event, str], None]) -> None:
        self._subscribers.append(callback)

    # ── internal ────────────────────────────────────────────────────────

    def _load(self, seed: list[str]) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._symbols = [s.upper() for s in data.get('symbols', seed)]
                self._status = data.get('status', {})
                logger.info(
                    f"SymbolRegistry: loaded {len(self._symbols)} symbol(s) from {self._path}"
                )
                return
            except Exception as exc:
                logger.warning(
                    f"SymbolRegistry: cannot read {self._path} ({exc}) "
                    f"— falling back to config seed"
                )
        self._symbols = [s.upper() for s in seed]
        self._status = {s: {'backtest': 'none', 'pid': None} for s in self._symbols}
        self._persist()
        logger.info(f"SymbolRegistry: seeded {len(self._symbols)} symbol(s) from config")

    def _persist(self) -> None:
        data = {
            'symbols': self._symbols,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'status': self._status,
        }
        self._path.write_text(json.dumps(data, indent=2))

    def _fire(self, event: Event, symbol: str) -> None:
        for cb in self._subscribers:
            try:
                cb(event, symbol)
            except Exception as exc:
                logger.error(f"SymbolRegistry: subscriber error for {event}/{symbol}: {exc}")
