# Order Execution and Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement test/live mode switching, start/stop controls, emergency alerting, system logging, order execution system (replacing paper trader), virtual order tracking, symbol disable flow, and supporting infrastructure.

**Architecture:** Bot communicates with the dashboard via JSON files in `data/` and `dashboard/public/`. A 2-second command-poll loop (independent of the candle loop) reads `data/bot_command.json`. Mode (test/live) is runtime-switchable; test mode uses `testnet.binancefuture.com`, live uses `fapi.binance.com`. An obligatory backtest runs on every bot start and every mode change before any orders are placed. Next.js spawns/kills the bot process; bot writes a heartbeat every 10s; dashboard detects STOPPED when heartbeat is >30s old.

**Tech Stack:** Python 3.11+, asyncio, python-binance, pytest; Next.js 15 App Router, TypeScript, Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-05-08-order-execution-and-infrastructure-design.md`

---

## Phase 1 — Logging & Notification Foundation

### Task 1: Extend `risk_config.py` with new fields

**Files:**
- Modify: `config/risk_config.py`
- Test: `tests/test_risk_config.py` (extend existing)

- [ ] **Step 1: Add failing tests for new fields**

Open `tests/test_risk_config.py` and append:

```python
def test_new_defaults_present(tmp_path):
    path = tmp_path / "risk_config.json"
    cfg = load_risk_config(path)
    assert "telegram" in cfg
    assert cfg["telegram"] == {"token": "", "chat_id": ""}
    assert cfg["min_balance_usdt"] == 0.0
    assert cfg["consecutive_failure_threshold"] == 3
    assert cfg["test_starting_balance_usdt"] == 10000.0
    assert cfg["max_leverage"] == 20
    assert cfg["price_stale_threshold_s"] == 15

def test_existing_file_missing_new_keys_gets_defaults(tmp_path):
    path = tmp_path / "risk_config.json"
    # Write file without new keys
    path.write_text('{"drawdown_warning_pct": 10.0}')
    cfg = load_risk_config(path)
    assert cfg["drawdown_warning_pct"] == 10.0
    assert cfg["telegram"] == {"token": "", "chat_id": ""}
    assert cfg["consecutive_failure_threshold"] == 3
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_risk_config.py::test_new_defaults_present -v
```

Expected: `FAILED` — KeyError or AssertionError.

- [ ] **Step 3: Add new fields to `DEFAULT_CONFIG` in `config/risk_config.py`**

Find the `DEFAULT_CONFIG` dict and add the new keys:

```python
DEFAULT_CONFIG: dict = {
    # --- existing keys preserved as-is ---
    # ... (all current keys stay) ...

    # Telegram alerting
    "telegram": {"token": "", "chat_id": ""},

    # Emergency thresholds
    "min_balance_usdt": 0.0,
    "consecutive_failure_threshold": 3,

    # Test mode
    "test_starting_balance_usdt": 10000.0,

    # Order execution
    "max_leverage": 20,
    "price_stale_threshold_s": 15,
}
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_risk_config.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add config/risk_config.py tests/test_risk_config.py
git commit -m "feat: add telegram, balance floor, test_balance, leverage fields to risk_config"
```

---

### Task 2: `bot/system_log.py` — rolling 100-entry log writer

**Files:**
- Create: `bot/system_log.py`
- Create: `tests/test_system_log.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_system_log.py
import json
from pathlib import Path
from bot.system_log import append_entry, MAX_ENTRIES

def test_creates_file_and_appends(tmp_path):
    path = tmp_path / "log.json"
    append_entry(path, level="info", title="hello", detail="world", source="test")
    entries = json.loads(path.read_text())
    assert len(entries) == 1
    e = entries[0]
    assert e["level"] == "info"
    assert e["title"] == "hello"
    assert e["detail"] == "world"
    assert e["source"] == "test"
    assert "id" in e
    assert "timestamp" in e

def test_rolling_cap(tmp_path):
    path = tmp_path / "log.json"
    for i in range(MAX_ENTRIES + 10):
        append_entry(path, "info", f"title {i}", "", "test")
    entries = json.loads(path.read_text())
    assert len(entries) == MAX_ENTRIES
    # Oldest entry should be gone — first surviving entry should be index 10
    assert entries[0]["title"] == f"title {10}"

def test_atomic_write_no_partial(tmp_path):
    path = tmp_path / "log.json"
    append_entry(path, "info", "a", "", "test")
    # tmp file must not remain after write
    assert not (tmp_path / "log.json.tmp").exists()

def test_existing_corrupt_file_is_reset(tmp_path):
    path = tmp_path / "log.json"
    path.write_text("{{broken json")
    append_entry(path, "warning", "b", "", "test")
    entries = json.loads(path.read_text())
    assert len(entries) == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_system_log.py -v
```

Expected: `ERROR` — ModuleNotFoundError.

- [ ] **Step 3: Implement `bot/system_log.py`**

```python
# bot/system_log.py
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_ENTRIES = 100


def append_entry(
    path: Path,
    level: str,
    title: str,
    detail: str,
    source: str,
) -> None:
    entries = _read(path)
    entries.append({
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "title": title,
        "detail": detail,
        "source": source,
    })
    if len(entries) > MAX_ENTRIES:
        entries = entries[len(entries) - MAX_ENTRIES:]
    _write(path, entries)


def _read(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _write(path: Path, entries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(path)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_system_log.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/system_log.py tests/test_system_log.py
git commit -m "feat: add system_log.py — rolling 100-entry JSON event log"
```

---

### Task 3: `bot/notifier.py` — Telegram + alert state + log wrapper

**Files:**
- Create: `bot/notifier.py`
- Create: `tests/test_notifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_notifier.py
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from bot.notifier import Notifier

def _make_notifier(tmp_path):
    return Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="",
        telegram_chat_id="",
    )

def test_info_writes_log_not_alert(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("info", "Boot", "started", "main")
    log = json.loads((tmp_path / "system_log.json").read_text())
    assert len(log) == 1
    assert log[0]["level"] == "info"
    # alert_state should not be created for info
    assert not (tmp_path / "alert_state.json").exists()

def test_emergency_writes_log_and_alert(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("emergency", "Crash", "details", "main")
    alert_state = json.loads((tmp_path / "alert_state.json").read_text())
    assert len(alert_state["alerts"]) == 1
    assert alert_state["alerts"][0]["level"] == "emergency"
    assert "dismissed_ids" in alert_state

def test_warning_writes_alert(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("warning", "Warn", "details", "main")
    alert_state = json.loads((tmp_path / "alert_state.json").read_text())
    assert len(alert_state["alerts"]) == 1

def test_notify_never_throws_on_telegram_error(tmp_path):
    n = Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="bad_token",
        telegram_chat_id="12345",
    )
    # Should not raise even if Telegram call fails
    n.notify("emergency", "Test", "body", "test")

def test_dismiss_removes_id(tmp_path):
    n = _make_notifier(tmp_path)
    n.notify("emergency", "Alert", "body", "src")
    state = json.loads((tmp_path / "alert_state.json").read_text())
    alert_id = state["alerts"][0]["id"]
    n.dismiss(alert_id)
    state2 = json.loads((tmp_path / "alert_state.json").read_text())
    assert alert_id in state2["dismissed_ids"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_notifier.py -v
```

Expected: `ERROR` — ModuleNotFoundError.

- [ ] **Step 3: Implement `bot/notifier.py`**

```python
# bot/notifier.py
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests

from bot.system_log import append_entry

logger = logging.getLogger(__name__)

_ALERT_LEVELS = {"warning", "emergency"}


class Notifier:
    def __init__(
        self,
        log_path: Path,
        alert_path: Path,
        telegram_token: str,
        telegram_chat_id: str,
    ) -> None:
        self._log_path = log_path
        self._alert_path = alert_path
        self._token = telegram_token
        self._chat_id = telegram_chat_id

    def notify(
        self,
        level: Literal["info", "warning", "emergency"],
        title: str,
        body: str,
        source: str,
    ) -> None:
        try:
            append_entry(self._log_path, level, title, body, source)
        except Exception as exc:
            logger.error(f"system_log write failed: {exc}")

        if level in _ALERT_LEVELS:
            try:
                self._append_alert(level, title, body, source)
            except Exception as exc:
                logger.error(f"alert_state write failed: {exc}")

        if self._token and self._chat_id:
            try:
                self._send_telegram(level, title, body)
            except Exception as exc:
                # Log locally only — never re-notify to avoid loops
                logger.error(f"Telegram send failed: {exc}")
                try:
                    append_entry(
                        self._log_path, "warning",
                        "Telegram send failed", str(exc), "notifier"
                    )
                except Exception:
                    pass

    def dismiss(self, alert_id: str) -> None:
        state = self._read_alert_state()
        if alert_id not in state["dismissed_ids"]:
            state["dismissed_ids"].append(alert_id)
        self._write_alert_state(state)

    def send_test(self) -> tuple[bool, str]:
        """Returns (ok, error_message)."""
        if not self._token or not self._chat_id:
            return False, "Token or chat_id not configured"
        try:
            self._send_telegram("info", "Test notification", "Bot notifier is working.")
            return True, ""
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------ #

    def _append_alert(self, level: str, title: str, body: str, source: str) -> None:
        state = self._read_alert_state()
        state["alerts"].append({
            "id": str(uuid.uuid4()),
            "level": level,
            "title": title,
            "body": body,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._write_alert_state(state)

    def _send_telegram(self, level: str, title: str, body: str) -> None:
        emoji = {"info": "ℹ️", "warning": "⚠️", "emergency": "🚨"}.get(level, "")
        text = f"{emoji} *{title}*\n{body}"
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        resp = requests.post(url, json={"chat_id": self._chat_id, "text": text,
                                        "parse_mode": "Markdown"}, timeout=10)
        resp.raise_for_status()

    def _read_alert_state(self) -> dict:
        if self._alert_path.exists():
            try:
                return json.loads(self._alert_path.read_text())
            except Exception:
                pass
        return {"alerts": [], "dismissed_ids": []}

    def _write_alert_state(self, state: dict) -> None:
        self._alert_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._alert_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self._alert_path)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_notifier.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/notifier.py tests/test_notifier.py
git commit -m "feat: add notifier.py — Telegram sender, alert state writer, log wrapper"
```

---

## Phase 2 — Bot Process Management

### Task 4: Bot writes `bot_pid.json` and `bot_state.json` heartbeat

**Files:**
- Modify: `main.py`
- No new tests (integration-level; verified by observing dashboard state)

- [ ] **Step 1: Add constants and helpers near the top of `main.py`**

Locate the imports section of `main.py` and add:

```python
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_BOT_PID_PATH = _PROJECT_ROOT / "data" / "bot_pid.json"
_BOT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "bot_state.json"
_HEARTBEAT_INTERVAL = 10  # seconds
_HEARTBEAT_STALE_THRESHOLD = 30  # seconds — matches dashboard assumption
```

- [ ] **Step 2: Add `write_pid()` and `write_state()` functions to `main.py`**

```python
def write_pid() -> None:
    _BOT_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_PID_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}))
    tmp.replace(_BOT_PID_PATH)


def write_bot_state(running: bool, mode: str, started_at: str,
                    symbols_active: int = 0, symbols_disabled: int = 0) -> None:
    from datetime import datetime, timezone
    _BOT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "running": running,
        "pid": os.getpid(),
        "mode": mode,
        "started_at": started_at,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "symbols_active": symbols_active,
        "symbols_disabled": symbols_disabled,
    }))
    tmp.replace(_BOT_STATE_PATH)


