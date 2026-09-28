"""A point-in-time snapshot of the bot's settings and results, to compare later.

Read-only: it only reads state files and writes one JSON into snapshots/. Run it on the
host (stdlib only), before and after any change whose impact you want to measure:

    python3 scripts/snapshot_state.py                     # write snapshots/<UTC>.json
    python3 scripts/snapshot_state.py --label "tilt N=5"  # with a note
    python3 scripts/snapshot_state.py --compare A.json B.json   # what changed, A -> B

What it keeps, per mode (test / live):
  settings  — risk config (secrets removed), weights, locks, disabled/paused symbols
  balance   — now and 1/7/30 days ago (primary account; test mode only has real orders)
  real      — closed real orders per window: count, win rate, PnL, per symbol, per exit,
              and sub-minute closes (the pre-entry-candle bug signature, fixed 2026-09-28)
  virtual   — rank-1 virtual orders per window and symbol (what the bot would trade)
  presets   — the 7d/14d Profit% leader per symbol and how many presets are profitable
  decisions — decision-log outcome counts over the last 7 days (why signals were skipped)
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "snapshots"
MODES = ("test", "live")
WINDOWS = {"1d": 1, "7d": 7, "30d": 30, "all": None}
SECRET = re.compile(r"token|secret|password|api_key|chat_id", re.I)
NOW = dt.datetime.now(dt.timezone.utc)


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def scrub(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if SECRET.search(k) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def when(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def since(days):
    return None if days is None else NOW - dt.timedelta(days=days)


def stats(orders: list[dict]) -> dict:
    pnl = [float(o.get("pnl_usdt") or 0) for o in orders]
    wins = sum(1 for p in pnl if p > 0)
    return {"n": len(pnl), "wins": wins,
            "win_rate": round(100 * wins / len(pnl), 1) if pnl else None,
            "pnl": round(sum(pnl), 2), "avg": round(sum(pnl) / len(pnl), 3) if pnl else None}


def windowed(orders: list[dict], per_symbol: bool) -> dict:
    out = {}
    for name, days in WINDOWS.items():
        cut = since(days)
        sel = [o for o in orders if cut is None or (o["_t"] and o["_t"] >= cut)]
        row = stats(sel)
        if per_symbol and name in ("7d", "30d"):
            by = defaultdict(list)
            for o in sel:
                by[o["_sym"]].append(o)
            row["by_symbol"] = {s: stats(v) for s, v in sorted(by.items())}
        out[name] = row
    return out


def closed_orders(pattern: str, sym_re: str) -> list[dict]:
    rows = []
    for f in glob.glob(str(DATA / pattern)):
        if "archive" in f:
            continue
        sym = re.search(sym_re, Path(f).name).group(1)
        for o in read(Path(f), []) or []:
            if not isinstance(o, dict) or o.get("close_time") is None or o.get("status") == "open":
                continue
            o["_sym"], o["_t"] = sym, when(o.get("close_time"))
            rows.append(o)
    return rows


def real_section(mode: str) -> dict:
    orders = closed_orders(f"real_orders_*_{mode}.json", rf"real_orders_(.+)_{mode}\.json")
    out = windowed(orders, per_symbol=True)
    for name in ("7d", "30d"):
        cut = since(WINDOWS[name])
        sel = [o for o in orders if o["_t"] and o["_t"] >= cut]
        out[name]["by_result"] = dict(Counter(o.get("result") for o in sel))
        quick = [o for o in sel if (when(o.get("open_time")) and
                 (o["_t"] - when(o.get("open_time"))).total_seconds() < 60)]
        out[name]["sub_minute"] = stats(quick)
    return out


def virtual_section(mode: str) -> dict:
    orders = closed_orders(f"virtual_orders_rank1_*_{mode}.json", rf"virtual_orders_rank1_(.+)_{mode}\.json")
    return windowed(orders, per_symbol=True)


def balance_section(mode: str) -> dict:
    hist = read(DATA / f"balance_history_{mode}.json", []) or []
    pts = [(when(h.get("timestamp")), h.get("balance")) for h in hist if isinstance(h, dict)]
    pts = [(t, b) for t, b in pts if t and b is not None]
    if not pts:
        return {}
    out = {"now": round(pts[-1][1], 2), "at": pts[-1][0].isoformat()}
    for name, days in (("1d", 1), ("7d", 7), ("30d", 30)):
        cut = since(days)
        before = [b for t, b in pts if t <= cut]
        if before:
            out[f"{name}_ago"] = round(before[-1], 2)
            out[f"{name}_change"] = round(pts[-1][1] - before[-1], 2)
    return out


def preset_section(mode: str) -> dict:
    store = read(DATA / f"preset_profit_{mode}.json", {}) or {}
    out = {}
    for sym, s in (store.get("symbols") or {}).items():
        row = {}
        for rng in ("7d", "14d"):
            presets = ((s.get("ranges") or {}).get(rng) or {}).get("presets") or {}
            vals = [(p, v[0], v[1]) for p, v in presets.items() if isinstance(v, list) and v and v[0] is not None]
            if not vals:
                continue
            best = max(vals, key=lambda x: x[1])
            row[rng] = {"top": best[0], "profit_pct": round(best[1], 2), "trades": best[2],
                        "profitable": sum(1 for v in vals if v[1] > 0), "total": len(vals)}
        out[sym] = row
    return out


def decision_section(mode: str) -> dict:
    rows = read(DATA / f"decision_log_{mode}.json", []) or []
    cut = since(7)
    sel = [r for r in rows if isinstance(r, dict) and (when(r.get("timestamp")) or NOW) >= cut]
    return {"window_days": 7, "rows": len(sel),
            "oldest_in_log": rows[0].get("timestamp") if rows else None,
            "by_decision": dict(Counter(r.get("decision") for r in sel).most_common())}


def settings_section(mode: str) -> dict:
    cfg = scrub(read(ROOT / f"risk_config_{mode}.json", {}) or {})
    shared = scrub(read(ROOT / "risk_config_shared.json", {}) or {})
    reg = read(ROOT / f"symbol_registry_{mode}.json", {}) or {}
    return {
        "symbol_weights": {k: v for k, v in (cfg.get("symbol_weights") or {}).items() if v},
        "weight_budget": cfg.get("weight_budget"),
        "locked_presets": (cfg.get("locked_presets") or {}).get(mode, cfg.get("locked_presets")),
        "disabled": sorted((reg.get("disabled") or {}).keys()),
        "paused": sorted((reg.get("paused") or {}).keys()),
        "risk_config": cfg,
        "risk_config_shared": shared,
    }


def open_positions(mode: str) -> dict:
    o = read(DATA / f"open_positions_{mode}.json", {}) or {}
    return {k: len(v) for k, v in o.items() if isinstance(v, list)}


def git_head() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %cI %s"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def snapshot(label: str | None) -> dict:
    return {
        "taken_at": NOW.isoformat(timespec="seconds"),
        "label": label,
        "git": git_head(),
        "primary": read(DATA / "primary_mode.json"),
        "symbols": (read(ROOT / "symbol_registry_shared.json", {}) or {}).get("symbols"),
        "modes": {m: {
            "settings": settings_section(m),
            "open_positions": open_positions(m),
            "balance": balance_section(m),
            "real": real_section(m),
            "virtual_rank1": virtual_section(m),
            "presets": preset_section(m),
            "decisions": decision_section(m),
        } for m in MODES},
    }


# ── compare ──────────────────────────────────────────────────────────────────

def flat(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = d
    return out


def compare(a_path: str, b_path: str) -> None:
    a, b = read(Path(a_path)), read(Path(b_path))
    print(f"A {a['taken_at']} {a.get('label') or ''} [{a.get('git')}]")
    print(f"B {b['taken_at']} {b.get('label') or ''} [{b.get('git')}]\n")
    for m in MODES:
        ma, mb = a["modes"][m], b["modes"][m]
        print(f"=== {m} ===")
        fa, fb = flat(ma["settings"]), flat(mb["settings"])
        changed = [k for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]
        print(f"settings changed ({len(changed)}):")
        for k in changed[:60]:
            print(f"  {k}: {fa.get(k)!r} -> {fb.get(k)!r}")
        print(f"balance: {ma['balance'].get('now')} -> {mb['balance'].get('now')}")
        for sec in ("real", "virtual_rank1"):
            for w in ("7d", "30d"):
                x, y = ma[sec].get(w, {}), mb[sec].get(w, {})
                print(f"{sec} {w}: n {x.get('n')}->{y.get('n')}  win% {x.get('win_rate')}->{y.get('win_rate')}"
                      f"  pnl {x.get('pnl')}->{y.get('pnl')}")
        da, db = ma["decisions"]["by_decision"], mb["decisions"]["by_decision"]
        print("decisions 7d: " + ", ".join(f"{k} {da.get(k, 0)}->{db.get(k, 0)}"
                                            for k in sorted(set(da) | set(db), key=lambda k: -db.get(k, 0))))
        print()


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--compare"] and len(args) == 3:
        compare(args[1], args[2])
        return 0
    label = args[args.index("--label") + 1] if "--label" in args else None
    snap = snapshot(label)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{NOW.strftime('%Y-%m-%dT%H%MZ')}.json"
    path.write_text(json.dumps(snap, indent=1, default=str))
    print(path)
    for m in MODES:
        s = snap["modes"][m]
        r7, r30, v7 = s["real"]["7d"], s["real"]["30d"], s["virtual_rank1"]["7d"]
        print(f"{m}: balance {s['balance'].get('now')} | real 7d n={r7['n']} win%={r7['win_rate']} pnl={r7['pnl']}"
              f" | real 30d n={r30['n']} pnl={r30['pnl']} | rank-1 virtual 7d n={v7['n']} pnl={v7['pnl']}"
              f" | weights {s['settings']['symbol_weights']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
