from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 10_000


def record(
    path: Path,
    balance: float,
    trigger: str,
    symbol: Optional[str] = None,
    leverage: Optional[int] = None,
    pnl_usdt: Optional[float] = None,
) -> None:
    """Append one balance event. Caps at MAX_ENTRIES (oldest trimmed first).

    trigger values: 'startup' | 'order_open' | 'order_close' | 'balance_refresh'
    """
    entry: dict = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'balance': balance,
        'trigger': trigger,
    }
    if symbol is not None:
        entry['symbol'] = symbol
    if leverage is not None:
        entry['leverage'] = leverage
    if pnl_usdt is not None:
        entry['pnl_usdt'] = pnl_usdt

    _append(path, entry)


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception as exc:
            logger.warning(f"balance_history: failed to read {path}, starting fresh: {exc}")
            existing = []
    existing.append(entry)
    if len(existing) > MAX_ENTRIES:
        existing = existing[-MAX_ENTRIES:]
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)
