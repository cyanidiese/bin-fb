from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 5_000


def record(
    path: Path,
    candle_ts: int,
    symbol: str,
    decision: str,
    reason: str,
    balance: float,
    leverage: int,
    efficiency_score: float,
    preset_name: Optional[str] = None,
    signal_type: Optional[str] = None,
    precision_score: Optional[float] = None,
    level: Optional[int] = None,
    scenario: Optional[str] = None,
) -> None:
    """Append one placement decision. Caps at MAX_ENTRIES (oldest trimmed first).

    decision values: 'placed' | 'skip_balance' | 'skip_profit_factor' |
                     'skip_hard_stop' | 'skip_already_open' | 'skip_no_signal'
    """
    entry: dict = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'candle_ts': candle_ts,
        'symbol': symbol,
        'decision': decision,
        'reason': reason,
        'balance': balance,
        'leverage': leverage,
        'efficiency_score': efficiency_score,
    }
    if preset_name is not None:
        entry['preset_name'] = preset_name
    if signal_type is not None:
        entry['signal_type'] = signal_type
    if precision_score is not None:
        entry['precision_score'] = precision_score
    if level is not None:
        entry['level'] = level
    if scenario is not None:
        entry['scenario'] = scenario

    _append(path, entry)


# 'placed' rows are the real-order records every profitability analysis reads. Skips are
# far more numerous and individually far less valuable, so a plain tail-trim throws away
# exactly the rows worth keeping. Measured 2026-09-08: adding reasons to the 17 silent
# rejection paths evicted 15 of 79 'placed' rows within four hours, and shrank the log's
# window from 27 days to 18.
# A protected FLOOR, not a ceiling: the newest MAX_PLACED 'placed' rows are exempt from
# eviction, and any older ones still compete for the remaining slots by recency. So a log
# that is mostly 'placed' still fills to MAX_ENTRIES rather than shrinking to MAX_PLACED.
MAX_PLACED = 1_000   # ~3 real orders/day measured -> years of protected history


def _trim(rows: list) -> list:
    """Trim to MAX_ENTRIES, keeping recent 'placed' rows in preference to skips.

    Preserves original order. Never grows the file beyond MAX_ENTRIES, so the
    read-modify-write cost per decision is unchanged.
    """
    keep_ids = {
        id(e) for e in
        [e for e in rows if e.get('decision') == 'placed'][-MAX_PLACED:]
    }
    budget = MAX_ENTRIES - len(keep_ids)
    kept = []
    for e in reversed(rows):          # newest first, so the budget keeps the newest skips
        if id(e) in keep_ids:
            kept.append(e)         # protected: never evicted while under MAX_ENTRIES
        elif budget > 0:
            kept.append(e)         # everything else, newest first, incl. older 'placed'
            budget -= 1
    kept.reverse()
    return kept


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception as exc:
            logger.warning(f"decision_log: failed to read {path}, starting fresh: {exc}")
            existing = []
    existing.append(entry)
    if len(existing) > MAX_ENTRIES:
        existing = _trim(existing)
    # PID-qualified tmp name prevents collision when multiple processes write concurrently
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)
