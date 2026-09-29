---
name: bfb-deploy
description: >
  Deploy code to the Binance Futures bot server. Trigger on: "deploy", "push to server",
  "rebuild the bot", "update the bot", "release", "ship the changes", or after any code
  change is approved by the user for production. Always ask for explicit confirmation
  before running deployment — never deploy automatically.
---

# BFB — Deploy to Server

**Always ask the user for explicit approval before running any of these steps.**
Deployment interrupts the live bot.

SSH alias: `ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105`

---

## Branch situation (update when merged)

- Server and local are both on: **`feature/mean-reversion-overlay`** (verified 2026-09-07)
- `scripts/push.sh` deploys **`main`** — do NOT use it while on the feature branch
- When merged to main: use `bash scripts/push.sh` normally

## Services (three, not one)

| Service | Image | Restarting it… |
|---|---|---|
| `bot` | `bot-bot` | interrupts real trading — graceful stop required |
| `bot_mirror` | `bot-bot` | virtual-only, but force-closes its open virtual positions |
| `dashboard` | `bot-dashboard` | harmless, no bot impact |

Python source is baked into `bot-bot`; the dashboard is baked into `bot-dashboard`.
`git pull` alone updates **neither** running container.

---

## Three images, not two

`bot`, `bot_mirror` and `dashboard` each have their own `build:` in docker-compose, so
compose builds **three** images: `bot-bot`, `bot-bot_mirror`, `bot-dashboard`. Building
`bot` does NOT update the mirror — measured 2026-09-28: after `docker compose build bot
dashboard` + `up -d --no-deps bot_mirror`, the mirror came back on the previous day's
image. Always name every service you recreate in the build:

```bash
docker compose build bot_mirror dashboard            # mirror + dashboard only
docker stop -t 60 bot_mirror
docker compose up -d --no-deps bot_mirror dashboard  # the trading bot keeps running
docker inspect -f '{{.Name}} {{.Image}} {{.State.StartedAt}}' bot bot_mirror
```

`--no-deps` plus naming services is how to ship bot-side changes that only matter to the
mirror (virtual only) without restarting the trading bot and its real positions.

## Dashboard-only deploy (does NOT touch the bot)

When a commit changes only `dashboard/`, skip the stop/start dance entirely.

```bash
git push origin feature/mean-reversion-overlay
ssh ... "cd /opt/bot && git pull origin feature/mean-reversion-overlay"
ssh ... "cd /opt/bot && docker compose up -d --build dashboard"
```

Naming the service is what protects the bot: only `bot-dashboard` is rebuilt and only the
`dashboard` container is recreated. Prove it — the start times must be **unchanged**:

```bash
ssh ... "docker inspect -f '{{.Name}} {{.State.StartedAt}}' bot bot_mirror"
```

Verify the deploy landed (`/trades` 307s to `/login` — that is the auth gate, not a fault):

```bash
ssh ... "curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/login"
ssh ... "docker exec dashboard grep -c '<a string from your change>' /app/dashboard/<path>"
```

---

## Full deploy procedure (bot code changed)

### Step 1 — Check for open positions FIRST

Before stopping anything, in its own command — not chained to `docker stop`.
Each instance keeps its own file, named by **mode**, so check the one for the mode that
instance is running (`bot` = the mode in `data/bot_mode.json`, `bot_mirror` = the opposite):

```bash
ssh ... "docker exec bot sh -c 'cat /app/data/open_positions_test.json /app/data/open_positions_live.json 2>/dev/null' || echo none"
```

Only the primary's positions are real money. The mirror's are virtual — worth a graceful
stop so they are not force-closed, but never a reason to postpone a deploy.

### Step 2 — Push commit

```bash
git push origin feature/mean-reversion-overlay
```

### Step 3 — Graceful stop: `docker stop -t 60` ONLY

```bash
ssh ... "docker stop -t 60 bot bot_mirror"
```

It sends SIGTERM to PID 1 (`main.py`), waits for the graceful handler, and does not trigger
the `restart: unless-stopped` policy. With `close_positions_on_stop=false`, open real
positions are saved to `data/restart_positions_{mode}.json` and restored on start (their
exchange SL stays live); since 2026-09-29 virtual positions are saved too
(`data/virtual_open_state_{mode}.json`) and resume instead of closing as `closed_early`.
Build the new images BEFORE stopping (`docker compose build bot bot_mirror`) so downtime is
only the restart. Stop right AFTER a candle has been processed (e.g. :46, :01), never across
:00/:15/:30/:45.

**NEVER kill `main.py` from inside the container** (`docker exec … kill -TERM`). The process
exits, Docker's restart policy immediately starts the OLD image again, that start restores
and deletes the restart file, and the later `docker stop` gives it no chance to save again.
The new container then finds the positions on the exchange with no record and closes them
at market as orphans. Happened 2026-09-26 11:19 and again 2026-09-29 14:52: APTUSDT −3.16 and
ENAUSDT +8.87 USDT closed by the deploy, re-entered at the next candle at worse prices.

### Step 4 — Start on the new images

```bash
ssh ... "cd /opt/bot && docker compose up -d --no-deps bot bot_mirror"
```

Then confirm in the log: `Startup: N position(s) restored from restart state` (if any were
open), `Virtual restore: …`, and no `Orphan position closed on startup`.

### Step 5 — Inspect the working tree, then pull

**Never blind-reset.** Look first:

```bash
ssh ... "cd /opt/bot && git status --porcelain | head -30"
```

