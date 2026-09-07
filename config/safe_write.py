"""Write JSON to a file that may be a Docker bind mount.

`tmp.write_text(...)` then `tmp.replace(path)` is the textbook atomic write, and it is
what `risk_config._atomic_write()` has always done. It does not work on the two config
files this project bind-mounts as *single files*:

    ./risk_config.json:/app/risk_config.json
    ./symbol_registry.json:/app/symbol_registry.json

Inside the container each of those paths is itself a mount point, and renaming over a
mount point fails. Verified on the server 2026-09-07:

    rename over bind-mounted file: FAILS -> OSError [Errno 16] Device or resource busy

This has never bitten because the only in-container caller, `weight_rebalancer`, is
disabled (`weight_rebalancer.enabled = false`); the config edits that do land come from
the host, where the path is an ordinary file. It would have fired the day rebalancing was
switched on — and it would have broken `SymbolRegistry._persist()` immediately, which
runs on every pause, disable and weight change.

So: prefer the atomic path, fall back to an in-place write where rename is impossible.
The fallback is not atomic, which is why readers of these two files must tolerate a torn
read — `SymbolRegistry._load()` does, and deliberately does not overwrite a file it
could not parse.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_warned: set[str] = set()


def write_json(path: Path, data: dict, indent: int = 2) -> None:
    """Persist `data` as JSON to `path`.

    Atomic (tmp + rename) wherever the filesystem allows it. On a bind-mounted single
    file, where rename returns EBUSY, writes in place instead. A genuine failure — no
    permission, no space, bad directory — still raises, because callers that care need
    to see it.
    """
    text = json.dumps(data, indent=indent)
    tmp = path.with_suffix(path.suffix + '.tmp')
    try:
        tmp.write_text(text)
        tmp.replace(path)
        return
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        key = str(path)
        if key not in _warned:
            _warned.add(key)
            logger.info(
                f"{path.name}: atomic rename unavailable ({exc.strerror}) — this path "
                f"is a bind-mounted file, writing in place instead"
            )
    # Raises on a real problem; the caller decides whether that is fatal.
    path.write_text(text)
