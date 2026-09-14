"""An audit trail for `symbol_weights`, because nothing else records who changed what.

`symbol_weights` is the single biggest lever on real-order sizing: it gates candidacy
(weight 0 means no real orders at all) and, under TATS, splits the deployable budget.
It can be changed from at least four places — by hand over SSH, from the dashboard, by
`weight_rebalancer`, and by editing `risk_config.json` directly — and until now none of
them left a trace.

Measured cost of that gap, 2026-09-11: ETHFIUSDT dropped 9 -> 3 and SOLUSDT 8 -> 3. With
no record, the change was attributed to `weight_rebalancer`, which turned out to be
disabled (`enabled: false`, zero log lines) — the edits had been made by hand. A session
was spent reaching the wrong conclusion and then correcting it, on a question a one-line
log entry answers outright.

This module diffs the live weights against the last snapshot on every config reload and
appends any change. It is deliberately a *detector*, not a hook on the writers: it cannot
be bypassed by a new code path or a manual edit, which is exactly the case that caused
the confusion.

Never raises — it runs on the candle path, and an audit log must never be able to stop
trading.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Change events retained. Weights move rarely, so this is many months of history.
MAX_CHANGES = 2000


def audit(
    path: Path,
    weights: dict,
    source: str = 'candle_reload',
) -> list[dict]:
    """Diff `weights` against the stored snapshot, record changes, return them.

    The first call on a fresh file records the snapshot and returns nothing: with no
    prior state there is no change to report, and inventing "0 -> 9" for every symbol
    would bury the real edits that follow.

    A symbol appearing for the first time is reported with `old: None`; one that
    disappears from the config is reported with `new: None`, since dropping a symbol
    from `symbol_weights` silently stops its real orders just as setting it to 0 does.
    """
    try:
        clean = _coerce(weights)
        store = _read(path)
        first_run = 'snapshot' not in store
        previous = _coerce(store.get('snapshot') or {})

        if first_run:
            store['snapshot'] = clean
            store.setdefault('changes', [])
            _write(path, store)
            return []

        changes: list[dict] = []
        ts = datetime.now(timezone.utc).isoformat()
        for sym in sorted(set(previous) | set(clean)):
            old = previous.get(sym)
            new = clean.get(sym)
            if old == new:
                continue
            changes.append({
                'timestamp': ts,
                'symbol': sym,
                'old': old,
                'new': new,
                'source': source,
            })

        if not changes:
            return []

        store['snapshot'] = clean
        existing = store.get('changes')
        if not isinstance(existing, list):
            existing = []
        existing.extend(changes)
        store['changes'] = existing[-MAX_CHANGES:]
        _write(path, store)

        for c in changes:
            logger.warning(
                "symbol_weights changed: %s %s -> %s (%s)",
                c['symbol'], _fmt(c['old']), _fmt(c['new']), c['source'],
            )
        return changes
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"weight_audit: could not audit weights: {exc}")
        return []


def history(path: Path, symbol: Optional[str] = None) -> list[dict]:
    """Recorded changes, newest last. Optionally for one symbol. Never raises."""
    try:
        rows = _read(path).get('changes') or []
        if not isinstance(rows, list):
            return []
        if symbol is None:
            return rows
        return [r for r in rows if isinstance(r, dict) and r.get('symbol') == symbol]
    except Exception:  # pragma: no cover - defensive
        return []


def _fmt(v) -> str:
    return 'absent' if v is None else f'{v:g}'


def _coerce(raw: dict) -> dict:
    """Weights as floats, dropping anything unparseable.

    Values arrive as ints from the dashboard and floats from hand edits; comparing them
    raw would report 9 -> 9.0 as a change on the first reload after a manual edit.
    """
    out = {}
    for k, v in (raw or {}).items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path: Path, store: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(path)