async def heartbeat_loop(mode: str, started_at: str,
                         get_counts: callable) -> None:
    """Writes bot_state.json every HEARTBEAT_INTERVAL seconds."""
    while True:
        active, disabled = get_counts()
        write_bot_state(True, mode, started_at, active, disabled)
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
```

- [ ] **Step 3: At the start of `main()` (or `async_main()`), call `write_pid()` and write initial state**

Find the entry point function in `main.py` and add at the very beginning (before any other setup):

```python
from datetime import datetime, timezone
started_at = datetime.now(timezone.utc).isoformat()
write_pid()
write_bot_state(running=True, mode=current_mode, started_at=started_at)
```

- [ ] **Step 4: At clean shutdown (finally block or SIGTERM handler), write `running: false`**

```python
# In the finally / shutdown block:
write_bot_state(running=False, mode=current_mode, started_at=started_at)
```

- [ ] **Step 5: Wire heartbeat_loop into asyncio.gather in `main()`**

```python
# Alongside existing tasks in asyncio.gather:
asyncio.create_task(heartbeat_loop(current_mode, started_at, lambda: (len(active_symbols), len(disabled_symbols))))
```

- [ ] **Step 6: Add `data/bot_pid.json` to `.gitignore`**

```
# Bot runtime state
data/bot_pid.json
data/bot_mode.json
data/bot_command.json
data/bot_command_result.json
data/system_log.json
data/preset_efficiency_test.json
data/preset_efficiency_live.json
data/virtual_orders_test.json
data/virtual_orders_live.json
dashboard/public/bot_state.json
dashboard/public/alert_state.json
```

- [ ] **Step 7: Commit**

```bash
git add main.py .gitignore
git commit -m "feat: bot writes bot_pid.json and bot_state.json heartbeat every 10s"
```

---

### Task 5: Next.js `/api/bot/start` and `/api/bot/stop` routes

**Files:**
- Create: `dashboard/app/api/bot/start/route.ts`
- Create: `dashboard/app/api/bot/stop/route.ts`

- [ ] **Step 1: Create `dashboard/app/api/bot/start/route.ts`**

```typescript
// dashboard/app/api/bot/start/route.ts
import { NextResponse } from 'next/server'
import { spawn } from 'child_process'
import { BOT_ROOT, isAlive } from '../../_utils'
import path from 'path'
import fs from 'fs'

export async function POST() {
  // Check if already running
  const statePath = path.join(BOT_ROOT, 'dashboard', 'public', 'bot_state.json')
  if (fs.existsSync(statePath)) {
    try {
      const state = JSON.parse(fs.readFileSync(statePath, 'utf8'))
      if (state.running && state.pid && isAlive(state.pid)) {
        return NextResponse.json({ ok: false, error: 'Bot is already running' }, { status: 409 })
      }
    } catch {}
  }

  const child = spawn('python', ['main.py'], {
    cwd: BOT_ROOT,
    detached: true,
    stdio: 'ignore',
  })
  child.unref()

  return NextResponse.json({ ok: true, pid: child.pid })
}
```

- [ ] **Step 2: Create `dashboard/app/api/bot/stop/route.ts`**

```typescript
// dashboard/app/api/bot/stop/route.ts
import { NextResponse } from 'next/server'
import { BOT_ROOT, isAlive } from '../../_utils'
import path from 'path'
import fs from 'fs'

const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')
const PID_PATH = path.join(BOT_ROOT, 'data', 'bot_pid.json')
const COMMAND_TIMEOUT_MS = 10_000

function writeCommand(id: string) {
  const tmp = COMMAND_PATH + '.tmp'
  fs.writeFileSync(tmp, JSON.stringify({ id, type: 'stop_bot', payload: {}, issued_at: new Date().toISOString() }))
  fs.renameSync(tmp, COMMAND_PATH)
}

async function waitForResult(id: string): Promise<boolean> {
  const deadline = Date.now() + COMMAND_TIMEOUT_MS
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const result = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (result.id === id && result.ok) return true
    } catch {}
  }
  return false
}

function sigterm(): boolean {
  if (!fs.existsSync(PID_PATH)) return false
  try {
    const { pid } = JSON.parse(fs.readFileSync(PID_PATH, 'utf8'))
    if (pid && isAlive(pid)) {
      process.kill(pid, 'SIGTERM')
      return true
    }
  } catch {}
  return false
}

