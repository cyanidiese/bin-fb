#!/usr/bin/env python3
"""Reconstruct past weight-shadow snapshots from existing order data and score them.

Spec: docs/specs/2026-09-28-weight-shadow-calculator.md (addendum: backfill)
Run on the host, in the repo root (stdlib only). Read-only except for the output file:

    python3 scripts/backfill_weight_shadow.py test          # print scores only
    python3 scripts/backfill_weight_shadow.py test --write  # also write
                                                            # data/weight_shadow_{mode}_history.jsonl

For every past day D (Europe/Kyiv midnight), per symbol, using only trades OPENED before D:
  p7 / p14 = the Preset Efficiency top row: the locked preset (current lock) if any, else the
             preset with the best summed Profit% over the window (trades = real + closed
             virtual orders at every rank).
Policies (same formulas as dashboard/app/api/trades/_weight-shadow.ts):
  tilt  = w x clamp(1 + 0.3 tanh(p14/50), 0.7, 1.3) with >= 20 trades
  brake = w x 0.5 with >= 30 trades and p14 <= -30
Forward result over [D, D+7d), two measures:
  r1   = would-be-real trades (real orders + rank-1 virtual), which exist since 2026-09-07
  top  = the top-row preset's own trades on that symbol (any rank) — longer history
Books:
  funded = symbols with weight > 0 today, today's weights
  equal  = every symbol at weight 1 (more symbols, more statistical power)
"""
from __future__ import annotations

import bisect
import datetime as dt
import glob
import json
import math
import statistics
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TZ = ZoneInfo("Europe/Kyiv")
DAY = 86400
EXCLUDED = {None, "promoted_to_real", "max_age", "closed_early", "rank_change"}


def margin_pct(o: dict) -> float | None:
    try:
        m = float(o["entry_price"]) * float(o["quantity"]) / float(o.get("leverage") or 1)
        return float(o.get("pnl_usdt") or 0) / m * 100 if m > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def ts(s) -> float:
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


class Series:
    """Sorted (time, pct) with prefix sums: window sum and count in O(log n)."""
    def __init__(self, rows):
        rows.sort()
        self.t = [r[0] for r in rows]
        self.cum = [0.0]
        for _, p in rows:
            self.cum.append(self.cum[-1] + p)

    def window(self, a: float, b: float) -> tuple[float, int]:
        i, j = bisect.bisect_left(self.t, a), bisect.bisect_left(self.t, b)
        return self.cum[j] - self.cum[i], j - i


def load(mode: str):
    by_preset: dict[tuple[str, str], list] = {}
    r1: dict[str, list] = {}
    for f in glob.glob(str(DATA / f"virtual_orders_rank*_*_{mode}.json")) + \
            glob.glob(str(DATA / f"real_orders_*_{mode}.json")):
        name = Path(f).name
        if "_archive_" in name:
            continue
        real = name.startswith("real_")
        sym = name.split("_")[2] if real else name.split("_")[3]
        rank1 = (not real) and name.startswith("virtual_orders_rank1_")
        try:
            rows = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        for o in rows if isinstance(rows, list) else []:
            if o.get("result") in EXCLUDED or not o.get("open_time"):
                continue
            if not real and o.get("status") != "closed":
                continue
            p = margin_pct(o)
            if p is None:
                continue
            t = ts(o["open_time"])
            by_preset.setdefault((sym, o.get("preset_name", "")), []).append((t, p))
            if real or rank1:
                r1.setdefault(sym, []).append((t, p))
    return ({k: Series(v) for k, v in by_preset.items()}, {k: Series(v) for k, v in r1.items()})


def top_row(presets: dict[str, Series], lock: str | None, a: float, b: float):
    if lock:
        s = presets.get(lock)
        v, n = s.window(a, b) if s else (0.0, 0)
        return (v if n else None), n, lock
    best = (None, 0, None)
    for name, s in presets.items():
        v, n = s.window(a, b)
        if n and (best[0] is None or v > best[0]):
            best = (v, n, name)
    return best


SHRINK_7D, SHRINK_14D, SCORE_CAP = 10, 20, 0.30      # = dashboard _weight-shadow.ts


def symbol_score(p7, p14):
    """Same as symbolScore() in the dashboard: Profit% shrunk by trade count, 50/50."""
    s7 = (p7[0] or 0) * p7[1] / (p7[1] + SHRINK_7D)
    s14 = (p14[0] or 0) * p14[1] / (p14[1] + SHRINK_14D)
    return 0.5 * s7 + 0.5 * s14


