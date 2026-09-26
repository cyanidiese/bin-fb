---
name: bfb-config
description: >
  Update risk_config_test.json / risk_config_live.json on the Binance Futures bot server. Trigger on: "update
  risk config", "change config", "block a preset", "add to blocklist", "set global
  min rr", "disable symbol", "change weight", "update risk_config", "hot-reload",
  any request to change a runtime parameter without a full deploy.
---

# BFB — Update the per-mode risk config

Since 2026-09-26 each trading mode has its own file (spec
`docs/specs/2026-09-26-per-mode-risk-config.md`):

| File | Read by (bot_mode = test) |
|---|---|
| `/opt/bot/risk_config_test.json` | `bot` — the primary, **real orders** |
| `/opt/bot/risk_config_live.json` | `bot_mirror` — virtual only; keys it lacks fall back to the test file |
| `/opt/bot/risk_config.json` | **nobody** — legacy, kept only as the rollback path. Editing it does nothing. |

Decide which mode the change is for. For a change both instances should see, write it to
both files. Bot-wide keys (`telegram`, `*_interval_s`, `startup_backtest`,
`backtest_klines`) must always be written to both.

The files are **gitignored** — never committed to the repo.
Changes take effect on the **next candle close** (hot-reload, no restart needed).

SSH alias: `ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105`

---

## Update pattern — always use heredoc

```bash
ssh ... "python3 << 'PYEOF'
import json
for mode in ['test']:            # ['test', 'live'] for both instances
    path = f'/opt/bot/risk_config_{mode}.json'
    with open(path, 'r') as f:
        cfg = json.load(f)

    # Make changes:
    cfg['some_key'] = value

    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)
    print(mode, 'updated:', json.dumps({k: cfg[k] for k in ['some_key']}, indent=2))
PYEOF"
```

**Never use `python3 -c "...f'{var}'..."` over SSH** — single/double quote escaping and
f-string curly braces break silently. The `<< 'PYEOF'` heredoc is always safe.

---

## Common operations

### Block a preset
```python
cfg['preset_blocklist'].append('preset_name')
```

### Disable a symbol (set weight to 0)
```python
cfg['symbol_weights']['SYMBOLUSDT'] = 0
```

### Lock a preset to a symbol
```python
cfg['locked_presets'].setdefault(mode, {})['SYMBOLUSDT'] = 'preset_name'   # nested by mode
```

### Update a global filter
```python
cfg['global_min_rr'] = 3.0
cfg['entry_zone_max_pct'] = 0.75
cfg['trading_blackout_hours'] = [17, 18, 19]
```

### Add per-symbol settings override
```python
cfg.setdefault('per_symbol_settings', {})['SYMBOLUSDT'] = {'min_sl_pct': 1.0}
```

---

## Key config fields reference

| Key | Type | Effect | Hot-reload |
|---|---|---|---|
| `preset_blocklist` | list[str] | Blocks presets from getting real orders | ✓ |
| `locked_presets` | **dict[mode→dict[sym→preset]]** | Forces specific preset per symbol. **MODE-SCOPED** — see below | ✓ |
| `symbol_weights` | dict[sym→int] | Allocation weight (0 = virtual only) | ✓ |
| `global_min_rr` | float | Minimum R:R for any real order | ✓ |
| `global_max_rr` | float | Maximum R:R (clips TP) | ✓ |
| `global_min_sl_pct` | float | SL floor as % of entry | ✓ |
| `entry_zone_max_pct` | float | Entry zone gate (0.75 = inner 75% only) | ✓ |
| `trading_blackout_hours` | list[int] | UTC hours to skip real orders | ✓ |
| `global_trend_regime_filter` | bool | Block counter-trend signals | ✓ |
| `global_blocked_signal_types` | list[str] | Block specific signal types | ✓ |
| `global_max_level` | int | Max trend level depth (0 = disabled) | ✓ |
| `global_correction_weight` | float | Override correction_bonus weight (-1 = use preset) | ✓ |

---

## After updating — verify it applied

The bot hot-reloads risk_config on every candle close (~15 min wait).
To confirm the key was read, grep the next candle's log for any filter that uses it,
or check the decision log for expected behavior.

Server paths: `/opt/bot/risk_config_test.json`, `/opt/bot/risk_config_live.json`


---

## NEVER replace the file — write in place

`risk_config_{test,live}.json` and `symbol_registry.json` are bind-mounted into the containers as
**single files**, which Docker binds *by inode*. Any operation that creates a new inode
silently detaches the running bots from your edit:

```bash
# WRONG — the containers keep reading the OLD inode; your change never lands
python3 -c "...json.dump(c, open('/opt/bot/risk_config.json.staged','w'))"
mv /opt/bot/risk_config.json.staged /opt/bot/risk_config.json
```

Measured 2026-09-09: after `mv`, the host held inode 290352 (3751 bytes) while both
containers still read inode 262665 (3515 bytes, four hours stale). The config looked
applied in every host-side check and had not reached either bot.

Use the heredoc pattern above — `open(P, 'w')` truncates the **same** inode. Also avoid
`sed -i` (creates a temp then renames), `cp new old` is fine (writes through), `mv` is not.

**Recovery if it already happened:** write the intended content in place from inside the
container, which reaches the inode both mounts hold, then confirm the host copy matches
so a later `docker compose up` stays consistent:

```bash
ssh ... "cat /opt/bot/risk_config_test.json | docker exec -i bot /app/.venv/bin/python -c \
  \"import sys,json; json.dump(json.load(sys.stdin), open('/app/risk_config_test.json','w'), indent=2)\""
```

Verify with inodes and sizes, not just content:

```bash
ssh ... "stat -c '%i %s' /opt/bot/risk_config_test.json; docker exec bot stat -c '%i %s' /app/risk_config_test.json"
```

Sizes must match. Inodes will differ after a recovery like this — that is fine as long as
the **content** is identical.

---

## What each instance sees

With bot_mode = test the mirror runs **live**, so it reads `risk_config_live.json`.

- Every key is now mode-scoped: a change to the test file does **not** reach the mirror
  unless the live file lacks that key (live falls back to test key by key).
- `locked_presets` stays nested (`{"test": {...}}` in the test file, `{"live": {...}}` in
  the live file). **When you change a test lock, decide explicitly whether live should match** —
  for a faithful virtual rehearsal it should. (Measured 2026-09-09: an empty live lock set
  left the mirror picking presets freely — the strategy that lost −751.71 lifetime.)
- `symbol_registry.json` is still **shared** by both instances (enabled/disabled/paused).
- If the primary is ever switched to live, it reads `risk_config_live.json` — review that
  file before any mode switch.