export async function POST() {
  const id = crypto.randomUUID()
  writeCommand(id)
  const ok = await waitForResult(id)
  if (!ok) {
    const killed = sigterm()
    if (!killed) return NextResponse.json({ ok: false, error: 'Bot not responding and no PID found' }, { status: 500 })
  }
  return NextResponse.json({ ok: true })
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/api/bot/
git commit -m "feat: add /api/bot/start and /api/bot/stop Next.js routes"
```

---

### Task 6: Settings page — Start Bot / Stop Bot buttons

**Files:**
- Modify: `dashboard/app/settings/page.tsx`

- [ ] **Step 1: Add bot status polling and start/stop handlers**

At the top of the Settings page component, add state and polling:

```typescript
const [botState, setBotState] = useState<{ running: boolean; mode: string } | null>(null)
const [botActionLoading, setBotActionLoading] = useState(false)
const [botActionError, setBotActionError] = useState('')

useEffect(() => {
  const poll = async () => {
    try {
      const r = await fetch(`/dashboard/public/bot_state.json?t=${Date.now()}`)
      if (r.ok) setBotState(await r.json())
    } catch {}
  }
  poll()
  const id = setInterval(poll, 5000)
  return () => clearInterval(id)
}, [])

const handleStart = async () => {
  setBotActionLoading(true); setBotActionError('')
  const r = await fetch('/api/bot/start', { method: 'POST' })
  const data = await r.json()
  if (!data.ok) setBotActionError(data.error || 'Failed to start bot')
  setBotActionLoading(false)
}

const handleStop = async () => {
  if (!confirm('Stop the bot? All open orders will be closed at market price.')) return
  setBotActionLoading(true); setBotActionError('')
  const r = await fetch('/api/bot/stop', { method: 'POST' })
  const data = await r.json()
  if (!data.ok) setBotActionError(data.error || 'Failed to stop bot')
  setBotActionLoading(false)
}
```

- [ ] **Step 2: Add the Bot Control section to the JSX**

Add before the existing first section in the Settings page:

```tsx
<section className="mb-8">
  <h2 className="text-lg font-semibold mb-3">Bot Control</h2>
  <div className="flex items-center gap-4">
    {botState?.running ? (
      <button
        onClick={handleStop}
        disabled={botActionLoading}
        className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
      >
        {botActionLoading ? 'Stopping…' : 'Stop Bot'}
      </button>
    ) : (
      <button
        onClick={handleStart}
        disabled={botActionLoading}
        className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
      >
        {botActionLoading ? 'Starting…' : 'Start Bot'}
      </button>
    )}
    <span className="text-sm text-slate-400">
      {botState ? (botState.running ? `Running in ${botState.mode?.toUpperCase()} mode` : 'Stopped') : 'Unknown'}
    </span>
  </div>
  {botActionError && <p className="mt-2 text-sm text-red-400">{botActionError}</p>}
</section>
```

- [ ] **Step 3: Start dashboard dev server and verify buttons render**

```bash
cd dashboard && npm run dev
```

Open `http://localhost:3000/settings`. Verify Start/Stop button appears based on bot state.

- [ ] **Step 4: Commit**

```bash
git add dashboard/app/settings/page.tsx
git commit -m "feat: add Start Bot / Stop Bot buttons to Settings page"
```

---

## Phase 3 — Mode Model Refactor

### Task 7: `config/settings.py` — `testnet` → `test` alias, remove `LIVE_MODE_CONFIRMED`

**Files:**
- Modify: `config/settings.py`
- Modify: `tests/test_risk_config.py` (no breaking changes expected)

- [ ] **Step 1: In `load_settings()`, accept `testnet` as an alias for `test`**

Find:
```python
trading_mode = os.getenv('TRADING_MODE', 'testnet').lower()
if trading_mode not in ('testnet', 'live'):
    raise RuntimeError(...)
```

Replace with:
```python
_raw_mode = os.getenv('TRADING_MODE', 'test').lower()
# Accept legacy 'testnet' value
trading_mode = 'test' if _raw_mode == 'testnet' else _raw_mode
if trading_mode not in ('test', 'live'):
    raise RuntimeError(f"TRADING_MODE must be 'test' or 'live', got: '{_raw_mode}'")
if _raw_mode == 'testnet':
    import logging
    logging.getLogger(__name__).warning(
        "TRADING_MODE=testnet is deprecated — treating as 'test'. Update your .env."
    )
```

- [ ] **Step 2: Remove the `LIVE_MODE_CONFIRMED` guard**

Find and delete this block entirely:
```python
if trading_mode == 'live':
    confirmed = os.getenv('LIVE_MODE_CONFIRMED', '').strip().lower()
    if confirmed != 'yes':
        raise RuntimeError(...)
```

Mode switching is now gated through the dashboard confirmation flow, not `.env`.

- [ ] **Step 3: Update API key selection to use `test` instead of `testnet`**

Find:
```python
if trading_mode == 'testnet':
    api_key = os.getenv('TESTNET_API_KEY', '')
    ...
```

Replace:
```python
if trading_mode == 'test':
    api_key = os.getenv('TESTNET_API_KEY', '')
    api_secret = os.getenv('TESTNET_API_SECRET', '')
    key_names = ('TESTNET_API_KEY', 'TESTNET_API_SECRET')
else:
    api_key = os.getenv('API_KEY', '')
    api_secret = os.getenv('API_SECRET', '')
    key_names = ('API_KEY', 'API_SECRET')
```

- [ ] **Step 4: Run existing tests to confirm nothing broken**

```bash
python -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add config/settings.py
git commit -m "feat: accept testnet as alias for test in TRADING_MODE, remove LIVE_MODE_CONFIRMED guard"
```

---

### Task 8: `bot/data_feed.py` — runtime mode injection

**Files:**
- Modify: `bot/data_feed.py`

The goal: `DataFeed` must accept a `mode` argument at construction so `mode_manager` can re-initialise it at runtime without restarting the process. Currently mode is baked in from `settings.trading_mode` at construction — that's fine for now, but `DataFeed` should expose a `reinit(mode)` method.

- [ ] **Step 1: Add `reinit()` method to `DataFeed`**

Inside the `DataFeed` class, after `__init__`, add:

```python
def reinit(self, mode: str, api_key: str, api_secret: str) -> None:
    """Re-initialise client and endpoints for a new mode without creating a new DataFeed."""
    self._is_testnet = (mode == 'test')
    self._mode_suffix = 'test' if self._is_testnet else 'live'
    self._client = Client(api_key, api_secret, testnet=self._is_testnet)
    if self._is_testnet:
        self._client.FUTURES_URL = _FUTURES_REST_TESTNET
    self._ws_base = _WS_TESTNET if self._is_testnet else _WS_LIVE
```

- [ ] **Step 2: Expose combined-stream WS URL builder (used in Task 23)**

```python
@staticmethod
def combined_stream_url(symbols: list[str], timeframe: str, testnet: bool) -> str:
    streams = '/'.join(f"{s.lower()}@kline_{timeframe}" for s in symbols)
    base = _WS_TESTNET if testnet else _WS_LIVE
    return f"{base.rstrip('/ws')}/stream?streams={streams}"
```

- [ ] **Step 3: Commit**

```bash
git add bot/data_feed.py
git commit -m "feat: add DataFeed.reinit() for runtime mode switching and combined stream URL builder"
```

---

### Task 9: `backtest.py` — `--mode` parameter

**Files:**
- Modify: `backtest.py`

- [ ] **Step 1: Add `--mode` argument to the argparse block**

Find the `argparse` section in `backtest.py` and add:

```python
parser.add_argument(
    '--mode', choices=['test', 'live'], default=None,
    help="Override TRADING_MODE for this backtest run ('test' uses testnet klines, 'live' uses fapi)"
)
```

- [ ] **Step 2: Apply mode override before `load_settings()` is called**

```python
args = parser.parse_args()
if args.mode:
    os.environ['TRADING_MODE'] = args.mode
```

- [ ] **Step 3: Verify it runs without error**

```bash
python backtest.py --mode test --klines-count 100 2>&1 | head -20
```

Expected: starts loading klines, no crash.

- [ ] **Step 4: Commit**

```bash
git add backtest.py
git commit -m "feat: add --mode param to backtest.py for testnet/live kline selection"
```

---

## Phase 4 — Mode Manager & Command Channel

### Task 10: `bot/mode_manager.py` — mode state and command poll loop

**Files:**
- Create: `bot/mode_manager.py`
- Create: `tests/test_mode_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mode_manager.py
import json
import asyncio
from pathlib import Path
from bot.mode_manager import ModeManager

def test_default_mode_is_test(tmp_path):
    mm = ModeManager(mode_path=tmp_path / "bot_mode.json",
                     command_path=tmp_path / "bot_command.json",
                     result_path=tmp_path / "bot_command_result.json")
    assert mm.current_mode == "test"

def test_write_and_read_mode(tmp_path):
    mode_path = tmp_path / "bot_mode.json"
    mm = ModeManager(mode_path=mode_path,
                     command_path=tmp_path / "bot_command.json",
                     result_path=tmp_path / "bot_command_result.json")
    mm._write_mode("live")
    assert json.loads(mode_path.read_text())["mode"] == "live"
    mm2 = ModeManager(mode_path=mode_path,
                      command_path=tmp_path / "bot_command.json",
                      result_path=tmp_path / "bot_command_result.json")
    assert mm2.current_mode == "live"

def test_poll_reads_and_clears_command(tmp_path):
    command_path = tmp_path / "bot_command.json"
    mm = ModeManager(mode_path=tmp_path / "bot_mode.json",
                     command_path=command_path,
                     result_path=tmp_path / "bot_command_result.json")
    command_path.write_text(json.dumps(
        {"id": "abc", "type": "stop_bot", "payload": {}, "issued_at": "2026-01-01T00:00:00Z"}
    ))
    cmd = mm._read_and_clear_command()
    assert cmd is not None
    assert cmd["type"] == "stop_bot"
    assert not command_path.exists()

def test_poll_returns_none_when_no_command(tmp_path):
    mm = ModeManager(mode_path=tmp_path / "bot_mode.json",
                     command_path=tmp_path / "bot_command.json",
                     result_path=tmp_path / "bot_command_result.json")
    assert mm._read_and_clear_command() is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_mode_manager.py -v
```

Expected: `ERROR` — ModuleNotFoundError.

- [ ] **Step 3: Implement `bot/mode_manager.py` (state + poll, switch logic in Task 11)**

```python
# bot/mode_manager.py
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODE_PATH = _PROJECT_ROOT / "data" / "bot_mode.json"
_DEFAULT_COMMAND_PATH = _PROJECT_ROOT / "data" / "bot_command.json"
_DEFAULT_RESULT_PATH = _PROJECT_ROOT / "data" / "bot_command_result.json"

POLL_INTERVAL = 2.0  # seconds


class ModeManager:
    def __init__(
        self,
        mode_path: Path = _DEFAULT_MODE_PATH,
        command_path: Path = _DEFAULT_COMMAND_PATH,
        result_path: Path = _DEFAULT_RESULT_PATH,
    ) -> None:
        self._mode_path = mode_path
        self._command_path = command_path
        self._result_path = result_path
        self._lock = asyncio.Lock()
        self.current_mode: str = self._read_mode()

    # ------------------------------------------------------------------ #
    # Mode state                                                           #
    # ------------------------------------------------------------------ #

    def _read_mode(self) -> str:
        if self._mode_path.exists():
            try:
                return json.loads(self._mode_path.read_text()).get("mode", "test")
            except Exception:
                pass
        return "test"

    def _write_mode(self, mode: str) -> None:
        self._mode_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._mode_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"mode": mode, "switched_at": datetime.now(timezone.utc).isoformat()}))
        tmp.replace(self._mode_path)
        self.current_mode = mode

    # ------------------------------------------------------------------ #
    # Command polling                                                      #
    # ------------------------------------------------------------------ #

    def _read_and_clear_command(self) -> dict | None:
        if not self._command_path.exists():
            return None
        try:
            data = json.loads(self._command_path.read_text())
            self._command_path.unlink(missing_ok=True)
            return data
        except Exception as exc:
            logger.error(f"Failed to read command file: {exc}")
            self._command_path.unlink(missing_ok=True)
            return None

    def _write_result(self, cmd_id: str, ok: bool, error: str = "") -> None:
        self._result_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._result_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "id": cmd_id,
            "ok": ok,
            "error": error,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }))
        tmp.replace(self._result_path)

    async def poll_loop(
        self,
        on_switch_mode: Callable[[str], Awaitable[None]],
        on_stop_bot: Callable[[], Awaitable[None]],
    ) -> None:
        """2-second poll loop. Runs as a background asyncio task."""
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            cmd = self._read_and_clear_command()
            if cmd is None:
                continue
            cmd_id = cmd.get("id", "unknown")
            cmd_type = cmd.get("type")
            async with self._lock:
                try:
                    if cmd_type == "switch_mode":
                        target = cmd.get("payload", {}).get("target_mode", "test")
                        await on_switch_mode(target)
                        self._write_result(cmd_id, ok=True)
                    elif cmd_type == "stop_bot":
                        await on_stop_bot()
                        self._write_result(cmd_id, ok=True)
                    else:
                        logger.warning(f"Unknown command type: {cmd_type}")
                        self._write_result(cmd_id, ok=False, error=f"Unknown type: {cmd_type}")
                except Exception as exc:
                    logger.error(f"Command {cmd_type} failed: {exc}")
                    self._write_result(cmd_id, ok=False, error=str(exc))
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_mode_manager.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/mode_manager.py tests/test_mode_manager.py
git commit -m "feat: add mode_manager.py — mode state persistence and 2s command poll loop"
```

---

### Task 11: `bot/mode_manager.py` — `switch_mode()` sequence

**Files:**
- Modify: `bot/mode_manager.py`
- Modify: `tests/test_mode_manager.py`

- [ ] **Step 1: Add failing test for switch sequence**

```python
# Append to tests/test_mode_manager.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_switch_mode_calls_close_orders_then_updates_mode(tmp_path):
    mm = ModeManager(mode_path=tmp_path / "bot_mode.json",
                     command_path=tmp_path / "bot_command.json",
                     result_path=tmp_path / "bot_command_result.json")

    close_called = []
    backtest_called = []

    async def fake_close():
        close_called.append(True)

    async def fake_backtest(mode):
        backtest_called.append(mode)

    await mm.switch_mode("live", close_all=fake_close, run_backtest=fake_backtest)

    assert close_called == [True]
    assert backtest_called == ["live"]
    assert mm.current_mode == "live"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pip install pytest-asyncio
python -m pytest tests/test_mode_manager.py::test_switch_mode_calls_close_orders_then_updates_mode -v
```

Expected: `FAILED` — AttributeError (no `switch_mode` method).

- [ ] **Step 3: Add `switch_mode()` and `stop_bot()` to `ModeManager`**

```python
# Add to ModeManager class in bot/mode_manager.py

async def switch_mode(
    self,
    target_mode: str,
    close_all: Callable[[], Awaitable[None]],
    run_backtest: Callable[[str], Awaitable[None]],
) -> None:
    if target_mode == self.current_mode:
        logger.info(f"Already in {target_mode} mode — no switch needed")
        return

    logger.info(f"Switching mode: {self.current_mode} → {target_mode}")
    await close_all()
    await run_backtest(target_mode)
    self._write_mode(target_mode)
    logger.info(f"Mode switch complete — now in {target_mode}")

async def stop_bot(
    self,
    close_all: Callable[[], Awaitable[None]],
) -> None:
    logger.info("Stop bot command received")
    await close_all()
    logger.info("All orders closed — stopping")
```

- [ ] **Step 4: Run all mode_manager tests**

```bash
python -m pytest tests/test_mode_manager.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/mode_manager.py tests/test_mode_manager.py
git commit -m "feat: add switch_mode() and stop_bot() sequences to ModeManager"
```

---

### Task 12: `/api/mode` route and Settings page mode switcher

**Files:**
- Create: `dashboard/app/api/mode/route.ts`
- Modify: `dashboard/app/settings/page.tsx`

- [ ] **Step 1: Create `dashboard/app/api/mode/route.ts`**

```typescript
// dashboard/app/api/mode/route.ts
import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'
import crypto from 'crypto'

const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')
const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')

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

  const id = crypto.randomUUID()
  const tmp = COMMAND_PATH + '.tmp'
  fs.mkdirSync(path.dirname(COMMAND_PATH), { recursive: true })
  fs.writeFileSync(tmp, JSON.stringify({
    id, type: 'switch_mode',
    payload: { target_mode },
    issued_at: new Date().toISOString(),
  }))
  fs.renameSync(tmp, COMMAND_PATH)

  // Poll for result (60s timeout)
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const result = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (result.id === id) {
        return NextResponse.json(result)
      }
    } catch {}
  }
  return NextResponse.json({ ok: false, error: 'Timeout waiting for mode switch' }, { status: 504 })
}
```

- [ ] **Step 2: Add mode switcher section to Settings page**

```tsx
// In dashboard/app/settings/page.tsx — add below Bot Control section

const [mode, setMode] = useState<string>('test')
const [modeSwitching, setModeSwitching] = useState(false)
const [modeError, setModeError] = useState('')

useEffect(() => {
  fetch('/api/mode').then(r => r.json()).then(d => setMode(d.mode)).catch(() => {})
}, [])

const handleSwitchMode = async () => {
  const target = mode === 'test' ? 'live' : 'test'
  const msg = target === 'live'
    ? 'Switch to LIVE mode? Real orders will be placed with real money.'
    : 'Switch to TEST mode? All open orders will be closed at market price.'
  if (!confirm(msg)) return
  setModeSwitching(true); setModeError('')
  const r = await fetch('/api/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_mode: target }),
  })
  const data = await r.json()
  if (data.ok) setMode(target)
  else setModeError(data.error || 'Mode switch failed')
  setModeSwitching(false)
}
```

```tsx
<section className="mb-8">
  <h2 className="text-lg font-semibold mb-3">Trading Mode</h2>
  <div className="flex items-center gap-4">
    <span className={`px-3 py-1 rounded text-sm font-mono font-bold ${mode === 'live' ? 'bg-amber-500 text-black' : 'bg-slate-600 text-white'}`}>
      {mode.toUpperCase()}
    </span>
    <button
      onClick={handleSwitchMode}
      disabled={modeSwitching || !botState?.running}
      className="px-4 py-2 bg-slate-700 text-white rounded hover:bg-slate-600 disabled:opacity-50 text-sm"
    >
      {modeSwitching ? 'Switching…' : `Switch to ${mode === 'test' ? 'LIVE' : 'TEST'}`}
    </button>
    {!botState?.running && <span className="text-xs text-slate-500">Start bot to switch modes</span>}
  </div>
  {modeError && <p className="mt-2 text-sm text-red-400">{modeError}</p>}
</section>
```

- [ ] **Step 3: Verify in browser**

Open `http://localhost:3000/settings`. Mode section should show current mode, switch button disabled when bot stopped.

- [ ] **Step 4: Commit**

```bash
git add dashboard/app/api/mode/ dashboard/app/settings/page.tsx
git commit -m "feat: add /api/mode route and mode switcher UI on Settings page"
```

---

## Phase 5 — Dashboard: Badge, Alerts, Log

### Task 13: `ModeBadge` component and `layout.tsx` wiring

**Files:**
- Create: `dashboard/components/ModeBadge.tsx`
- Modify: `dashboard/app/layout.tsx`

- [ ] **Step 1: Create `dashboard/components/ModeBadge.tsx`**

```tsx
// dashboard/components/ModeBadge.tsx
'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'

type BotState = { running: boolean; mode: string; last_heartbeat?: string }

export default function ModeBadge() {
  const [state, setState] = useState<BotState | null>(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`/bot_state.json?t=${Date.now()}`)
        if (r.ok) {
          const data: BotState = await r.json()
          // Check staleness
          if (data.last_heartbeat) {
            const age = Date.now() - new Date(data.last_heartbeat).getTime()
            if (age > 30_000) data.running = false
          }
          setState(data)
        }
      } catch {}
    }
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [])

  const mode = state?.mode?.toUpperCase() ?? '…'
  const running = state?.running ?? false
  const isLive = state?.mode === 'live'

  return (
    <Link href="/settings" title="Go to Settings">
      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono font-semibold border
        ${isLive && running ? 'border-amber-500 text-amber-400' : 'border-slate-600 text-slate-400'}`}>
        {isLive && running && (
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
        )}
        {mode} · {running ? 'RUNNING' : 'STOPPED'}
      </span>
    </Link>
  )
}
```

- [ ] **Step 2: Add `ModeBadge` to `dashboard/app/layout.tsx`**

Find the nav/header section in `layout.tsx` and add before `<NavBar>`:

```tsx
import ModeBadge from '@/components/ModeBadge'

