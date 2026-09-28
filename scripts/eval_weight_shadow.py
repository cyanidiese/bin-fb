#!/usr/bin/env python3
"""Score the weight shadow policies against what actually happened next.

Spec: docs/specs/2026-09-28-weight-shadow-calculator.md
Run on the host, in the repo root (stdlib only, read-only):

    python3 scripts/eval_weight_shadow.py            # both modes, 7-day forward window
    python3 scripts/eval_weight_shadow.py live 3     # one mode, 3-day window

For each snapshot day: forward Profit% per symbol = the would-be-real trades (real orders
plus rank-1 virtual orders) opened in [day, day + N days). Policy value for the day =
sum over symbols of (policy weight / sum of that policy's weights) x forward Profit%.
A policy only earns its keep if it beats `static` over many days.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import statistics
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TZ = ZoneInfo("Europe/Kyiv")
POLICIES = ("static", "tilt", "brake")


def margin_pct(o: dict) -> float | None:
    try:
        m = float(o["entry_price"]) * float(o["quantity"]) / float(o.get("leverage") or 1)
        return float(o.get("pnl_usdt") or 0) / m * 100 if m > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def ts(s: str) -> float:
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def trades(mode: str) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    files = glob.glob(str(DATA / f"virtual_orders_rank1_*_{mode}.json")) + \
        glob.glob(str(DATA / f"real_orders_*_{mode}.json"))
    for f in files:
        name = Path(f).name
        if "_archive_" in name:
            continue
        sym = name.split("_")[3] if name.startswith("virtual") else name.split("_")[2]
        try:
            rows = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        for o in rows if isinstance(rows, list) else []:
            if o.get("result") in (None, "promoted_to_real", "max_age", "closed_early") or not o.get("open_time"):
                continue
            p = margin_pct(o)
            if p is not None:
                out.setdefault(sym, []).append((ts(o["open_time"]), p))
    return out


def evaluate(mode: str, days: int) -> None:
    path = DATA / f"weight_shadow_{mode}.jsonl"
    if not path.exists():
        print(f"{mode}: no snapshots yet ({path.name})")
        return
    snaps = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    tr = trades(mode)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    per_day: dict[str, list[float]] = {p: [] for p in POLICIES}
    used = 0
    for snap in snaps:
        start = dt.datetime.fromisoformat(snap["day"]).replace(tzinfo=TZ).timestamp()
        end = start + days * 86400
        if end > now:
            continue                       # forward window not complete yet
        fwd = {s: sum(p for t, p in tr.get(s, []) if start <= t < end) for s in snap["symbols"]}
        used += 1
        for pol in POLICIES:
            ws = {s: float(v["policies"][pol]) for s, v in snap["symbols"].items()}
            tot = sum(w for w in ws.values() if w > 0)
            per_day[pol].append(sum(w / tot * fwd[s] for s, w in ws.items() if w > 0) if tot > 0 else 0.0)
    print(f"== {mode}: {len(snaps)} snapshot(s), {used} with a complete {days}-day forward window")
    if not used:
        return
    base = per_day["static"]
    for pol in POLICIES:
        v = per_day[pol]
        diff = [a - b for a, b in zip(v, base)]
        better = sum(1 for d in diff if d > 0)
        print(f"   {pol:7} mean {statistics.mean(v):+8.2f}%/day-window  "
              f"vs static {statistics.mean(diff):+7.2f}  better on {better}/{len(diff)} days")


def main() -> int:
    modes = [sys.argv[1]] if len(sys.argv) > 1 else ["test", "live"]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    for m in modes:
        evaluate(m, days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
