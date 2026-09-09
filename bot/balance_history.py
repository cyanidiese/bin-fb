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


def last_known(path: Path) -> float:
    """The newest balance on disk that reflects a real exchange figure, else 0.0.

    This is the only last-known-good available before any successful API call, so it is
    what the startup seed falls back to when a rate-limit ban makes
    fetch_account_balance() return 0.0 (observed 2026-09-09: a deploy landed inside a
    ban, the seed was skipped, and RiskManager sized real orders off its 1000.00 config
    default against a real 3098.93 — a third of intent, for the 190 minutes left).

    'startup_unconfirmed' entries are skipped: those are the ones written when the
    balance could NOT be confirmed, so trusting them would let a wrong figure written by
    one restart seed the next one, making it self-sustaining.

    Never raises — a missing or corrupt file returns 0.0 and the caller keeps its default.
    """
    try:
        rows = json.loads(Path(path).read_text())
    except Exception:
        return 0.0
    if not isinstance(rows, list):
        return 0.0
    for entry in reversed(rows):
        if not isinstance(entry, dict):
            continue
        if entry.get('trigger') == 'startup_unconfirmed':
            continue
        try:
            bal = float(entry.get('balance') or 0.0)
        except (TypeError, ValueError):
            continue
        if bal > 0:
            return bal
    return 0.0


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