def score_allocation(rows: dict, n: int, budget: float, disabled=frozenset()) -> dict:
    """Same as scoreAllocation(): top-n positive-score symbols share `budget` in proportion
    to score, each at most max(CAP, 1/n) of it; everyone else 0."""
    sc = {s: symbol_score(v["p7"], v["p14"]) for s, v in rows.items() if s not in disabled}
    elig = sorted(((s, x) for s, x in sc.items() if x > 0), key=lambda kv: (-kv[1], kv[0]))[:n]
    tot = sum(x for _, x in elig)
    out = {s: 0.0 for s in rows}
    if tot <= 0 or budget <= 0:
        return out
    cap = max(SCORE_CAP, 1 / len(elig)) * budget
    w = {s: budget * x / tot for s, x in elig}
    for _ in range(20):
        over = {s: v for s, v in w.items() if v > cap + 1e-9}
        if not over:
            break
        ex = sum(v - cap for v in over.values())
        for s in over:
            w[s] = cap
        free = {s: v for s, v in w.items() if v < cap - 1e-9}
        fs = sum(free.values())
        if fs <= 0:
            break
        for s in free:
            w[s] += ex * free[s] / fs
    out.update(w)
    return out


def tilt(w, p14):
    pct, n, _ = p14
    if pct is None or n < 20:
        return w
    return w * min(1.3, max(0.7, 1 + 0.3 * math.tanh(pct / 50)))


