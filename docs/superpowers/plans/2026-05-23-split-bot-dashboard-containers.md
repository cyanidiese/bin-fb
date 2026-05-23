# Split Bot and Dashboard into Separate Docker Services — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single `app` docker-compose service into independent `bot` and `dashboard` services so dashboard changes can be deployed without restarting the bot.

**Architecture:** `docker-compose.yml` gains a second service (`bot`) with its own `container_name: bot` and `command: main.py`; the existing service becomes `dashboard`. Both build from the same `Dockerfile` (no changes) and mount the same host volumes. `push.sh` is updated to reference the new container name and add an explicit `docker stop bot` step to prevent a restart-race; a new `push_dashboard.sh` handles dashboard-only deploys by calling `docker compose up -d --build --no-deps dashboard` with no bot interaction.

**Tech Stack:** Docker Compose, Bash, Next.js (TypeScript API routes)

---

## Files changed

| File | Action | Why |
|------|--------|-----|
| `docker-compose.yml` | Modify | Split `app` into `bot` + `dashboard` services |
| `scripts/push.sh` | Modify | Fix container name `bot-app-1` → `bot`; add `docker stop bot`; remove manual `docker exec` bot start |
| `scripts/push_dashboard.sh` | Create | Dashboard-only deploy that never touches the bot container |
| `dashboard/app/api/bot/start/route.ts` | Modify | Return 503 — bot is now Docker-managed, spawning from dashboard container is dangerous |
| `dashboard/app/api/mode/route.ts` | Modify | Remove `isBotAlive()` cross-container PID check; always write mode file directly |

---

### Task 1: Split docker-compose.yml into two services

**Files:**
- Modify: `docker-compose.yml`

Context: the current file has one service called `app` that builds the shared image, exposes port 3000, and is used by `push.sh` which then starts `main.py` via `docker exec`. We're replacing it with two services that each have an explicit `container_name` so scripts can reference them predictably, and each has a `command` so Docker manages the process lifecycle.

- [ ] **Step 1: Replace docker-compose.yml**

Write this exact content to `docker-compose.yml`:

```yaml
services:
  bot:
    build: .
    container_name: bot
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./dashboard/public:/app/dashboard/public
      - ./risk_config.json:/app/risk_config.json
      - ./symbol_registry.json:/app/symbol_registry.json
    command: >
      sh -c 'cd /app && .venv/bin/python3 main.py >> /app/logs/bot.log 2>&1'
    restart: unless-stopped

  dashboard:
    build: .
    container_name: dashboard
    ports:
      - "3000:3000"
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./dashboard/public:/app/dashboard/public
      - ./risk_config.json:/app/risk_config.json
      - ./symbol_registry.json:/app/symbol_registry.json
    command: >
      sh -c 'cd /app/dashboard && node_modules/.bin/next start -p 3000'
    restart: unless-stopped
```

- [ ] **Step 2: Validate the compose file**

```bash
docker compose config
```

Expected: YAML printed with both `bot` and `dashboard` services visible, no errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: split app into bot and dashboard docker-compose services"
```

---

### Task 2: Update push.sh — fix container name, add docker stop, remove manual bot start

**Files:**
- Modify: `scripts/push.sh` (lines 30, 35, 38–45)

Context: `push.sh` currently references `bot-app-1` (the auto-assigned name for the old `app` service). With the new explicit `container_name: bot`, all three occurrences must change. Additionally, with `restart: unless-stopped`, SIGTERMing `main.py` causes Docker to immediately restart the process on the old image — `docker stop bot` suppresses that restart. The final `docker exec -d bot-app-1 sh -c '...main.py...'` block (lines 42–45) is deleted entirely because the bot now starts automatically via the container's `command`.

- [ ] **Step 1: Fix `bot-app-1` → `bot` on line 30**

Current line 30:
```
  "docker exec bot-app-1 /bin/sh -c 'for PID in ...
```

Replace with:
```bash
$SSH "$HOST" \
  "docker exec bot /bin/sh -c 'for PID in \$(grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"); do kill -TERM \$PID 2>/dev/null && echo \"Sent SIGTERM to \$PID\"; done' 2>/dev/null || echo 'Bot was not running'"
```

- [ ] **Step 2: Fix `bot-app-1` → `bot` on line 35**

Current line 35:
```
  "for i in $(seq 1 25); do PIDS=$(docker exec bot-app-1 /bin/sh -c ...
```

Replace with:
```bash
$SSH "$HOST" \
  "for i in \$(seq 1 25); do PIDS=\$(docker exec bot /bin/sh -c 'grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"' 2>/dev/null); [ -z \"\$PIDS\" ] && echo \"All bot instances exited after \${i}s\" && break; sleep 1; done; echo 'Proceeding with deploy'"
```

- [ ] **Step 3: Add `docker stop bot` after the wait loop, before the git pull block**

After the wait-loop block (after line 36) and before the `# Step 2` comment, insert:

```bash
# Explicitly stop the bot container to prevent restart: unless-stopped from
# relaunching main.py on the old image during the git pull + docker build cycle.
echo "==> Stopping bot container..."
$SSH "$HOST" "docker stop bot 2>/dev/null || true"
```

- [ ] **Step 4: Delete the manual bot-start block (old lines 42–45)**

Remove these four lines entirely:

```bash
# Step 3 — Start bot in the new container
echo "==> Starting bot..."
$SSH "$HOST" \
  "docker exec -d bot-app-1 sh -c 'cd /app && .venv/bin/python3 main.py >> /app/logs/bot.log 2>&1'"
```

