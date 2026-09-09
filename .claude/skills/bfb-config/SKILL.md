---
name: bfb-config
description: >
  Update risk_config.json on the Binance Futures bot server. Trigger on: "update
  risk config", "change config", "block a preset", "add to blocklist", "set global
  min rr", "disable symbol", "change weight", "update risk_config", "hot-reload",
  any request to change a runtime parameter without a full deploy.
---

# BFB — Update risk_config.json

`risk_config.json` is **gitignored** — never committed to the repo.
Changes take effect on the **next candle close** (hot-reload, no restart needed).

SSH alias: `ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105`

---

## Update pattern — always use heredoc

```bash
ssh ... "python3 << 'PYEOF'
import json
with open('/opt/bot/risk_config.json', 'r') as f:
    cfg = json.load(f)

# Make changes:
cfg['some_key'] = value

with open('/opt/bot/risk_config.json', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Updated:', json.dumps({k: cfg[k] for k in ['some_key']}, indent=2))
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
cfg['locked_presets']['SYMBOLUSDT'] = 'preset_name'
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

Server path: `/opt/bot/risk_config.json`


---

## NEVER replace the file — write in place

`risk_config.json` and `symbol_registry.json` are bind-mounted into the containers as
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
ssh ... "cat /opt/bot/risk_config.json | docker exec -i bot /app/.venv/bin/python -c \
  \"import sys,json; json.dump(json.load(sys.stdin), open('/app/risk_config.json','w'), indent=2)\""
```

Verify with inodes and sizes, not just content:

```bash
ssh ... "stat -c '%i %s' /opt/bot/risk_config.json; docker exec bot stat -c '%i %s' /app/risk_config.json"
```

Sizes must match. Inodes will differ after a recovery like this — that is fine as long as
the **content** is identical.

---

## `locked_presets` is mode-scoped — and the mirror runs the OTHER mode

The shape is `{"test": {sym: preset}, "live": {sym: preset}}`, resolved by
`locked_presets_for(cfg, mode)` (`config/risk_config.py:140`). It is the **only**
mode-scoped key; everything else in the file applies to both instances, because
`bot_mirror` mounts the same `risk_config.json` and `symbol_registry.json` read-only.

That matters because the mirror derives its mode as the **opposite** of the primary. With
the primary on `test`, the mirror runs `live` — so an empty `locked_presets.live` leaves
the mirror selecting presets freely while the primary uses its locks.

Measured 2026-09-09: primary logged 2,387 `Using manually locked preset` lines, mirror
logged **0**, and every mirror rank-1 preset differed from the primary's lock (SOLUSDT
`hl_buy_trail15` vs locked `l2_trend_buy`; REZUSDT `mr_fade`/`oscillating_zone` vs locked
`sl_adjust_rr_tp95`). The shadow was rehearsing the free-selection strategy that produced
the −751.71 lifetime result rather than the locked one that produced +171.68.

**When you change `locked_presets.test`, decide explicitly whether `live` should match.**
For a virtual-only rehearsal it should. Note the consequence: if the primary is ever
switched to `TRADING_MODE=live`, it inherits whatever sits in `locked_presets.live`.

---

## What propagates to `bot_mirror`

| shared (mirror sees it immediately) | mirror-specific |
|---|---|
| `symbol_weights`, `balance_tiers`, `min_balance_pct`, `max_trade_pct`, `tats_min_weight` | `locked_presets` — mode-scoped, see above |
| `preset_blocklist`, `per_symbol_settings`, `global_*`, `trading_blackout_hours` | its mode (opposite of the primary) |
| all of `symbol_registry.json` — enabled/disabled/paused/weights | its own `*_live.json` result files |
