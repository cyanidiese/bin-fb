#!/usr/bin/env python3
"""Create risk_config_test.json / risk_config_live.json from the legacy risk_config.json.

Spec: docs/specs/2026-09-26-per-mode-risk-config.md

Run on the HOST, in the repo root, BEFORE rebuilding containers:

    python3 scripts/split_risk_config.py            # dry run: shows what it would write
    python3 scripts/split_risk_config.py --apply

Why before: docker-compose bind-mounts each file individually. If a file is missing when
the container starts, Docker creates a DIRECTORY of that name instead.

Rules (never overwrites an existing mode file):
  - risk_config_test.json missing → copy of risk_config.json, locked_presets reduced to
    {"test": <test locks>}.
  - risk_config_live.json missing → copy of the test file, locked_presets set to
    {"live": <live locks from the legacy file>}.
The legacy risk_config.json is not modified — it stays as the rollback path.
Stdlib only, so it runs with the host's python3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "risk_config.json"


def locks_for(cfg: dict, mode: str) -> dict:
    """Same rule as config.risk_config.locked_presets_for: nested per mode, or a legacy
    flat dict meaning the test set."""
    raw = cfg.get("locked_presets")
    if not isinstance(raw, dict):
        return {}
    if "test" in raw or "live" in raw:
        got = raw.get(mode)
        return dict(got) if isinstance(got, dict) else {}
    return dict(raw) if mode == "test" else {}


def main() -> int:
    apply = "--apply" in sys.argv
    if not LEGACY.exists():
        print(f"no {LEGACY.name} — nothing to split")
        return 1
    legacy = json.loads(LEGACY.read_text())
    plan: list[tuple[Path, dict]] = []

    test_path = ROOT / "risk_config_test.json"
    if test_path.exists():
        test_cfg = json.loads(test_path.read_text())
        print(f"{test_path.name}: exists, left as is")
    else:
        test_cfg = {**legacy, "locked_presets": {"test": locks_for(legacy, "test")}}
        plan.append((test_path, test_cfg))

    live_path = ROOT / "risk_config_live.json"
    if live_path.exists():
        print(f"{live_path.name}: exists, left as is")
    else:
        live_cfg = {**test_cfg, "locked_presets": {"live": locks_for(legacy, "live")}}
        plan.append((live_path, live_cfg))

    for path, cfg in plan:
        locks = next(iter(cfg["locked_presets"].values()))
        print(f"{path.name}: {'WRITING' if apply else 'would write'} {len(cfg)} keys, "
              f"{len(locks)} locked preset(s), symbol_weights={cfg.get('symbol_weights')}")
        if apply:
            path.write_text(json.dumps(cfg, indent=2))
    if plan and not apply:
        print("dry run — rerun with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