The server legitimately carries uncommitted state:

- `dashboard/public/results_*.json`, `risk_state.json` — tracked, rewritten by the bot
- `dashboard/public/results_*_live.json` — **untracked, the mirror's results**
- `symbol_registry.json` — tracked, holds live weights edited through the dashboard

Then pull plainly. It fast-forwards fine as long as the incoming commit does not touch a
locally-modified file:

```bash
ssh ... "cd /opt/bot && git pull origin feature/mean-reversion-overlay"
```

**Only if the pull refuses**, restore the specific tracked files under `dashboard/public/`
that block it — narrowest possible scope:

```bash
ssh ... "cd /opt/bot && git checkout -- dashboard/public/results_SOME_SYMBOL.json"
```

If the blocker is `symbol_registry.json`, **stop and ask the user**. The server's copy is
live trading state; overwriting it with the repo's copy can silently change symbol weights.

### Step 6 — Rebuild

```bash
ssh ... "cd /opt/bot && docker compose up -d --build 2>&1 | tail -20"
```

### Step 7 — Confirm startup

```bash
ssh ... "until grep -q 'Combined stream connected' /opt/bot/logs/bot.log; do sleep 2; done && grep -v 'Kline cache\|Cache has' /opt/bot/logs/bot.log | tail -8"
```

---

## After deploy checklist

- [ ] Container shows `Up` in `docker ps`
- [ ] Log shows `Combined stream connected (15 symbols)`
- [ ] No `ERROR` or `Traceback` after the startup line
  - `SystemExit: 0` from `on_stop_bot` is **normal** (graceful shutdown artifact) — ignore it
- [ ] Mirror's `results_*_live.json` files still present (`ls dashboard/public/*_live.json | wc -l`)
- [ ] If risk_config needs new keys: apply via `/bfb-config` skill (hot-reload, no restart needed)

---

## Deploy wipes the symbol roster — restore it afterwards

**Obsolete once `symbol_registry_shared.json` exists on the server** (spec
`2026-09-26-shared-settings-and-per-mode-registry.md`). From then on the roster and the
per-mode decisions live in gitignored files (`symbol_registry_shared.json`,
`symbol_registry_{test,live}.json`, `risk_config_shared.json`); the tracked
`symbol_registry.json` is a frozen rollback copy nothing reads, so a reset of it is
harmless. The first deploy of that change must run
`python3 scripts/split_shared_and_registry.py --apply` on the host **after the bots stop
and before the rebuild** (single-file bind mounts: a missing file becomes a directory). The
same script copies `dashboard/public/backtest_results_{SYM}.json` → `_test.json` (backtest
results are keyed by mode since spec 2026-09-26-mode-switch-restart-and-per-mode-backtests).
Until then, the procedure below still applies.

**Mode switches restart the bots by design** (same spec): after the Trading Mode button,
the primary closes every position at market and exits 0; `restart: unless-stopped` brings
it back in the new mode and the mirror follows `data/primary_mode.json`. A restart of `bot`
right after someone pressed that button is expected, not a crash.

`symbol_registry.json` is **tracked by git**, so step 4's `git reset --hard HEAD` silently
reverts it to the committed roster. Any symbol added or removed since that commit is lost.

Measured 2026-09-13: the roster had been grown to 22 symbols; after the deploy the log said
`Combined stream connected (15 symbols)` and the 7 newly-added symbols stopped collecting.

**Before deploying:** record the live roster.

```bash
ssh ... "python3 -c \"import json;print(json.load(open('/opt/bot/symbol_registry.json'))['symbols'])\""
```

**After deploying:** compare and re-add anything missing, then confirm the stream count.

```bash
ssh ... "docker exec bot sh -c 'grep \"Combined stream connected\" /app/logs/bot.log | tail -1'"
```

The count in that line is the authoritative check — it must match the roster length. Symbols
re-added this way are picked up by the hot-subscribe path at the next candle close, no
second restart needed.

Kline caches (`data/*_15m_*.json`) are gitignored and survive the reset, so a backfilled
symbol keeps its history.

---

## Pitfalls

| Situation | Wrong | Right |
|---|---|---|
| Server working tree is dirty | `git reset --hard HEAD && git clean -f dashboard/public/` | `git status` first, then a plain `git pull`; restore individual files only if it refuses |
| Untracked `*_live.json` present | `git clean -f dashboard/public/` — **deletes the mirror's results** | Leave untracked files alone; they are the mirror's only copy |
| `symbol_registry.json` modified on server | Reset it to the repo version | Preserve it — it is live weight state; ask the user if a commit touches it |
| Dashboard-only change | Full stop → rebuild all → restart bot | `docker compose up -d --build dashboard`, bots keep running |
| Compound destructive SSH command | `reset --hard && clean && pull && build` in one line | Split into steps — the auto-mode classifier blocks the chained form, and one command cannot be reviewed |
| Checking open positions | Same command as `docker stop` | Separate command, **before** stopping |
| Feature branch | `bash scripts/push.sh` | Manual procedure above |
| Wait for bot exit | `sleep 25 && ssh ...` (BLOCKED) | `for i in $(seq 1 20); do ... sleep 1; done` |
| Check bot after restart | Check `/opt/bot/logs/bot.log` mtime | `docker exec bot tail /app/logs/bot.log` — host file may lag |
| risk_config after deploy | Commit it / include in image | SSH update via `/bfb-config` (it's gitignored) |
