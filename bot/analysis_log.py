"""Structured JSONL event log for offline analysis.

Deliberately separate from bot.log:
  * bot.log stays human-readable for operations; this file is machine-parseable,
    so an investigation never needs a regex over free-text log lines again.
  * it carries its own size-based rotation budget, independent of the weekly
    logrotate that governs bot.log.

Design constraints, in order of priority:
  1. It must NEVER raise. This runs inside the live trading loop; an analytics
     write failing must not affect an order.
  2. It must be size-capped. The VPS has ~6.8 GB free; the default budget here is
     100 MB total (5 files x 20 MB), which at the observed event rate is roughly a
     year of history.
  3. It must be cheap. One json.dumps and one buffered write per event.

Every record carries `ts` (UTC ISO) and `event`; the rest is event-specific.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 20 * 1024 * 1024   # 20 MB per file
_DEFAULT_BACKUPS = 5                     # -> 100 MB ceiling

_handler: Optional[logging.Handler] = None
_enabled: bool = False


def configure(
    path: Path,
    enabled: bool = True,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backups: int = _DEFAULT_BACKUPS,
) -> None:
    """Point the analysis log at `path`. Safe to call more than once."""
    global _handler, _enabled
    _enabled = bool(enabled)
    if not _enabled:
        _handler = None
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8', delay=True,
        )
        h.setFormatter(logging.Formatter('%(message)s'))
        _handler = h
        logger.info(
            f"Analysis log at {path} (cap {(max_bytes * (backups + 1)) // (1024*1024)} MB)"
        )
    except Exception as exc:                      # pragma: no cover - disk/permission
        logger.warning(f"Analysis log disabled — could not open {path}: {exc}")
        _handler, _enabled = None, False


def record(event: str, **fields: Any) -> None:
    """Append one event. Never raises, never blocks on a bad value."""
    if not _enabled or _handler is None:
        return
    try:
        payload = {'ts': datetime.now(timezone.utc).isoformat(), 'event': event}
        payload.update(fields)
        line = json.dumps(payload, separators=(',', ':'), default=str)
        _handler.emit(logging.LogRecord(
            name='analysis', level=logging.INFO, pathname='', lineno=0,
            msg=line, args=(), exc_info=None,
        ))
    except Exception:
        # Analytics must never disturb trading. Deliberately silent: a logger call
        # here could itself fail (e.g. disk full) and recurse.
        pass


def is_enabled() -> bool:
    return _enabled and _handler is not None
