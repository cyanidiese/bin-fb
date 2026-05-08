from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_ENTRIES = 100


def append_entry(
    path: Path,
    level: str,
    title: str,
    detail: str,
    source: str,
) -> None:
    entries = _read(path)
    entries.append({
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "title": title,
        "detail": detail,
        "source": source,
    })
    if len(entries) > MAX_ENTRIES:
        entries = entries[len(entries) - MAX_ENTRIES:]
    _write(path, entries)


def _read(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _write(path: Path, entries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(path)