def brake(w, p14):
    pct, n, _ = p14
    return w * 0.5 if pct is not None and n >= 30 and pct <= -30 else w


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    write = "--write" in sys.argv
    cfg = json.loads((ROOT / f"risk_config_{mode}.json").read_text())
    locks = (cfg.get("locked_presets") or {}).get(mode, {}) or {}
    weights = cfg.get("symbol_weights") or {}
    presets_all, r1 = load(mode)
    syms = sorted({s for s, _ in presets_all})
    per_sym = {s: {p: ser for (ss, p), ser in presets_all.items() if ss == s} for s in syms}
    all_t = [t for ser in presets_all.values() for t in ser.t]
    first = dt.datetime.fromtimestamp(min(all_t), TZ).date() + dt.timedelta(days=14)
    last = dt.datetime.fromtimestamp(max(all_t), TZ).date()
    now = dt.datetime.now(dt.timezone.utc).timestamp()

    snaps = []
    day = first
    while day <= last:
        d0 = dt.datetime.combine(day, dt.time(), TZ).timestamp()
        symbols = {}
        for s in syms:
            lock = locks.get(s)
            p7 = top_row(per_sym[s], lock, d0 - 7 * DAY, d0)
            p14 = top_row(per_sym[s], lock, d0 - 14 * DAY, d0)
            w = float(weights.get(s, 0))
            symbols[s] = {"w": w, "lock": lock,
                          "p7": [None if p7[0] is None else round(p7[0], 2), p7[1], p7[2]],
                          "p14": [None if p14[0] is None else round(p14[0], 2), p14[1], p14[2]],
                          "policies": {"static": w, "tilt": round(tilt(w, p14), 4), "brake": round(brake(w, p14), 4)},
                          "equal": {"static": 1.0, "tilt": round(tilt(1.0, p14), 4), "brake": round(brake(1.0, p14), 4)}}
        snaps.append({"day": day.isoformat(), "mode": mode, "backfill": True, "symbols": symbols})
        day += dt.timedelta(days=1)

    # tracked tilt = the score allocation over every eligible symbol (as the dashboard)
    for snap in snaps:
        rows = snap["symbols"]
        budget = sum(v["w"] for v in rows.values())
        alloc = score_allocation(rows, len(rows), budget)
        for s, v in rows.items():
            v["policies"]["tilt"] = round(alloc[s], 4)

    if write:
        out = DATA / f"weight_shadow_{mode}_history.jsonl"
        out.write_text("".join(json.dumps(s) + "\n" for s in snaps))
        print(f"wrote {len(snaps)} reconstructed snapshots to {out.name}")

    def score(book: str, measure: str, horizon: int = 7):
        per = {"static": [], "tilt": [], "brake": []}
        changed = {"tilt": 0, "brake": 0}
        for snap in snaps:
            d0 = dt.datetime.fromisoformat(snap["day"]).replace(tzinfo=TZ).timestamp()
            d1 = d0 + horizon * DAY
            if d1 > now:
                continue
            fwd = {}
            for s, v in snap["symbols"].items():
                if measure == "r1":
                    ser = r1.get(s)
                    fwd[s] = ser.window(d0, d1)[0] if ser else 0.0
                else:
                    pr = v["p14"][2] or v["p7"][2]
                    ser = per_sym[s].get(pr) if pr else None
                    fwd[s] = ser.window(d0, d1)[0] if ser else 0.0
            if measure == "r1" and not any(r1.get(s) and r1[s].window(d0, d1)[1] for s in snap["symbols"]):
                continue            # no would-be-real data that week (before rank-1 existed)
            key = "policies" if book == "funded" else "equal"
            for pol in per:
                ws = {s: v[key][pol] for s, v in snap["symbols"].items()}
                if book == "funded":
                    ws = {s: w for s, w in ws.items() if snap["symbols"][s]["w"] > 0}
                tot = sum(ws.values())
                per[pol].append(sum(w / tot * fwd[s] for s, w in ws.items()) if tot > 0 else 0.0)
            for pol in changed:
                changed[pol] += sum(1 for s, v in snap["symbols"].items()
                                    if (book == "equal" or v["w"] > 0) and v[key][pol] != v[key]["static"])
        n = len(per["static"])
        if not n:
            return f"{book:6} {measure:3}: no complete forward windows"
        out = [f"{book:6} {measure:3} {n:3} days"]
        for pol in ("tilt", "brake"):
            diff = [a - b for a, b in zip(per[pol], per["static"])]
            se = statistics.pstdev(diff) / math.sqrt(n) if n > 1 else float("nan")
            out.append(f"{pol}: {statistics.mean(diff):+6.2f}%/wk vs static (±{se:.2f} se), "
                       f"better {sum(1 for d in diff if d > 0)}/{n}, changes/day {changed[pol] / n:.1f}")
        out.append(f"static mean {statistics.mean(per['static']):+6.2f}%/wk")
        return " | ".join(out)

    # ── Score allocation history for every N (what the panel shows for the chosen N) ──
    rank1_start = dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc).timestamp()

    def fwd_of(snap, d0, d1, measure):
        out = {}
        for s, v in snap["symbols"].items():
            if measure == "r1":
                ser = r1.get(s)
            else:
                pr = v["p14"][2] or v["p7"][2]
                ser = per_sym[s].get(pr) if pr else None
            out[s] = ser.window(d0, d1)[0] if ser else 0.0
        return out

    score_hist: dict[str, dict] = {}
    for n in range(1, len(syms) + 1):
        entry = {}
        for measure in ("top", "r1"):
            val, eq, cur = [], [], []
            for snap in snaps:
                d0 = dt.datetime.fromisoformat(snap["day"]).replace(tzinfo=TZ).timestamp()
                d1 = d0 + 7 * DAY
                if d1 > now or (measure == "r1" and d0 < rank1_start):
                    continue
                rows = snap["symbols"]
                F = fwd_of(snap, d0, d1, measure)
                budget = sum(v["w"] for v in rows.values())
                books = {
                    "score": score_allocation(rows, n, budget),
                    "equal": {s: 1.0 for s in rows},
                    "current": {s: v["w"] for s, v in rows.items() if v["w"] > 0},
                }
                res = {}
                for k, w in books.items():
                    t = sum(w.values())
                    res[k] = sum(x / t * F[s] for s, x in w.items()) if t else 0.0
                val.append(res["score"]); eq.append(res["equal"]); cur.append(res["current"])
            if val:
                entry[measure] = {
                    "days": len(val),
                    "mean": round(statistics.mean(val), 2),
                    "vs_equal": round(statistics.mean(a - b for a, b in zip(val, eq)), 2),
                    "vs_current": round(statistics.mean(a - b for a, b in zip(val, cur)), 2),
                    "better_equal": sum(1 for a, b in zip(val, eq) if a > b),
                }
        score_hist[str(n)] = entry
    if write:
        out2 = DATA / f"weight_shadow_{mode}_score_history.json"
        out2.write_text(json.dumps(score_hist, indent=1))
        print(f"wrote score-allocation history for N=1..{len(syms)} to {out2.name}")
    print("score allocation, top measure (N: vs equal %/wk, better days):",
          ", ".join(f"{n}: {v['top']['vs_equal']:+.2f} ({v['top']['better_equal']}/{v['top']['days']})"
                    for n, v in score_hist.items() if "top" in v))

    print(f"mode={mode} reconstructed days {first}..{last} ({len(snaps)}), symbols {len(syms)}, "
          f"funded now: {', '.join(f'{s}={w}' for s, w in weights.items() if float(w) > 0)}")
    for book in ("funded", "equal"):
        for measure in ("r1", "top"):
            print("  " + score(book, measure))
    return 0


if __name__ == "__main__":
    sys.exit(main())
