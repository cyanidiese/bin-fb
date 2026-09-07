# Quick Reference

**Load this doc when:** you need server paths, deploy commands, thresholds, or the list of unprotected presets.

---

## Server

```
Host:       185.237.14.105
User:       root
Key:        ~/.ssh/id_ed25519
Container:  bot  (docker compose)
```

```bash
# Standard SSH
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105

# Pull logs locally (always prefer this over remote grep)
scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/logs/bot.log /tmp/bot_latest.log

# Check bot status
ssh -i ~/.ssh/id_ed25519 root@185.237.14.105 "docker ps | grep bot"
```

---

## Key File Paths

| File | Location | Reload method |
|---|---|---|
| Bot log | `/opt/bot/logs/bot.log` | — |
| Risk config (live) | `/opt/bot/risk_config.json` | Hot-reload, 5s TTL |
| Streak state | `/opt/bot/data/streak_state_test.json` | On restart |
| Main bot logic | `main.py` | Deploy |
| Preset definitions | `config/presets.py` | Deploy |
| Virtual tracker | `bot/virtual_tracker.py` | Deploy |
| Order executor | `bot/order_executor.py` | Deploy |

**Note:** `risk_config.json` is gitignored — git pull will NOT update it on the server. Always SCP it separately after a config change.

---

## Hot-Reload vs Deploy

| What to change | Method |
|---|---|
| Symbol weights | Hot-reload |
| `preset_blocklist` | Hot-reload |
| `global_min_sl_pct` | Hot-reload |
| `min_profit_factor` | Hot-reload |
| `locked_presets` | Hot-reload |
| `per_symbol_settings` | Hot-reload |
| Preset `loss_streak_max`, `dup_skip` etc. | Deploy |
| Streak logic, gate checks in `main.py` | Deploy |
| Order sizing / execution | Deploy |
| Virtual tracker scoring | Deploy |

---

## Deploy Procedure

Use the **`/bfb-deploy`** skill. It has the current procedure (feature branch, graceful SIGTERM, Docker rebuild). Do not use `docker compose stop` directly — it skips graceful order cleanup.

---

## Performance Benchmarks for Symbol Decisions

- Net PnL < −$20 with ≥10 real trades → weight → 0
- Win rate < 25% with ≥8 real trades → weight ≤ 5
- Win rate < 15% with ≥8 real trades → weight → 0
- Net PnL > +$15 with ≥8 real trades → candidate for weight increase (verify preset stability first)
- Any symbol with weight > 15 that is net negative → immediate review required

For current symbol status and active config, use the **`/bfb-status`** skill.
