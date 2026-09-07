from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_ENTRIES = 1000


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
        entries = entries[-MAX_ENTRIES:]
    _write(path, entries)


def read_entries(path: Path) -> list[dict]:
    """The log's entries, oldest first. Empty list when the file is missing or bad.

    Public wrapper over _read so callers outside this module do not reach for a private
    name. Used at startup to spot a ban alert the previous run never closed out.
    """
    return _read(path)


def trim_to(path: Path, keep: int) -> int:
    """Keep only the latest `keep` entries. Returns the new count."""
    entries = _read(path)
    if len(entries) <= keep:
        return len(entries)
    entries = entries[-keep:]
    _write(path, entries)
    return len(entries)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


def _write(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(path)