// Inside the layout JSX, before <NavBar />:
<div className="flex items-center gap-3 px-4 py-2 border-b border-slate-800">
  <ModeBadge />
  <NavBar />
</div>
```

- [ ] **Step 3: Verify badge appears on all pages**

Check `http://localhost:3000` — badge should show `TEST · STOPPED` (or RUNNING) in the top bar.

- [ ] **Step 4: Commit**

```bash
git add dashboard/components/ModeBadge.tsx dashboard/app/layout.tsx
git commit -m "feat: add ModeBadge to layout — shows TEST/LIVE · RUNNING/STOPPED on every page"
```

---

### Task 14: Alert banner and dismiss API

**Files:**
- Create: `dashboard/app/api/alerts/dismiss/route.ts`
- Create: `dashboard/components/AlertBanner.tsx`
- Modify: `dashboard/app/layout.tsx`

- [ ] **Step 1: Create dismiss route**

```typescript
// dashboard/app/api/alerts/dismiss/route.ts
import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../../../_utils'
import path from 'path'
import fs from 'fs'

const ALERT_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'alert_state.json')

export async function POST(req: Request) {
  const { id } = await req.json()
  if (!id) return NextResponse.json({ ok: false, error: 'Missing id' }, { status: 400 })

  let state = { alerts: [] as object[], dismissed_ids: [] as string[] }
  if (fs.existsSync(ALERT_PATH)) {
    try { state = JSON.parse(fs.readFileSync(ALERT_PATH, 'utf8')) } catch {}
  }
  if (!state.dismissed_ids.includes(id)) state.dismissed_ids.push(id)

  const tmp = ALERT_PATH + '.tmp'
  fs.mkdirSync(path.dirname(ALERT_PATH), { recursive: true })
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2))
  fs.renameSync(tmp, ALERT_PATH)

  return NextResponse.json({ ok: true })
}
```

- [ ] **Step 2: Create `dashboard/components/AlertBanner.tsx`**

```tsx
// dashboard/components/AlertBanner.tsx
'use client'
import { useEffect, useState } from 'react'

type Alert = { id: string; level: string; title: string; body: string; source: string; timestamp: string }
type AlertState = { alerts: Alert[]; dismissed_ids: string[] }

export default function AlertBanner() {
  const [alertState, setAlertState] = useState<AlertState>({ alerts: [], dismissed_ids: [] })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`/alert_state.json?t=${Date.now()}`)
        if (r.ok) setAlertState(await r.json())
      } catch {}
    }
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [])

  const visible = alertState.alerts.filter(a =>
    !alertState.dismissed_ids.includes(a.id) && ['warning', 'emergency'].includes(a.level)
  )

  if (visible.length === 0) return null

  const dismiss = async (id: string) => {
    await fetch('/api/alerts/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    })
    setAlertState(s => ({ ...s, dismissed_ids: [...s.dismissed_ids, id] }))
  }

  const toggleExpand = (id: string) =>
    setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  return (
    <div className="space-y-0">
      {visible.map(a => (
        <div key={a.id} className="flex items-start gap-3 px-4 py-2 bg-red-950 border-b border-red-800 text-sm">
          <span className={`px-1.5 py-0.5 rounded text-xs font-bold shrink-0 ${a.level === 'emergency' ? 'bg-red-600' : 'bg-amber-600'}`}>
            {a.level.toUpperCase()}
          </span>
          <div className="flex-1 min-w-0">
            <span className="font-medium text-red-200">{a.title}</span>
            <span className="ml-2 text-xs text-red-400">{a.source} · {new Date(a.timestamp).toLocaleTimeString()}</span>
            {expanded.has(a.id) && <p className="mt-1 text-red-300 text-xs whitespace-pre-wrap">{a.body}</p>}
          </div>
          <button onClick={() => toggleExpand(a.id)} className="text-xs text-red-400 hover:text-red-200 shrink-0">
            {expanded.has(a.id) ? 'Hide' : 'Details'}
          </button>
          <button onClick={() => dismiss(a.id)} className="text-red-400 hover:text-red-200 shrink-0 text-lg leading-none">×</button>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Add `AlertBanner` to `layout.tsx` above the badge/nav bar**

```tsx
import AlertBanner from '@/components/AlertBanner'

