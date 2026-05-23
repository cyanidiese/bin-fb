# Split Bot and Dashboard into Separate Docker Services

## Goal

Deploy dashboard-only changes without restarting the bot, by splitting the single `app` service into two independent docker-compose services: `bot` and `dashboard`.

## Architecture

### Current state
- One service (`app`) builds from one `Dockerfile` (Python 3.12 + Node.js 22)
- Bot is started after container start via `docker exec` in `push.sh`
- Any deploy requires stopping the bot, rebuilding the image, and restarting everything

### Target state
- Two services (`bot` and `dashboard`), both built from the same unchanged `Dockerfile`
- `bot` service starts `main.py` via its container `command` — no more `docker exec` hack
- `dashboard` service starts `next start` via its container `command` — same as today
- Both services mount the same host volumes — file-based IPC unchanged
- `push.sh` handles full deploys (bot + dashboard rebuilt, bot restarted gracefully)
- `push_dashboard.sh` (new) handles dashboard-only deploys — bot container never touched

## docker-compose.yml changes

Replace single `app` service with two services:

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

No changes to `Dockerfile`.

## push.sh changes

The bot now starts automatically via its container `command`, so the final `docker exec` step that manually starts `main.py` is removed. The graceful stop step remains (SIGTERM to running `main.py` processes before rebuild).

Full deploy flow:
1. SSH → SIGTERM all `main.py` PIDs inside `bot` container
2. Wait up to 25 s for cleanup
3. `git reset --hard && git clean -f dashboard/public/ && git pull origin main`
4. `docker compose up -d --build` — rebuilds image, recreates both containers; bot starts automatically
5. Done (no `docker exec` to start bot)

Updated push.sh flow:
1. SSH → SIGTERM all `main.py` PIDs inside `bot` container (change `bot-app-1` → `bot`)
2. Wait up to 25 s for cleanup
3. **`docker stop bot`** — explicitly stops the container so Docker does not auto-restart it on the old image before the rebuild (without this, `restart: unless-stopped` would restart main.py on old code for ~30–60 s during the git pull + build cycle)
4. `git reset --hard && git clean -f dashboard/public/ && git pull origin main`
5. `docker compose up -d --build` — rebuilds image, recreates both containers; bot starts automatically
6. Done (no `docker exec` to start bot)

## push_dashboard.sh (new script)

Dashboard-only deploy flow:
1. SSH → `git pull origin main`
2. `docker compose up -d --build --no-deps dashboard`
   - Rebuilds the shared image with latest code
   - Recreates only the `dashboard` container
   - `bot` container is never stopped or restarted
3. Done

No bot interaction at all — no SIGTERM, no wait loop.

## Data flow — unchanged

Bot and dashboard continue to communicate exclusively through host-mounted files:

| File | Writer | Reader |
|------|--------|--------|
| `data/open_positions_{mode}.json` | bot | dashboard |
| `data/bot_mode.json` | bot | dashboard |
| `dashboard/public/bot_state.json` | bot | dashboard |
| `dashboard/public/results*.json` | bot | dashboard |
| `dashboard/public/risk_state.json` | bot | dashboard |
| `risk_config.json` | dashboard API | bot |
| `symbol_registry.json` | dashboard API | bot |

No API layer between the two services. File-based IPC is sufficient and requires no changes.

## BOT_ROOT path — unchanged

Dashboard Next.js runs from `WORKDIR /app/dashboard` in both old and new setups. `BOT_ROOT = path.resolve(process.cwd(), '..')` resolves to `/app`. All data volumes mount under `/app/...`. No code changes needed.

## backtest_api.py — unchanged

The dashboard container includes the full Python environment (Option A). `backtest_api.py` is spawned by the dashboard's API routes exactly as today. No changes.

## Dashboard API fixes required by the split

Three routes break or degrade due to cross-container PID namespace isolation (a PID from `main.py` in the `bot` container has no meaning in the `dashboard` container).

### `/api/bot/start` — disable (BREAKS)

Currently spawns `main.py` as a child process of the caller. In the split, this creates a rogue bot instance in the dashboard container writing to the same shared order files as the real bot.

Fix: return HTTP 503 with message `"Bot is managed by Docker — use 'docker start bot' from the CLI."` The bot auto-starts with its container via `restart: unless-stopped`, so the start button is not needed in normal operation.

### `/api/mode` — simplify (RISK → OK)

Currently calls `isBotAlive(pid)` to decide whether to use the command-file handshake or the direct file-write path. Cross-container, `isBotAlive()` always returns `false`, so the handshake is never attempted and the code falls through to the direct write anyway — but only after a 60-second timeout loop.

Fix: remove the `isBotAlive()` branch entirely. Always write `bot_mode.json` directly. The bot's command-poll loop picks up the change on its next cycle. No functional loss.

### `/api/bot/stop` — acceptable degradation (leave as-is)

Primary path (write `bot_command.json`, wait for `bot_command_result.json` via shared volume) still works. The SIGTERM fallback silently reports "already stopped" cross-container, but the primary path already handled the stop. No code change required.

## Out of scope

- Separate Dockerfiles per service (can be done later if image size becomes a concern)
- Health checks or `depends_on` between services (bot and dashboard are independent)
- Any changes to bot trading logic or backtest routes
