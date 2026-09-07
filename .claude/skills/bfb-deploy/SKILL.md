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

### Step 3 — Graceful stop (SIGTERM)

Both bot containers run `main.py` and answer to the same pattern (verified 2026-09-07).
Run it for **each** container you are about to recreate — swap `bot` for `bot_mirror`:

```bash
ssh ... "docker exec bot /bin/sh -c 'for PID in \$(grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"); do kill -TERM \$PID 2>/dev/null && echo \"SIGTERM → \$PID\"; done' 2>/dev/null || echo not_running"
ssh ... "docker exec bot_mirror /bin/sh -c 'for PID in \$(grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"); do kill -TERM \$PID 2>/dev/null && echo \"SIGTERM → \$PID\"; done' 2>/dev/null || echo not_running"
```

Skipping the mirror does not corrupt anything, but every hard restart force-closes its open
virtual positions as `closed_early`, which pollutes preset statistics.

### Step 4 — Wait for exit then stop container

Use `for` loop — `sleep N && command` chains are **blocked** by the tool sandbox:

```bash
ssh ... "for i in \$(seq 1 20); do PIDS=\$(docker exec bot /bin/sh -c 'grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"' 2>/dev/null); [ -z \"\$PIDS\" ] && echo \"Exited after \${i}s\" && break; sleep 1; done; docker stop bot 2>/dev/null || true"
```

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
