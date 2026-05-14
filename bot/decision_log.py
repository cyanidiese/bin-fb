from __future__ import annotations

import json
import logging
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

    _append(path, entry)


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
        existing = existing[-MAX_ENTRIES:]
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)
