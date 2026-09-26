#!/usr/bin/env python3
"""Create risk_config_shared.json and the per-mode symbol registry files.

Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md

Run on the HOST, in the repo root, AFTER the bots stop (the primary writes the registry)
and BEFORE rebuilding containers:

    python3 scripts/split_shared_and_registry.py            # dry run
    python3 scripts/split_shared_and_registry.py --apply

Why before rebuilding: docker-compose bind-mounts each file individually; a file missing
when a container starts becomes a DIRECTORY of that name.

Creates (never overwrites an existing file):
  risk_config_shared.json      ← SHARED_KEYS present in risk_config_test.json (the file the
                                 real-order bot trades on); reports keys whose live value
                                 differs, since live will follow the shared value.
  symbol_registry_shared.json  ← symbols, status from symbol_registry.json
  symbol_registry_test.json    ← disabled, paused, disabled_ranks, weights,
                                 leverage_overrides from symbol_registry.json
  symbol_registry_live.json    ← copy of the test state (the user's rule: missing live
                                 config comes from test)
  dashboard/public/backtest_results_{SYM}_test.json
                               ← backtest_results_{SYM}.json, the testnet primary's
                                 (results are keyed by mode since spec
                                 2026-09-26-mode-switch-restart-and-per-mode-backtests)
The legacy files are not modified — they stay as the rollback path.
Stdlib only; SHARED_KEYS is parsed out of config/risk_config.py so the list has one home.
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_KEYS = ("weights", "disabled", "disabled_ranks", "paused", "leverage_overrides")


def shared_keys() -> tuple[str, ...]:
    """SHARED_KEYS from config/risk_config.py without importing it (host has no venv)."""
    tree = ast.parse((ROOT / "config" / "risk_config.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SHARED_KEYS" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise SystemExit("SHARED_KEYS not found in config/risk_config.py")


def load(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"{path.name} is not a JSON object")
    return data


def main() -> int:
    apply = "--apply" in sys.argv
    plan: list[tuple[Path, dict, str]] = []

    # ── shared risk settings ────────────────────────────────────────────
    shared_path = ROOT / "risk_config_shared.json"
    test_cfg_path = ROOT / "risk_config_test.json"
    if shared_path.exists():
        print(f"{shared_path.name}: exists, left as is")
    elif not test_cfg_path.exists():
        print(f"{test_cfg_path.name} missing — run scripts/split_risk_config.py first")
        return 1
    else:
        keys = shared_keys()
        test_cfg = load(test_cfg_path)
        shared = {k: test_cfg[k] for k in keys if k in test_cfg}
        live_path = ROOT / "risk_config_live.json"
        if live_path.exists():
            live_cfg = load(live_path)
            differ = [k for k in shared if k in live_cfg and live_cfg[k] != shared[k]]
            if differ:
                print(f"NOTE: live differs from test on shared keys {differ} — "
                      f"live will follow the shared (test) value")
        plan.append((shared_path, shared, f"{len(shared)} shared keys"))

    # ── symbol registry ─────────────────────────────────────────────────
    legacy_path = ROOT / "symbol_registry.json"
    roster_path = ROOT / "symbol_registry_shared.json"
    test_state_path = ROOT / "symbol_registry_test.json"
    live_state_path = ROOT / "symbol_registry_live.json"
    need_legacy = not (roster_path.exists() and test_state_path.exists())
    legacy = load(legacy_path) if legacy_path.exists() else None
    if need_legacy and legacy is None:
        print(f"no {legacy_path.name} — cannot seed the registry")
        return 1

    if roster_path.exists():
        print(f"{roster_path.name}: exists, left as is")
    else:
        roster = {"symbols": legacy.get("symbols", []), "status": legacy.get("status", {}),
                  "updated_at": legacy.get("updated_at", "")}
        plan.append((roster_path, roster, f"{len(roster['symbols'])} symbols"))

    if test_state_path.exists():
        test_state = load(test_state_path)
        print(f"{test_state_path.name}: exists, left as is")
    else:
        test_state = {"mode": "test", **{k: legacy.get(k, {}) for k in STATE_KEYS}}
        plan.append((test_state_path, test_state,
                     f"disabled={sorted(test_state['disabled'])} paused={sorted(test_state['paused'])}"))

    if live_state_path.exists():
        print(f"{live_state_path.name}: exists, left as is")
    else:
        live_state = {**test_state, "mode": "live"}
        plan.append((live_state_path, live_state,
                     f"disabled={sorted(live_state.get('disabled', {}))} (copy of test)"))

    # ── backtest results by mode ────────────────────────────────────────
    public = ROOT / "dashboard" / "public"
    copies: list[tuple[Path, Path]] = []
    if public.is_dir():
        for legacy_bt in sorted(public.glob("backtest_results_*.json")):
            stem = legacy_bt.stem[len("backtest_results_"):]
            if not stem or stem.endswith(("_test", "_live")):
                continue
            target = public / f"backtest_results_{stem}_test.json"
            if not target.exists():
                copies.append((legacy_bt, target))
    if copies:
        print(f"backtest results: {'COPYING' if apply else 'would copy'} {len(copies)} "
              f"file(s) to *_test.json (e.g. {copies[0][1].name})")

    for path, data, summary in plan:
        print(f"{path.name}: {'WRITING' if apply else 'would write'} — {summary}")
        if apply:
            path.write_text(json.dumps(data, indent=2))
    if apply:
        for src, dst in copies:
            # copy2 keeps the mtime: main.py reports backtest age from it, and a fresh
            # timestamp made 13-19-day-old results look brand new (first deploy, 2026-09-26).
            shutil.copy2(src, dst)
    plan = plan or copies
    if plan and not apply:
        print("dry run — rerun with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
