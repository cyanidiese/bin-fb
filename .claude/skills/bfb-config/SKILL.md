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
| `locked_presets` | dict[sym→preset] | Forces specific preset per symbol | ✓ |
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
