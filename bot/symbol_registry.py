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

from config.safe_write import write_json

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path('symbol_registry.json')

Event = Literal['added', 'removed']


class SymbolRegistry:
    def __init__(
        self,
        seed_symbols: list[str],
        registry_path: Path = _DEFAULT_PATH,
        read_only: bool = False,
    ) -> None:
        self._path = registry_path
        # The mirror instance mounts symbol_registry.json :ro — it shares the trading
        # bot's rules and must never mutate them. Defaults to False so the trading
        # bot is unaffected.
        self._read_only = read_only
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

    def is_disabled(self, symbol: str) -> bool:
        return symbol in self._disabled

    def is_symbol_paused(self, symbol: str) -> bool:
        return symbol in self._paused

    def pause_symbol(self, symbol: str) -> None:
        with self._lock:
            self._paused[symbol] = {
                "paused_at": datetime.now(timezone.utc).isoformat(),
            }
            self._persist()

    def resume_symbol(self, symbol: str) -> None:
        with self._lock:
            self._paused.pop(symbol, None)
            self._persist()

    def get_paused_symbols(self) -> dict:
        return dict(self._paused)

    def is_rank_disabled(self, symbol: str, rank: int) -> bool:
        return rank in self._disabled_ranks.get(symbol, [])

    def disable_rank(self, symbol: str, rank: int) -> None:
        with self._lock:
            ranks = self._disabled_ranks.setdefault(symbol, [])
            if rank not in ranks:
                ranks.append(rank)
            self._persist()

    def enable_rank(self, symbol: str, rank: int) -> None:
        with self._lock:
            ranks = self._disabled_ranks.get(symbol, [])
            if rank in ranks:
                ranks.remove(rank)
            if not ranks:
                self._disabled_ranks.pop(symbol, None)
            self._persist()

    def disable(self, symbol: str, reason: str) -> None:
        with self._lock:
            self._disabled[symbol] = {
                "reason": reason,
                "disabled_at": datetime.now(timezone.utc).isoformat(),
            }
            lost_weight = self._weights.get(symbol, 0.0)
            self._weights[symbol] = 0.0
            active = [
                s for s in self._weights
                if s != symbol and self._weights[s] > 0 and not self.is_disabled(s)
            ]
            if active:
                per_symbol = lost_weight / len(active)
                for s in active:
                    self._weights[s] += per_symbol
            self._persist()

    def reenable(self, symbol: str) -> None:
        with self._lock:
            self._disabled.pop(symbol, None)
            active = [s for s in self._symbols if not self.is_disabled(s)]
            if active:
                per = 1.0 / len(active)
                for s in active:
                    self._weights[s] = per
            self._persist()

    def get_weight(self, symbol: str) -> float:
        with self._lock:
            return self._weights.get(symbol, 0.0)

    def get_disabled(self) -> dict:
        return dict(self._disabled)

    def get_weights(self) -> dict:
        """Return a snapshot of the full weights dict."""
        with self._lock:
            return dict(self._weights)

    def set_weight(self, symbol: str, weight: float) -> None:
        """Update a symbol's weight (float supported) and persist."""
        sym = symbol.upper()
        with self._lock:
            if sym in self._weights:
                self._weights[sym] = weight
                self._persist()

    def get_leverage_override(self, symbol: str) -> int:
        """Return the exchange-constraint-derived leverage override, or 0 if none."""
        with self._lock:
            return self._leverage_overrides.get(symbol.upper(), 0)

    def set_leverage_override(self, symbol: str, leverage: int) -> None:
        with self._lock:
            self._leverage_overrides[symbol.upper()] = leverage
            self._persist()

    def all_disabled(self) -> bool:
        return len(self._symbols) > 0 and all(self.is_disabled(s) for s in self._symbols)

    def reload_from_disk(self) -> None:
        """Re-read mutable external state from the registry file.

        Called at the start of each candle so dashboard changes (disable, weight
        edits) take effect within one candle without a bot restart.
        The in-memory state the bot has written since startup is always reflected
        in the file already (every mutating method calls _persist()), so a full
        reload is safe and idempotent under normal operation.
        """
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except Exception as exc:
            logger.warning(f"SymbolRegistry.reload_from_disk: cannot read file ({exc}) — keeping current state")
            return
        with self._lock:
            self._disabled = data.get('disabled', {})
            self._weights = data.get('weights', self._weights)
            self._paused = data.get('paused', {})
            self._disabled_ranks = data.get('disabled_ranks', self._disabled_ranks)
            self._leverage_overrides = {k: int(v) for k, v in data.get('leverage_overrides', {}).items()}

    # ── internal ────────────────────────────────────────────────────────

    def _load(self, seed: list[str]) -> None:
        existed = self._path.exists()
        if existed:
            try:
                data = json.loads(self._path.read_text())
                self._symbols = [s.upper() for s in data.get('symbols', seed)]
                self._status = data.get('status', {})
                self._weights = data.get('weights', {})
                self._disabled = data.get('disabled', {})
                self._disabled_ranks: dict[str, list[int]] = data.get('disabled_ranks', {})
                self._paused: dict[str, dict] = data.get('paused', {})
                self._leverage_overrides: dict[str, int] = {k: int(v) for k, v in data.get('leverage_overrides', {}).items()}
                logger.info(
                    f"SymbolRegistry: loaded {len(self._symbols)} symbol(s) from {self._path}"
                )
                return
            except Exception as exc:
                logger.error(
                    f"SymbolRegistry: cannot read {self._path} ({exc}) "
                    f"— falling back to config seed"
                )
        self._symbols = [s.upper() for s in seed]
        self._status = {s: {'backtest': 'none', 'pid': None} for s in self._symbols}
        self._weights = {s: 1.0 / len(self._symbols) for s in self._symbols} if self._symbols else {}
        self._disabled: dict[str, dict] = {}
        self._disabled_ranks: dict[str, list[int]] = {}
        self._paused: dict[str, dict] = {}
        self._leverage_overrides: dict[str, int] = {}
        if existed:
            # The file is there but unreadable. Persisting the seed here would replace
            # real weights, disabled, paused and leverage_overrides with defaults — and
            # a truncated read is transient, so the next restart could have recovered
            # them. Run from the seed in memory and leave the file alone.
            logger.error(
                f"SymbolRegistry: running from the config seed in memory; "
                f"{self._path} left untouched so a later restart can recover it"
            )
            return
        self._persist()
        logger.info(f"SymbolRegistry: seeded {len(self._symbols)} symbol(s) from config")

    def _persist(self) -> None:
        data = {
            'symbols': self._symbols,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'status': self._status,
            'weights': self._weights,
            'disabled': self._disabled,
            'disabled_ranks': self._disabled_ranks,
            'paused': self._paused,
            'leverage_overrides': self._leverage_overrides,
        }
        if self._read_only:
            logger.debug(
                f"SymbolRegistry: read-only instance — not writing {self._path}")
            return
        # write_json prefers tmp+rename and falls back to an in-place write on a
        # bind-mounted file, where rename returns EBUSY. Two processes read this file
        # now, so the atomic path matters wherever it is available; where it is not,
        # _load() tolerates a torn read and refuses to overwrite what it cannot parse.
        try:
            write_json(self._path, data)
        except Exception as exc:
            # Never propagate. _persist() runs from __init__ and from every
            # pause/disable/weight change; dying over a disk problem while the
            # in-memory state is perfectly usable is the wrong trade.
            logger.error(f"SymbolRegistry: failed to write {self._path}: {exc}")

    def _fire(self, event: Event, symbol: str) -> None:
        for cb in self._subscribers:
            try:
                cb(event, symbol)
            except Exception as exc:
                logger.error(f"SymbolRegistry: subscriber error for {event}/{symbol}: {exc}")