// At the very top of the body content, before everything else:
<AlertBanner />
```

- [ ] **Step 4: Verify — create a dummy `alert_state.json` in `dashboard/public/`**

```bash
cat > dashboard/public/alert_state.json << 'EOF'
{
  "alerts": [{"id":"test-1","level":"emergency","title":"Test Alert","body":"This is a test","source":"test","timestamp":"2026-05-08T10:00:00Z"}],
  "dismissed_ids": []
}
EOF
```

Open `http://localhost:3000` — red banner should appear at top. Click Details, then ×.

- [ ] **Step 5: Clean up test file and commit**

```bash
rm dashboard/public/alert_state.json
git add dashboard/app/api/alerts/ dashboard/components/AlertBanner.tsx dashboard/app/layout.tsx
git commit -m "feat: add AlertBanner, dismiss API, and alert_state.json persistence"
```

---

### Task 15: System log API route and `/log` page

**Files:**
- Create: `dashboard/app/api/log/route.ts`
- Create: `dashboard/app/log/page.tsx`
- Modify: `dashboard/components/NavBar.tsx`

- [ ] **Step 1: Create `dashboard/app/api/log/route.ts`**

```typescript
// dashboard/app/api/log/route.ts
import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'

const LOG_PATH = path.join(BOT_ROOT, 'data', 'system_log.json')

export async function GET() {
  if (!fs.existsSync(LOG_PATH)) return NextResponse.json([])
  try {
    const entries = JSON.parse(fs.readFileSync(LOG_PATH, 'utf8'))
    return NextResponse.json(entries.reverse()) // newest first
  } catch {
    return NextResponse.json([])
  }
}
```

- [ ] **Step 2: Create `dashboard/app/log/page.tsx`**

```tsx
// dashboard/app/log/page.tsx
'use client'
import { useEffect, useState } from 'react'

type LogEntry = { id: string; timestamp: string; level: string; title: string; detail: string; source: string }

const LEVEL_STYLE: Record<string, string> = {
  info: 'bg-slate-600 text-slate-200',
  warning: 'bg-amber-600 text-white',
  emergency: 'bg-red-600 text-white',
}

const LEVELS = ['info', 'warning', 'emergency']

export default function LogPage() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [levels, setLevels] = useState<Set<string>>(new Set(LEVELS))
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    // Mark as read
    localStorage.setItem('log_last_read', new Date().toISOString())
    fetch('/api/log').then(r => r.json()).then(setEntries).catch(() => {})
  }, [])

  const toggleLevel = (l: string) =>
    setLevels(s => { const n = new Set(s); n.has(l) ? n.delete(l) : n.add(l); return n })

  const toggleExpand = (id: string) =>
    setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  const visible = entries.filter(e => levels.has(e.level))

  return (
    <main className="max-w-5xl mx-auto px-4 py-6">
      <h1 className="text-xl font-semibold mb-4">System Log</h1>

      <div className="flex gap-2 mb-4">
        {LEVELS.map(l => (
          <button key={l} onClick={() => toggleLevel(l)}
            className={`px-3 py-1 rounded text-xs font-bold border ${levels.has(l) ? LEVEL_STYLE[l] : 'border-slate-700 text-slate-500'}`}>
            {l.toUpperCase()}
          </button>
        ))}
      </div>

      <div className="space-y-0 border border-slate-800 rounded overflow-hidden">
        {visible.length === 0 && (
          <div className="px-4 py-8 text-center text-slate-500">No entries</div>
        )}
        {visible.map((e, i) => (
          <div key={e.id} className={`px-4 py-2 text-sm border-b border-slate-800 ${i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900'}`}>
            <div className="flex items-center gap-3">
              <span className={`px-1.5 py-0.5 rounded text-xs font-bold shrink-0 ${LEVEL_STYLE[e.level] ?? ''}`}>
                {e.level.toUpperCase()}
              </span>
              <span className="text-slate-400 text-xs shrink-0">{new Date(e.timestamp).toLocaleString()}</span>
              <span className="text-slate-300 flex-1 truncate">{e.title}</span>
              <span className="text-slate-500 text-xs shrink-0">{e.source}</span>
              {e.detail && (
                <button onClick={() => toggleExpand(e.id)}
                  className="text-xs text-slate-400 hover:text-slate-200 shrink-0">
                  {expanded.has(e.id) ? '▲' : '▼'}
                </button>
              )}
            </div>
            {expanded.has(e.id) && e.detail && (
              <pre className="mt-2 text-xs text-slate-400 whitespace-pre-wrap pl-2 border-l border-slate-700">{e.detail}</pre>
            )}
          </div>
        ))}
      </div>
    </main>
  )
}
```

- [ ] **Step 3: Add Log link with unread badge to `NavBar.tsx`**

In `dashboard/components/NavBar.tsx`, add:

```tsx
'use client'
import { useEffect, useState } from 'react'

// Inside NavBar component:
const [unread, setUnread] = useState(0)

useEffect(() => {
  const lastRead = localStorage.getItem('log_last_read')
  fetch('/api/log').then(r => r.json()).then((entries: LogEntry[]) => {
    if (!lastRead) { setUnread(entries.filter(e => ['warning','emergency'].includes(e.level)).length); return }
    const count = entries.filter(e =>
      ['warning','emergency'].includes(e.level) && new Date(e.timestamp) > new Date(lastRead)
    ).length
    setUnread(count)
  }).catch(() => {})
}, [])

// In the nav link list:
<Link href="/log" className="...">
  Log
  {unread > 0 && (
    <span className="ml-1.5 inline-flex items-center justify-center w-4 h-4 text-xs bg-red-600 text-white rounded-full">
      {unread > 9 ? '9+' : unread}
    </span>
  )}
</Link>
```

- [ ] **Step 4: Verify log page renders at `http://localhost:3000/log`**

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/api/log/ dashboard/app/log/ dashboard/components/NavBar.tsx
git commit -m "feat: add system log page, /api/log route, and unread badge on nav"
```

---

### Task 16: Settings page — Telegram section and UI Preview

**Files:**
- Modify: `dashboard/app/settings/page.tsx`

- [ ] **Step 1: Add Telegram state and handlers**

```typescript
const [telegram, setTelegram] = useState({ token: '', chat_id: '' })
const [telegramStatus, setTelegramStatus] = useState<'idle'|'testing'|'ok'|'error'>('idle')
const [telegramError, setTelegramError] = useState('')

// Load from risk config on mount (reuse existing risk config fetch if present)
// Add to existing useEffect or create a new one:
useEffect(() => {
  fetch('/api/risk').then(r => r.json()).then(d => {
    if (d.config?.telegram) setTelegram(d.config.telegram)
  }).catch(() => {})
}, [])

const saveTelegram = async () => {
  await fetch('/api/risk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ telegram }),
  })
}

const testTelegram = async () => {
  setTelegramStatus('testing'); setTelegramError('')
  const r = await fetch('/api/telegram/test', { method: 'POST' })
  const d = await r.json()
  setTelegramStatus(d.ok ? 'ok' : 'error')
  if (!d.ok) setTelegramError(d.error || 'Unknown error')
}
```

- [ ] **Step 2: Add Telegram JSX section**

```tsx
<section className="mb-8">
  <h2 className="text-lg font-semibold mb-3">Telegram Alerts</h2>
  <div className="space-y-3 max-w-md">
    <div>
      <label className="block text-xs text-slate-400 mb-1">Bot Token</label>
      <input value={telegram.token} onChange={e => setTelegram(t => ({...t, token: e.target.value}))}
        onBlur={saveTelegram} type="password" placeholder="123456:ABCdef..."
        className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm" />
    </div>
    <div>
      <label className="block text-xs text-slate-400 mb-1">Chat ID</label>
      <input value={telegram.chat_id} onChange={e => setTelegram(t => ({...t, chat_id: e.target.value}))}
        onBlur={saveTelegram} placeholder="123456789"
        className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm" />
    </div>
    <div className="flex items-center gap-3">
      <button onClick={testTelegram} disabled={telegramStatus === 'testing' || !telegram.token || !telegram.chat_id}
        className="px-3 py-1.5 bg-slate-700 rounded text-sm hover:bg-slate-600 disabled:opacity-50">
        {telegramStatus === 'testing' ? 'Sending…' : 'Send test notification'}
      </button>
      {telegramStatus === 'ok' && <span className="text-green-400 text-sm">✓ Sent</span>}
      {telegramStatus === 'error' && <span className="text-red-400 text-sm">✗ {telegramError}</span>}
    </div>
    <p className="text-xs text-slate-500">See <code>TELEGRAM_SETUP.md</code> for setup instructions.</p>
  </div>
</section>
```

- [ ] **Step 3: Add UI Preview section**

```tsx
const [preview, setPreview] = useState({ liveRunning: false, testRunning: false, emergency: false })