The bot now starts automatically when `docker compose up -d --build` creates the `bot` container.

- [ ] **Step 5: Verify the final push.sh has no remaining `bot-app-1` references**

```bash
grep "bot-app-1" scripts/push.sh
```

Expected: no output (zero matches).

- [ ] **Step 6: Validate bash syntax**

```bash
bash -n scripts/push.sh && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 7: Commit**

```bash
git add scripts/push.sh
git commit -m "fix: update push.sh for split-container deploy — container name, docker stop, remove manual bot start"
```

---

### Task 3: Create push_dashboard.sh — dashboard-only deploy script

**Files:**
- Create: `scripts/push_dashboard.sh`

Context: this script is the whole point of the feature. It SSHs into the server, pulls the latest code, and runs `docker compose up -d --build --no-deps dashboard`. The `--no-deps` flag prevents Docker Compose from touching the `bot` service. The `--build` flag ensures the dashboard image is rebuilt with the new code. The `bot` container continues running its existing process without interruption.

- [ ] **Step 1: Create the script**

Write this content to `scripts/push_dashboard.sh`:

```bash
#!/usr/bin/env bash
# Deploy dashboard-only changes to the VPS.
# The bot container is never touched — trading continues uninterrupted.
# Usage: bash scripts/push_dashboard.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "ERROR: .env not found at $ROOT_DIR/.env"
  exit 1
fi

set -a
source "$ROOT_DIR/.env"
set +a

HOST="${SERVER_USER:-root}@${SERVER_HOST:?SERVER_HOST not set in .env}"
DIR="${SERVER_DIR:-/opt/bot}"
SSH="ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no"

echo "==> Dashboard-only deploy to $HOST:$DIR"
echo "==> Bot will NOT be restarted."

echo "==> Pulling latest code..."
$SSH "$HOST" "cd $DIR && git pull origin main"

echo "==> Rebuilding and restarting dashboard container only..."
$SSH "$HOST" "cd $DIR && docker compose up -d --build --no-deps dashboard 2>&1 | tail -20"

echo "==> Done. Bot is still running."
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/push_dashboard.sh
```

- [ ] **Step 3: Validate bash syntax**

```bash
bash -n scripts/push_dashboard.sh && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add scripts/push_dashboard.sh
git commit -m "feat: add push_dashboard.sh — deploy dashboard without restarting bot"
```

---

### Task 4: Disable the bot/start API route

**Files:**
- Modify: `dashboard/app/api/bot/start/route.ts`

Context: this route currently spawns `main.py` as a child process of its caller. In the split-container setup the caller is the `dashboard` container — so it would start a rogue bot instance inside the wrong container, sharing the same volume-mounted order files as the real bot in the `bot` container. The bot now auto-starts via `restart: unless-stopped`, so manual start from the UI is not needed. Return 503 with a clear message.

- [ ] **Step 1: Replace the route file entirely**

Write this exact content to `dashboard/app/api/bot/start/route.ts`:

```typescript
import { NextResponse } from 'next/server'

export async function POST() {
  return NextResponse.json(
    {
      ok: false,
      error: "Bot is managed by Docker — use 'docker start bot' from the CLI.",
    },
    { status: 503 }
  )
}
```

- [ ] **Step 2: Check TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors (empty output or only informational lines, no `error TS`).

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/api/bot/start/route.ts
git commit -m "fix: disable bot/start route — bot is now Docker-managed, not dashboard-spawned"
```

---

### Task 5: Simplify the mode API route — always write directly

**Files:**
- Modify: `dashboard/app/api/mode/route.ts`

Context: the current `POST` handler calls `isBotAlive()` which reads `bot_state.json` and calls Node's `process.kill(pid, 0)` to check if the PID is alive. In the split-container setup, the PID belongs to the `bot` container's PID namespace; `process.kill` in the `dashboard` container's namespace will always throw `ESRCH` (no such process), so `isBotAlive()` always returns `false`. The consequence is that the handshake branch is never reached, but the code still goes through a 60-second timeout loop before falling through to the direct write — making every mode switch appear to hang for a minute. Fix: remove `isBotAlive()` and the command-file path entirely. Always write `bot_mode.json` directly. The bot's command-poll loop reads the file on its next cycle anyway.

- [ ] **Step 1: Replace the route file entirely**

Write this exact content to `dashboard/app/api/mode/route.ts`:

```typescript
import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'

const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')

function writeModeFile(mode: string): void {
  fs.mkdirSync(path.dirname(MODE_PATH), { recursive: true })
  const tmp = MODE_PATH + '.tmp'
  fs.writeFileSync(tmp, JSON.stringify({
    mode,
    switched_at: new Date().toISOString(),
  }))
  fs.renameSync(tmp, MODE_PATH)
}

export async function GET() {
  try {
    const data = fs.existsSync(MODE_PATH)
      ? JSON.parse(fs.readFileSync(MODE_PATH, 'utf8'))
      : { mode: 'test' }
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ mode: 'test' })
  }
}

export async function POST(req: Request) {
  const { target_mode } = await req.json()
  if (!['test', 'live'].includes(target_mode)) {
    return NextResponse.json({ ok: false, error: 'Invalid mode' }, { status: 400 })
  }
  writeModeFile(target_mode)
  return NextResponse.json({ ok: true, via: 'direct' })
}
```

- [ ] **Step 2: Check TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/api/mode/route.ts
git commit -m "fix: mode route — always write bot_mode.json directly, remove cross-container isBotAlive check"
```