<section className="mb-8">
  <h2 className="text-lg font-semibold mb-3">UI Preview</h2>
  <div className="space-y-2">
    {[
      ['liveRunning', 'Imitate live mode running'],
      ['testRunning', 'Imitate test mode running'],
      ['emergency', 'Imitate emergency notice'],
    ].map(([key, label]) => (
      <label key={key} className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox"
          checked={preview[key as keyof typeof preview]}
          onChange={e => setPreview(p => ({...p, [key]: e.target.checked}))}
          className="rounded" />
        <span className="text-sm text-slate-300">{label}</span>
      </label>
    ))}
  </div>
  {(preview.liveRunning || preview.testRunning || preview.emergency) && (
    <div className="mt-4 p-3 border border-slate-700 rounded space-y-2">
      {(preview.liveRunning || preview.testRunning) && (
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-mono font-bold border
            ${preview.liveRunning ? 'border-amber-500 text-amber-400' : 'border-slate-600 text-slate-400'}`}>
            {preview.liveRunning ? '● LIVE · RUNNING' : 'TEST · RUNNING'}
          </span>
          <span className="text-xs text-slate-500">Badge preview</span>
        </div>
      )}
      {preview.emergency && (
        <div className="flex items-center gap-2 px-3 py-2 bg-red-950 border border-red-800 rounded text-sm">
          <span className="px-1.5 py-0.5 rounded text-xs font-bold bg-red-600">EMERGENCY</span>
          <span className="text-red-200">Sample emergency alert · main · 10:00:00</span>
        </div>
      )}
    </div>
  )}
</section>
```

- [ ] **Step 4: Create `/api/telegram/test` route**

```typescript
// dashboard/app/api/telegram/test/route.ts
import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'

const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')
import crypto from 'crypto'

export async function POST() {
  // For now: write a test_telegram command and wait for result
  // The bot's notifier handles the actual Telegram send
  const id = crypto.randomUUID()
  const tmp = COMMAND_PATH + '.tmp'
  fs.mkdirSync(path.dirname(COMMAND_PATH), { recursive: true })
  fs.writeFileSync(tmp, JSON.stringify({
    id, type: 'test_telegram', payload: {}, issued_at: new Date().toISOString()
  }))
  fs.renameSync(tmp, COMMAND_PATH)

  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const result = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (result.id === id) return NextResponse.json(result)
    } catch {}
  }
  return NextResponse.json({ ok: false, error: 'Bot not responding (is it running?)' }, { status: 504 })
}
```

Add `test_telegram` command handling to `ModeManager.poll_loop()`:

```python
elif cmd_type == "test_telegram":
    ok, error = self._notifier.send_test()
    self._write_result(cmd_id, ok=ok, error=error)
```

And pass notifier into `ModeManager` constructor:

```python
def __init__(self, ..., notifier: 'Notifier | None' = None):
    ...
    self._notifier = notifier
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/settings/page.tsx dashboard/app/api/telegram/
git commit -m "feat: add Telegram config section, test button, and UI Preview to Settings page"
```

---

## Phase 6 — Order Execution

### Task 17: `bot/order_executor.py` — state machine and `close_all_orders_at_market()`

**Files:**
- Create: `bot/order_executor.py`
- Create: `tests/test_order_executor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_order_executor.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bot.order_executor import OrderExecutor, OrderState

def make_executor(mode='test'):
    settings = MagicMock()
    settings.trading_mode = mode
    risk = MagicMock()
    risk.can_open_sync.return_value = True
    notifier = MagicMock()
    notifier.notify = MagicMock()
    return OrderExecutor(mode=mode, settings=settings, risk_manager=risk, notifier=notifier)

def test_initial_state_is_idle():
    ex = make_executor()
    assert ex.get_state('BTCUSDT') == OrderState.IDLE

def test_consecutive_failure_increments():
    ex = make_executor()
    ex._record_failure('BTCUSDT')
    ex._record_failure('BTCUSDT')
    assert ex._failure_counts['BTCUSDT'] == 2

def test_failure_reset_on_success():
    ex = make_executor()
    ex._record_failure('BTCUSDT')
    ex._record_success('BTCUSDT')
    assert ex._failure_counts['BTCUSDT'] == 0

@pytest.mark.asyncio
async def test_close_all_returns_list():
    ex = make_executor()
    # No open orders — should return empty list without error
    result = await ex.close_all_orders_at_market()
    assert isinstance(result, list)

def test_threshold_fires_notifier():
    ex = make_executor()
    ex._consecutive_failure_threshold = 2
    ex._record_failure('BTCUSDT')
    ex._record_failure('BTCUSDT')
    ex._notifier.notify.assert_called()
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: `ERROR` — ModuleNotFoundError.

- [ ] **Step 3: Implement `bot/order_executor.py`**

```python
# bot/order_executor.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Literal

from config.settings import Settings
from bot.risk_manager import RiskManager
from bot.notifier import Notifier

logger = logging.getLogger(__name__)


class OrderState(Enum):
    IDLE = auto()
    PLACING = auto()
    OPEN = auto()
    PARTIAL_EXIT = auto()
    CLOSED = auto()


@dataclass
class OpenOrder:
    symbol: str
    preset_name: str
    side: str  # BUY | SELL
    entry_price: float
    tp_price: float
    sl_price: float
    quantity: float
    leverage: int
    exchange_order_id: str | None = None


class OrderExecutor:
    """
    Unified order execution for test and live modes.
    Mode injected at construction — logic is identical, only exchange calls differ.
    """

    PLACING_TIMEOUT = 30.0  # seconds

    def __init__(
        self,
        mode: Literal["test", "live"],
        settings: Settings,
        risk_manager: RiskManager,
        notifier: Notifier,
    ) -> None:
        self._mode = mode
        self._settings = settings
        self._risk_manager = risk_manager
        self._notifier = notifier

        self._states: dict[str, OrderState] = {}
        self._open_orders: dict[str, OpenOrder] = {}
        self._placing_locks: dict[str, asyncio.Lock] = {}
        self._failure_counts: dict[str, int] = {}

        from config.risk_config import load_risk_config
        cfg = load_risk_config()
        self._consecutive_failure_threshold: int = cfg.get("consecutive_failure_threshold", 3)

    # ------------------------------------------------------------------ #
    # State queries                                                        #
    # ------------------------------------------------------------------ #

    def get_state(self, symbol: str) -> OrderState:
        return self._states.get(symbol, OrderState.IDLE)

    def get_open_orders(self) -> dict[str, OpenOrder]:
        return dict(self._open_orders)

    def get_unrealised_pnl(self, current_prices: dict[str, float]) -> float:
        total = 0.0
        for symbol, order in self._open_orders.items():
            price = current_prices.get(symbol, order.entry_price)
            if order.side == 'BUY':
                pnl = (price - order.entry_price) / order.entry_price * 100 * order.quantity * order.entry_price
            else:
                pnl = (order.entry_price - price) / order.entry_price * 100 * order.quantity * order.entry_price
            total += pnl
        return total

    # ------------------------------------------------------------------ #
    # Placement                                                            #
    # ------------------------------------------------------------------ #

    def _get_placing_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._placing_locks:
            self._placing_locks[symbol] = asyncio.Lock()
        return self._placing_locks[symbol]

    async def place_order(self, symbol: str, preset_name: str, side: str,
                          entry: float, tp: float, sl: float,
                          quantity: float, leverage: int) -> bool:
        lock = self._get_placing_lock(symbol)
        async with lock:
            self._states[symbol] = OrderState.PLACING
            try:
                order_id = await self._submit_to_exchange(symbol, side, quantity, tp, sl, leverage)
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name=preset_name, side=side,
                    entry_price=entry, tp_price=tp, sl_price=sl,
                    quantity=quantity, leverage=leverage, exchange_order_id=order_id,
                )
                self._states[symbol] = OrderState.OPEN
                self._record_success(symbol)
                logger.info(f"Order placed: {symbol} {side} @ {entry} TP={tp} SL={sl}")
                return True
            except Exception as exc:
                self._states[symbol] = OrderState.IDLE
                self._record_failure(symbol)
                logger.error(f"Order placement failed for {symbol}: {exc}")
                return False

    async def _submit_to_exchange(self, symbol: str, side: str, quantity: float,
                                   tp: float, sl: float, leverage: int) -> str | None:
        """
        In live and test modes: calls the appropriate Binance API endpoint.
        Returns the exchange order ID or None.
        """
        # TODO in Task 19: wire real Binance client here
        # Stub: return None (simulated fill)
        return None

    # ------------------------------------------------------------------ #
    # Close all orders                                                     #
    # ------------------------------------------------------------------ #

    async def close_all_orders_at_market(self) -> list[dict]:
        """
        Close all open orders at current market price.
        Returns list of closed order summaries.
        Continues on per-symbol errors — logs and fires warning, does not abort.
        """
        results = []
        for symbol, order in list(self._open_orders.items()):
            try:
                close_price = await self._market_close(symbol, order)
                pnl = self._calc_pnl(order, close_price)
                results.append({
                    "symbol": symbol,
                    "side": order.side,
                    "entry_price": order.entry_price,
                    "close_price": close_price,
                    "pnl_usdt": pnl,
                })
                del self._open_orders[symbol]
                self._states[symbol] = OrderState.IDLE
                logger.info(f"Closed {symbol} at market: entry={order.entry_price} close={close_price} pnl={pnl:.2f}")
            except Exception as exc:
                logger.error(f"Failed to close {symbol} at market: {exc}")
                self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
        return results

    async def _market_close(self, symbol: str, order: OpenOrder) -> float:
        """Submit market close to exchange and return fill price."""
        # TODO in Task 19: wire real Binance client
        # Stub: return entry price (no-op close)
        return order.entry_price

    @staticmethod
    def _calc_pnl(order: OpenOrder, close_price: float) -> float:
        if order.side == 'BUY':
            return (close_price - order.entry_price) * order.quantity
        return (order.entry_price - close_price) * order.quantity

    # ------------------------------------------------------------------ #
    # Failure tracking                                                     #
    # ------------------------------------------------------------------ #

    def _record_failure(self, symbol: str) -> None:
        self._failure_counts[symbol] = self._failure_counts.get(symbol, 0) + 1
        if self._failure_counts[symbol] >= self._consecutive_failure_threshold:
            self._notifier.notify(
                "emergency",
                f"Order placement threshold reached: {symbol}",
                f"{self._failure_counts[symbol]} consecutive failures",
                "order_executor",
            )

    def _record_success(self, symbol: str) -> None:
        self._failure_counts[symbol] = 0
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: add order_executor.py — unified order state machine and close_all_orders_at_market"
```

---

### Task 18: `bot/symbol_registry.py` — disable/enable with weight redistribution

**Files:**
- Modify: `bot/symbol_registry.py`
- Create: `tests/test_symbol_registry_disable.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_symbol_registry_disable.py
import json
from pathlib import Path
from bot.symbol_registry import SymbolRegistry

def _make_registry(tmp_path, symbols):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "symbols": symbols,
        "weights": {s: 1.0 / len(symbols) for s in symbols},
        "disabled": {},
    }))
    return SymbolRegistry(path)

def test_disable_marks_symbol(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    reg.disable("BTCUSDT", reason="not tradeable")
    assert reg.is_disabled("BTCUSDT")
    assert not reg.is_disabled("ETHUSDT")

def test_disable_redistributes_weight(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    original_weight = reg.get_weight("BTCUSDT")
    reg.disable("BTCUSDT", reason="test")
    # Remaining two symbols should share the weight
    assert abs(reg.get_weight("ETHUSDT") + reg.get_weight("SOLUSDT") - 1.0) < 0.001
    assert reg.get_weight("BTCUSDT") == 0.0

def test_reenable_restores_equal_split(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.disable("BTCUSDT", reason="test")
    reg.reenable("BTCUSDT")
    assert not reg.is_disabled("BTCUSDT")
    assert abs(reg.get_weight("BTCUSDT") - 0.5) < 0.001

def test_all_disabled_returns_true(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.disable("BTCUSDT", reason="a")
    reg.disable("ETHUSDT", reason="b")
    assert reg.all_disabled()
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_symbol_registry_disable.py -v
```

- [ ] **Step 3: Add disable/enable methods to `bot/symbol_registry.py`**

Read the existing `SymbolRegistry` class first, then add:

```python
def is_disabled(self, symbol: str) -> bool:
    return symbol in self._data.get("disabled", {})

def disable(self, symbol: str, reason: str) -> None:
    from datetime import datetime, timezone
    if "disabled" not in self._data:
        self._data["disabled"] = {}
    self._data["disabled"][symbol] = {
        "reason": reason,
        "disabled_at": datetime.now(timezone.utc).isoformat(),
    }
    # Zero out this symbol's weight and redistribute
    weights = self._data.get("weights", {})
    lost_weight = weights.get(symbol, 0.0)
    weights[symbol] = 0.0
    active = [s for s in weights if s != symbol and weights[s] > 0 and not self.is_disabled(s)]
    if active:
        per_symbol = lost_weight / len(active)
        for s in active:
            weights[s] += per_symbol
    self._data["weights"] = weights
    self._save()

def reenable(self, symbol: str) -> None:
    disabled = self._data.get("disabled", {})
    disabled.pop(symbol, None)
    self._data["disabled"] = disabled
    # Redistribute weights equally among all active symbols
    active = [s for s in self._data.get("symbols", []) if not self.is_disabled(s)]
    if active:
        per = 1.0 / len(active)
        for s in active:
            self._data.setdefault("weights", {})[s] = per
    self._save()

def get_weight(self, symbol: str) -> float:
    return self._data.get("weights", {}).get(symbol, 0.0)

def get_disabled(self) -> dict:
    return dict(self._data.get("disabled", {}))

def all_disabled(self) -> bool:
    symbols = self._data.get("symbols", [])
    return all(self.is_disabled(s) for s in symbols) and len(symbols) > 0
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_symbol_registry_disable.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/symbol_registry.py tests/test_symbol_registry_disable.py
git commit -m "feat: add disable/reenable/weight-redistribution to SymbolRegistry"
```

---

### Task 19: `bot/virtual_tracker.py` — virtual orders and efficiency stats

**Files:**
- Create: `bot/virtual_tracker.py`
- Create: `tests/test_virtual_tracker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_virtual_tracker.py
import json
from pathlib import Path
from bot.virtual_tracker import VirtualTracker

def _make_tracker(tmp_path, mode='test'):
    return VirtualTracker(
        mode=mode,
        orders_path=tmp_path / f"virtual_orders_{mode}.json",
        efficiency_path=tmp_path / f"preset_efficiency_{mode}.json",
    )

def test_seed_from_backtest(tmp_path):
    # Write minimal backtest results
    bt = tmp_path / "backtest_results_BTCUSDT.json"
    bt.write_text(json.dumps({
        "presets": [
            {"name": "preset_a", "trades": [
                {"profit_pct": 1.0, "profit_usdt": 50.0},
                {"profit_pct": -0.5, "profit_usdt": -25.0},
                {"profit_pct": 2.0, "profit_usdt": 100.0},
                {"profit_pct": 0.8, "profit_usdt": 40.0},
            ]}
        ]
    }))
    tracker = _make_tracker(tmp_path)
    tracker.seed_from_backtest("BTCUSDT", tmp_path / "backtest_results_BTCUSDT.json")
    eff = tracker.get_efficiency("BTCUSDT", "preset_a")
    assert eff["total_winning_usdt"] == 190.0  # 50 + 100 + 40
    assert eff["trade_count"] == 4

def test_best_preset_selection(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "slow", total_winning=100.0, count=5)
    tracker._set_efficiency("BTCUSDT", "fast", total_winning=250.0, count=6)
    tracker._set_efficiency("BTCUSDT", "too_few", total_winning=999.0, count=2)
    best = tracker.best_preset("BTCUSDT")
    assert best == "fast"  # highest winning, meets min trades

def test_record_closed_trade(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=100.0, count=4)
    tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=50.0)
    eff = tracker.get_efficiency("BTCUSDT", "p1")
    assert eff["total_winning_usdt"] == 150.0
    assert eff["trade_count"] == 5

def test_no_best_preset_when_below_min_trades(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=999.0, count=2)
    assert tracker.best_preset("BTCUSDT") is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_virtual_tracker.py -v
```

- [ ] **Step 3: Implement `bot/virtual_tracker.py`**

```python
# bot/virtual_tracker.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_MIN_TRADES = 4


class VirtualTracker:
    """
    Tracks virtual order efficiency per (symbol, preset).
    Persists to data/preset_efficiency_{mode}.json.
    """

    def __init__(
        self,
        mode: Literal["test", "live"],
        orders_path: Path,
        efficiency_path: Path,
    ) -> None:
        self._mode = mode
        self._orders_path = orders_path
        self._efficiency_path = efficiency_path
        self._efficiency: dict = self._load_efficiency()

    # ------------------------------------------------------------------ #
    # Seeding                                                              #
    # ------------------------------------------------------------------ #

    def seed_from_backtest(self, symbol: str, backtest_path: Path) -> None:
        """Populate initial efficiency values from backtest results file."""
        if not backtest_path.exists():
            logger.warning(f"No backtest file for {symbol}: {backtest_path}")
            return
        try:
            data = json.loads(backtest_path.read_text())
            for preset_data in data.get("presets", []):
                name = preset_data.get("name", "")
                trades = preset_data.get("trades", [])
                winning_usdt = sum(t.get("profit_usdt", 0.0) for t in trades if t.get("profit_usdt", 0.0) > 0)
                self._set_efficiency(symbol, name, total_winning=winning_usdt, count=len(trades))
        except Exception as exc:
            logger.error(f"Failed to seed efficiency for {symbol}: {exc}")

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    def best_preset(self, symbol: str) -> str | None:
        symbol_data = self._efficiency.get(symbol, {})
        eligible = {
            name: stats for name, stats in symbol_data.items()
            if stats.get("trade_count", 0) >= _MIN_TRADES
        }
        if not eligible:
            return None
        return max(eligible, key=lambda n: eligible[n].get("total_winning_usdt", 0.0))

    def get_efficiency(self, symbol: str, preset: str) -> dict:
        return self._efficiency.get(symbol, {}).get(preset, {"total_winning_usdt": 0.0, "trade_count": 0})

    # ------------------------------------------------------------------ #
    # Updates                                                              #
    # ------------------------------------------------------------------ #

    def record_closed_trade(self, symbol: str, preset: str, profit_usdt: float) -> None:
        eff = self.get_efficiency(symbol, preset)
        new_winning = eff["total_winning_usdt"] + (profit_usdt if profit_usdt > 0 else 0.0)
        self._set_efficiency(symbol, preset, total_winning=new_winning, count=eff["trade_count"] + 1)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _set_efficiency(self, symbol: str, preset: str, total_winning: float, count: int) -> None:
        self._efficiency.setdefault(symbol, {})[preset] = {
            "total_winning_usdt": total_winning,
            "trade_count": count,
        }
        self._save_efficiency()

    def _load_efficiency(self) -> dict:
        if self._efficiency_path.exists():
            try:
                return json.loads(self._efficiency_path.read_text())
            except Exception:
                pass
        return {}

    def _save_efficiency(self) -> None:
        self._efficiency_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._efficiency_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._efficiency, indent=2))
        tmp.replace(self._efficiency_path)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_virtual_tracker.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/virtual_tracker.py tests/test_virtual_tracker.py
git commit -m "feat: add virtual_tracker.py — per-(symbol,preset) efficiency stats and seed-from-backtest"
```

---

## Phase 7 — Integration & Cleanup

### Task 20: `bot/risk_manager.py` — rename `"paper"` → `"test"`, add `min_balance` check

**Files:**
- Modify: `bot/risk_manager.py`
- Modify: `tests/test_risk_manager.py`

- [ ] **Step 1: Replace all `"paper"` mode references**

In `bot/risk_manager.py`, update the `Literal` type hint and any `if self._mode == "paper"` checks:

```python
# Before:
mode: Literal["backtest", "paper", "live"]
# After:
mode: Literal["backtest", "test", "live"]
```

Search for any `"paper"` string literals in the file and replace with `"test"`. Run:

```bash
grep -n '"paper"' bot/risk_manager.py
```

Replace each occurrence.

- [ ] **Step 2: Add `min_balance_usdt` check to `update_balance()`**

Find the `update_balance()` method and add after the existing drawdown check:

```python
# After existing drawdown logic:
min_balance = self._config.get("min_balance_usdt", 0.0)
if min_balance > 0 and self._balance < min_balance:
    self._pending_notify = ("emergency",
        f"Balance below floor: {self._balance:.2f} USDT (floor: {min_balance:.2f})",
        "risk_manager")
```

Then wire `_pending_notify` to `self._notifier.notify(...)` in the `notify()` call site (already exists in the class — just add the new condition).

- [ ] **Step 3: Accept optional `Notifier` in constructor**

```python
def __init__(self, mode, ..., notifier: 'Notifier | None' = None):
    ...
    self._notifier = notifier
```

Wire `_pending_notify` discharge to use `self._notifier` when available.

- [ ] **Step 4: Update tests — replace `"paper"` with `"test"`**

```bash
sed -i 's/"paper"/"test"/g' tests/test_risk_manager.py
python -m pytest tests/test_risk_manager.py -v
```

Expected: all 17 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: rename paper→test in RiskManager, add min_balance_usdt check"
```

---

### Task 21: Delete paper trader and clean up stale files

**Files:**
- Delete: `bot/paper_trader.py`
- Delete: `paper_trade.py`
- Delete: `dashboard/app/paper/` (directory)
- Delete: `dashboard/public/paper_results_*.json` (if any)

- [ ] **Step 1: Delete Python files**

```bash
git rm bot/paper_trader.py paper_trade.py
```

- [ ] **Step 2: Delete dashboard paper page**

```bash
git rm -r dashboard/app/paper/
```

- [ ] **Step 3: Remove Paper nav link from `NavBar.tsx`**

Open `dashboard/components/NavBar.tsx`, find and remove the `/paper` link.

- [ ] **Step 4: Delete stale public JSON files**

```bash
ls dashboard/public/paper_results_*.json 2>/dev/null && git rm dashboard/public/paper_results_*.json || echo "none"
ls dashboard/public/paper_state_*.json 2>/dev/null && git rm dashboard/public/paper_state_*.json || echo "none"
```

- [ ] **Step 5: Run all tests to confirm nothing broken**

```bash
python -m pytest tests/ -v
```

Expected: all pass (paper_trader was not imported by any remaining test).

- [ ] **Step 6: Commit**

```bash
git commit -m "chore: delete paper_trader.py, paper_trade.py, and /paper dashboard page"
```

---

### Task 22: Wire everything into `main.py`

**Files:**
- Modify: `main.py`

This is the integration task. The existing `main.py` already has the bot loop structure — we're adding the new components.

- [ ] **Step 1: Add imports at the top of `main.py`**

```python
from bot.mode_manager import ModeManager
from bot.notifier import Notifier
from bot.order_executor import OrderExecutor
from bot.virtual_tracker import VirtualTracker
from config.risk_config import load_risk_config
```

- [ ] **Step 2: Instantiate shared components in `async_main()`**

```python
risk_cfg = load_risk_config()
notifier = Notifier(
    log_path=Path("data/system_log.json"),
    alert_path=Path("dashboard/public/alert_state.json"),
    telegram_token=risk_cfg.get("telegram", {}).get("token", ""),
    telegram_chat_id=risk_cfg.get("telegram", {}).get("chat_id", ""),
)
mode_manager = ModeManager(notifier=notifier)
current_mode = mode_manager.current_mode

risk_manager = RiskManager(
    mode=current_mode,
    initial_balance=risk_cfg.get("test_starting_balance_usdt", 10000.0),
    notifier=notifier,
)
order_executor = OrderExecutor(
    mode=current_mode,
    settings=settings,
    risk_manager=risk_manager,
    notifier=notifier,
)
virtual_tracker = VirtualTracker(
    mode=current_mode,
    orders_path=Path(f"data/virtual_orders_{current_mode}.json"),
    efficiency_path=Path(f"data/preset_efficiency_{current_mode}.json"),
)
```

- [ ] **Step 3: Wire mode_manager callbacks**

```python
async def on_switch_mode(target_mode: str) -> None:
    await order_executor.close_all_orders_at_market()
    # Re-initialise data feed, run backtest, reseed
    settings_new = load_settings()
    data_feed.reinit(target_mode, settings_new.api_key, settings_new.api_secret)
    import subprocess
    subprocess.run(["python", "backtest.py", "--mode", target_mode], check=True)
    # Reload virtual tracker for new mode
    virtual_tracker.__init__(
        mode=target_mode,
        orders_path=Path(f"data/virtual_orders_{target_mode}.json"),
        efficiency_path=Path(f"data/preset_efficiency_{target_mode}.json"),
    )
    notifier.notify("info", f"Mode switched to {target_mode}", "", "mode_manager")

async def on_stop_bot() -> None:
    await order_executor.close_all_orders_at_market()
    write_bot_state(running=False, mode=current_mode, started_at=started_at)
    notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
    sys.exit(0)
```

- [ ] **Step 4: Add obligatory startup backtest before starting workers**

```python
# After write_pid() and before starting symbol workers:
notifier.notify("info", "Running obligatory backtest", f"mode={current_mode}", "main")
import subprocess
result = subprocess.run(["python", "backtest.py", "--mode", current_mode], capture_output=True)
if result.returncode != 0:
    notifier.notify("emergency", "Obligatory backtest failed — cannot start", result.stderr.decode()[:500], "main")
    sys.exit(1)

# Seed virtual tracker from fresh backtest results
for symbol in symbols:
    bt_path = Path(f"dashboard/public/backtest_results_{symbol}.json")
    virtual_tracker.seed_from_backtest(symbol, bt_path)

notifier.notify("info", "Startup sequence complete", f"{len(symbols)} symbols active", "main")
```

- [ ] **Step 5: Add poll loop and heartbeat to asyncio.gather**

```python
await asyncio.gather(
    # ... existing tasks ...
    mode_manager.poll_loop(on_switch_mode=on_switch_mode, on_stop_bot=on_stop_bot),
    heartbeat_loop(current_mode, started_at, lambda: (len(symbols), 0)),
)
```

- [ ] **Step 6: Add `test_telegram` command handler to ModeManager**

In `bot/mode_manager.py` `poll_loop`, add:

```python
elif cmd_type == "test_telegram":
    if self._notifier:
        ok, error = self._notifier.send_test()
        self._write_result(cmd_id, ok=ok, error=error)
    else:
        self._write_result(cmd_id, ok=False, error="Notifier not configured")
```

- [ ] **Step 7: Run the bot briefly to verify startup**

```bash
python main.py 2>&1 | head -30
```

Expected: sees "Running obligatory backtest", "Startup sequence complete", then begins normal loop. No exceptions.

- [ ] **Step 8: Commit**

```bash
git add main.py bot/mode_manager.py
git commit -m "feat: wire mode_manager, notifier, order_executor, virtual_tracker into main.py"
```

---

### Task 23: `TELEGRAM_SETUP.md`

**Files:**
- Create: `TELEGRAM_SETUP.md`

- [ ] **Step 1: Create the guide**

```markdown
# Telegram Bot Setup

## Step 1 — Create a bot via BotFather

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Choose a name (e.g. "My Trading Bot Alerts") and a username ending in `bot` (e.g. `mytrading_alerts_bot`).
4. BotFather replies with your **bot token** — a string like `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Copy it.

## Step 2 — Find your Chat ID

1. Start a conversation with your new bot: search for its username and click Start.
2. Send any message to the bot (e.g. "hello").
3. Open this URL in your browser (replace `<TOKEN>` with your bot token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Find `"chat": { "id": 123456789 }` in the response. That number is your **chat ID**.

## Step 3 — Enter in dashboard

1. Open the dashboard → Settings page → Telegram Alerts section.
2. Paste your bot token and chat ID.
3. Click **Send test notification**.
4. You should receive a message from your bot.

## Troubleshooting

- **No getUpdates response?** Make sure you sent a message to the bot first.
- **401 Unauthorized?** Double-check the token — include the full string including the colon.
- **Chat not found?** Ensure you sent the bot a message before calling getUpdates.
```

- [ ] **Step 2: Commit**

```bash
git add TELEGRAM_SETUP.md
git commit -m "docs: add TELEGRAM_SETUP.md with step-by-step bot creation guide"
```

---

### Task 24: Update `.gitignore`, `CLAUDE_NOTES.md`, and `TODO.md`

**Files:**
- Modify: `.gitignore`
- Modify: `CLAUDE_NOTES.md`
- Modify: `TODO.md`

- [ ] **Step 1: Verify `.gitignore` includes all new runtime files**

Confirm the block added in Task 4 Step 6 is present:

```
# Bot runtime state
data/bot_pid.json
data/bot_mode.json
data/bot_command.json
data/bot_command_result.json
data/system_log.json
data/preset_efficiency_test.json
data/preset_efficiency_live.json
data/virtual_orders_test.json
data/virtual_orders_live.json
dashboard/public/bot_state.json
dashboard/public/alert_state.json
```

- [ ] **Step 2: Add a new session entry to `CLAUDE_NOTES.md`**

Append a summary of the new architecture: new modules, mode model (test=testnet, live=fapi), command channel, obligatory backtest gate, deleted paper trader, new data files.

- [ ] **Step 3: Mark completed items and add new ones in `TODO.md`**

Mark as done:
- `[x]` Paper trader replaced by OrderExecutor  
- `[x]` Mode switching with obligatory backtest gate  
- `[x]` Emergency alerting + Telegram  
- `[x]` System log page  
- `[x]` Start Bot / Stop Bot controls  
- `[x]` Virtual order efficiency tracking  
- `[x]` Symbol disable flow  

Add as pending:
- `[ ]` Wire real Binance API calls into `order_executor._submit_to_exchange()` and `_market_close()`
- `[ ]` Combined WebSocket stream (all symbols on one WS connection)
- `[ ]` Price feed fallback (REST polling when WS silent >15s)
- `[ ]` Kline gap detection and re-fetch
- `[ ]` Allocation weight step 0.01 + initial rebalance
- `[ ]` Leverage bracket fetch from Binance API
- `[ ]` End-to-end test: start bot from dashboard, switch modes, verify backtest gate fires

- [ ] **Step 4: Commit**

```bash
git add .gitignore CLAUDE_NOTES.md TODO.md
git commit -m "docs: update session notes, TODO, and gitignore for new infrastructure"
```

---

## Self-Review Checklist

- [x] **§0 Architecture audit** — covered in Task 8 (DataFeed reinit) and Task 22 (combined stream noted in TODO)
- [x] **§1 Mode model** — Tasks 7, 8, 9, 10, 11, 12 cover mode state, DataFeed injection, obligatory backtest
- [x] **§2 Command channel** — Tasks 10, 11 (ModeManager poll loop)
- [x] **§3 Bot state** — Task 4 (heartbeat), Task 5 (start/stop routes)
- [x] **§4 Mode switching** — Tasks 11, 12, 13
- [x] **§5 Stop Bot / Start Bot** — Tasks 5, 6
- [x] **§6 Emergency alerting + Telegram** — Tasks 1, 3, 16, 23
- [x] **§7 System log** — Tasks 2, 15
- [x] **§8 Order execution** — Task 17
- [x] **§9 Symbol disable** — Task 18
- [x] **§10 Virtual orders** — Task 19
- [x] **§11 Price/kline reliability** — noted in Task 24 TODO as next work item (combined WS, fallback, gap detection)
- [x] **§12 Allocation updates** — noted in Task 24 TODO
- [x] **§13 Edge cases** — PLACING lock in Task 17, all-disabled check uses `all_disabled()` from Task 18
- [x] **§14/15 File changes** — all files accounted for across tasks

**Gap noted:** §11 (price/kline reliability) and §12 (allocation) are not implemented in this plan — they are the most self-contained subsystems and are listed as next-step TODO items in Task 24. They can be a follow-on plan.

**Type consistency verified:** `OrderState` used in Tasks 17 and 22. `VirtualTracker` constructor args consistent between Tasks 19 and 22. `Notifier` constructor consistent between Tasks 3 and 22.
