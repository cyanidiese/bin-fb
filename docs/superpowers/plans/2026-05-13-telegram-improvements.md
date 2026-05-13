# Telegram Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add trade win/loss Telegram notifications with per-category rate limiting, `@bo_pal` mention on emergencies, and a configurable interval + message-type selector in Settings.

**Architecture:** `Notifier` gains a `_last_sent` dict keyed by category (`"trade"`, `"system"`); emergency always bypasses. A new `notify_trade_close()` method is called from `OrderExecutor._record_real_order_close()`. `send_test(msg_type)` dispatches five sample message types, bypassing rate limits. The interval is stored in `risk_config.json` under `telegram_notify_interval_s`.

**Tech Stack:** Python 3.11, `requests`, Next.js 15 App Router, TypeScript, Tailwind v4.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `config/risk_config.py` | Modify | Add `telegram_notify_interval_s: 120` to `DEFAULT_CONFIG` |
| `dashboard/app/api/risk/route.ts` | Modify | Add `telegram_notify_interval_s: 120` to TS `DEFAULT_CONFIG` |
| `tests/test_notifier.py` | Modify | 8 new tests for rate limit, trade close, mention, send_test |
| `bot/notifier.py` | Rewrite | Rate limit, HTML mode, `notify_trade_close`, extended `send_test` |
| `bot/order_executor.py` | Modify | Call `notify_trade_close` at end of `_record_real_order_close` |
| `main.py` | Modify | Pass `min_interval_s` kwarg to `Notifier` |
| `dashboard/app/api/telegram/test/route.ts` | Modify | Accept `type` body param; dispatch sample messages |
| `dashboard/app/settings/page.tsx` | Modify | Add interval selector + message-type dropdown |

---

## Task 1: Add `telegram_notify_interval_s` to config defaults

**Files:**
- Modify: `config/risk_config.py`
- Modify: `dashboard/app/api/risk/route.ts`

- [ ] **Step 1: Add key to Python DEFAULT_CONFIG**

In `config/risk_config.py`, after the `"use_allocation_weighting": False,` line (currently line 35), add:

```python
    # Telegram rate limiting
    "telegram_notify_interval_s": 120,
```

Full updated `DEFAULT_CONFIG` block (lines 10-36):

```python
DEFAULT_CONFIG: dict = {
    "balance_tiers": [
        {"min_balance_usdt": 0,    "max_deploy_pct": 40, "max_leverage_ceiling": 5},
        {"min_balance_usdt": 1000, "max_deploy_pct": 50, "max_leverage_ceiling": 10},
        {"min_balance_usdt": 5000, "max_deploy_pct": 60, "max_leverage_ceiling": 15},
    ],
    "base_leverage": 2,
    "max_leverage": 20,
    "min_profit_factor": 1.2,
    "drawdown_warning_pct": 10.0,
    "drawdown_hard_stop_pct": 20.0,
    "backtest_initial_balance_usdt": 1000.0,
    "symbol_weights": {},
    # Telegram alerting
    "telegram": {"token": "", "chat_id": ""},
    # Emergency thresholds — keep 15% of balance untouched
    "min_balance_pct": 15.0,
    "consecutive_failure_threshold": 3,
    # Test mode
    "test_starting_balance_usdt": 10000.0,
    # Order execution
    "price_stale_threshold_s": 15,
    # Leverage progression
    "max_leverage_level": 5,
    # Allocation weighting (archived — disabled by default)
    "use_allocation_weighting": False,
    # Telegram rate limiting
    "telegram_notify_interval_s": 120,
}
```

- [ ] **Step 2: Add key to TypeScript DEFAULT_CONFIG**

In `dashboard/app/api/risk/route.ts`, after `use_allocation_weighting: false,` (currently line 23), add:

```typescript
  telegram_notify_interval_s: 120,
```

- [ ] **Step 3: Commit**

```bash
git add config/risk_config.py dashboard/app/api/risk/route.ts
git commit -m "feat: add telegram_notify_interval_s config key (default 120s)"
```

---

## Task 2: Write failing tests for new Notifier behaviour

**Files:**
- Modify: `tests/test_notifier.py`

- [ ] **Step 1: Append 8 new tests to `tests/test_notifier.py`**

Add the following after the last existing test (`test_send_test_no_credentials`):

```python
# ── Rate limiting ───────────────────────────────────────────────────── #

def _make_notifier_with_creds(tmp_path, min_interval_s=120.0):
    return Notifier(
        log_path=tmp_path / "system_log.json",
        alert_path=tmp_path / "alert_state.json",
        telegram_token="tok",
        telegram_chat_id="123",
        min_interval_s=min_interval_s,
    )


def _mock_resp():
    m = MagicMock()
    m.raise_for_status.return_value = None
    return m


def test_rate_limit_drops_second_trade_message(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("BTCUSDT", "BUY", 10.0, 68000.0, 68500.0, "preset_a")
        n.notify_trade_close("ETHUSDT", "SELL", -5.0, 3200.0, 3250.0, "preset_b")
    assert mock_post.call_count == 1


def test_rate_limit_allows_after_interval(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("BTCUSDT", "BUY", 10.0, 68000.0, 68500.0, "preset_a")
        n._last_sent["trade"] = 0.0  # simulate interval elapsed
        n.notify_trade_close("ETHUSDT", "SELL", -5.0, 3200.0, 3250.0, "preset_b")
    assert mock_post.call_count == 2


def test_emergency_bypasses_rate_limit(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify("emergency", "Alert 1", "body", "test")
        n.notify("emergency", "Alert 2", "body", "test")
    assert mock_post.call_count == 2


# ── Message format ──────────────────────────────────────────────────── #

def test_trade_close_win_format(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("BTCUSDT", "BUY", 12.34, 68000.0, 68500.0, "trail_15")
    text = mock_post.call_args[1]["json"]["text"]
    assert "Win" in text
    assert "BTCUSDT" in text
    assert "+12.34" in text
    assert "trail_15" in text


def test_trade_close_loss_format(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify_trade_close("ETHUSDT", "SELL", -5.20, 3200.0, 3220.0, "trail_15")
    text = mock_post.call_args[1]["json"]["text"]
    assert "Loss" in text
    assert "ETHUSDT" in text
    assert "5.20" in text


def test_emergency_includes_mention(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        n.notify("emergency", "Crash", "details", "main")
    text = mock_post.call_args[1]["json"]["text"]
    assert "@bo_pal" in text


# ── send_test ───────────────────────────────────────────────────────── #

def test_send_test_unknown_type(tmp_path):
    n = _make_notifier_with_creds(tmp_path)
    ok, err = n.send_test("foobar")
    assert ok is False
    assert "foobar" in err


def test_send_test_bypasses_rate_limit(tmp_path):
    import time as _time
    n = _make_notifier_with_creds(tmp_path)
    n._last_sent["trade"] = _time.monotonic()  # saturate trade category
    with patch("requests.post", return_value=_mock_resp()) as mock_post:
        ok, _ = n.send_test("trade_win")
    assert ok is True
    assert mock_post.call_count == 1
```

- [ ] **Step 2: Run new tests to confirm they all fail**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_notifier.py -k "rate_limit or trade_close or mention or send_test_unknown or send_test_bypasses" -v 2>&1 | tail -20
```

Expected: multiple ERRORS — `Notifier.__init__` doesn't accept `min_interval_s`, `notify_trade_close` doesn't exist, etc.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_notifier.py
git commit -m "test: add failing tests for notifier rate limit, trade close, mention"
```

---

## Task 3: Implement Notifier changes

**Files:**
- Rewrite: `bot/notifier.py`

- [ ] **Step 1: Replace `bot/notifier.py` with the new implementation**

```python
# bot/notifier.py
from __future__ import annotations

import json
import logging
import time
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
        min_interval_s: float = 120.0,
    ) -> None:
        self._log_path = log_path
        self._alert_path = alert_path
        self._token = telegram_token
        self._chat_id = telegram_chat_id
        self._min_interval_s = min_interval_s
        self._last_sent: dict[str, float] = {}

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
            is_emergency = level == "emergency"
            if is_emergency or self._rate_limit_ok("system"):
                emoji = {"info": "ℹ️", "warning": "⚠️", "emergency": "🚨"}.get(level, "")
                text = f"{emoji} <b>{title}</b>\n{body}"
                try:
                    self._send_telegram(text, mention=is_emergency)
                except Exception as exc:
                    logger.error("Telegram send failed (HTTP error)")
                    try:
                        append_entry(
                            self._log_path, "warning",
                            "Telegram send failed", str(exc), "notifier"
                        )
                    except Exception:
                        pass

    def notify_trade_close(
        self,
        symbol: str,
        side: str,
        pnl_usdt: float,
        entry_price: float,
        close_price: float,
        preset_name: str,
    ) -> None:
        win = pnl_usdt >= 0
        emoji = "✅" if win else "❌"
        result = "Win" if win else "Loss"
        sign = "+" if pnl_usdt >= 0 else ""
        text = (
            f"{emoji} <b>{symbol} {side} — {result}</b>\n"
            f"PnL: <b>{sign}{pnl_usdt:.2f} USDT</b>\n"
            f"Entry: {entry_price:,.2f} → Close: {close_price:,.2f}\n"
            f"Preset: {preset_name}"
        )
        try:
            append_entry(
                self._log_path, "info",
                f"{symbol} {side} {result}", f"pnl={pnl_usdt:.2f}", "order_executor",
            )
        except Exception as exc:
            logger.error(f"system_log write failed: {exc}")

        if not (self._token and self._chat_id):
            return
        if not self._rate_limit_ok("trade"):
            return
        try:
            self._send_telegram(text)
        except Exception as exc:
            logger.error("Telegram trade_close send failed")
            try:
                append_entry(
                    self._log_path, "warning", "Telegram send failed", str(exc), "notifier"
                )
            except Exception:
                pass

    def dismiss(self, alert_id: str) -> None:
        state = self._read_alert_state()
        if alert_id not in state["dismissed_ids"]:
            state["dismissed_ids"].append(alert_id)
        self._write_alert_state(state)

    def send_test(self, msg_type: str = "connection") -> tuple[bool, str]:
        """Returns (ok, error_message). Always bypasses rate limit."""
        if not self._token or not self._chat_id:
            return False, "Token or chat_id not configured"

        _SAMPLES: dict[str, tuple[str, bool]] = {
            "connection": (
                "ℹ️ <b>Test notification</b>\nBot notifier is working.",
                False,
            ),
            "trade_win": (
                "✅ <b>BTCUSDT BUY — Win</b>\n"
                "PnL: <b>+12.34 USDT</b>\n"
                "Entry: 68,000.00 → Close: 68,450.00\n"
                "Preset: trail_15_from_30_full",
                False,
            ),
            "trade_loss": (
                "❌ <b>ETHUSDT SELL — Loss</b>\n"
                "PnL: <b>−5.20 USDT</b>\n"
                "Entry: 3,200.00 → Close: 3,218.50\n"
                "Preset: trail_15_from_30_full",
                False,
            ),
            "emergency": (
                "🚨 <b>Test emergency alert</b>\n"
                "This is a test of the emergency notification.",
                True,
            ),
            "balance_warning": (
                "⚠️ <b>Low balance warning</b>\n"
                "Balance 42.10 USDT is below threshold 50.00 USDT.",
                False,
            ),
        }

        if msg_type not in _SAMPLES:
            return False, f"Unknown message type: {msg_type}"

        text, mention = _SAMPLES[msg_type]
        try:
            self._send_telegram(text, mention=mention)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------ #

    def _rate_limit_ok(self, category: str) -> bool:
        now = time.monotonic()
        if now - self._last_sent.get(category, 0.0) < self._min_interval_s:
            return False
        self._last_sent[category] = now
        return True

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

    def _send_telegram(self, text: str, mention: bool = False) -> None:
        if mention:
            text = f"@bo_pal {text}"
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            raise requests.HTTPError(f"HTTP {resp.status_code}") from None

    def _read_alert_state(self) -> dict[str, list]:
        if self._alert_path.exists():
            try:
                return json.loads(self._alert_path.read_text())
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                logger.warning(f"alert_state corrupt at {self._alert_path}, resetting: {exc}")
        return {"alerts": [], "dismissed_ids": []}

    def _write_alert_state(self, state: dict[str, list]) -> None:
        self._alert_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._alert_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self._alert_path)
```

- [ ] **Step 2: Run the full test suite to verify all pass**

```bash
python -m pytest tests/test_notifier.py -v 2>&1 | tail -25
```

Expected: all 15 tests PASS (7 original + 8 new).

- [ ] **Step 3: Commit**

```bash
git add bot/notifier.py
git commit -m "feat: notifier rate limiting, trade close notifications, emergency @bo_pal mention"
```

---

## Task 4: Call `notify_trade_close` from OrderExecutor

**Files:**
- Modify: `bot/order_executor.py`

- [ ] **Step 1: Add `notify_trade_close` call at end of `_record_real_order_close`**

Find `_record_real_order_close` (the line `tmp.replace(path)` at the very end of the method). Add after it:

```python
        if self._notifier is not None:
            self._notifier.notify_trade_close(
                symbol=symbol,
                side=order.side,
                pnl_usdt=pnl_usdt,
                entry_price=order.entry_price,
                close_price=close_price,
                preset_name=order.preset_name,
            )
```

The complete end of the method looks like:

```python
        if len(records) > 1000:
            records = records[-1000:]
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(records))
        tmp.replace(path)

        if self._notifier is not None:
            self._notifier.notify_trade_close(
                symbol=symbol,
                side=order.side,
                pnl_usdt=pnl_usdt,
                entry_price=order.entry_price,
                close_price=close_price,
                preset_name=order.preset_name,
            )
```

- [ ] **Step 2: Run existing order_executor tests**

```bash
python -m pytest tests/test_order_executor.py -v 2>&1 | tail -15
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add bot/order_executor.py
git commit -m "feat: send Telegram trade close notification on every real order close"
```

---

## Task 5: Pass `min_interval_s` to Notifier in main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update Notifier construction**

Find the `Notifier(...)` call in `main.py` (currently lines 107-112):

```python
    notifier = Notifier(
        log_path=_PROJECT_ROOT / "data" / "system_log.json",
        alert_path=_PROJECT_ROOT / "dashboard" / "public" / "alert_state.json",
        telegram_token=risk_cfg.get("telegram", {}).get("token", ""),
        telegram_chat_id=risk_cfg.get("telegram", {}).get("chat_id", ""),
    )
```

Replace with:

```python
    notifier = Notifier(
        log_path=_PROJECT_ROOT / "data" / "system_log.json",
        alert_path=_PROJECT_ROOT / "dashboard" / "public" / "alert_state.json",
        telegram_token=risk_cfg.get("telegram", {}).get("token", ""),
        telegram_chat_id=risk_cfg.get("telegram", {}).get("chat_id", ""),
        min_interval_s=float(risk_cfg.get("telegram_notify_interval_s", 120)),
    )
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: wire telegram_notify_interval_s from risk config into Notifier"
```

---

## Task 6: Update dashboard API routes

**Files:**
- Modify: `dashboard/app/api/telegram/test/route.ts`

- [ ] **Step 1: Replace `dashboard/app/api/telegram/test/route.ts`**

The route now accepts an optional `type` body param. Unknown types return 400. `connection` still sends the rich backtest summary (existing behaviour). Others send fixed sample messages.

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { BOT_ROOT } from '../../_utils'
import path from 'path'
import fs from 'fs'

const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')
const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')

interface Preset {
  total_profit_pct: number
  total_trades: number
  win_rate: number
  balance_start: number
  balance_end: number
}

interface BacktestFile {
  symbol: string
  presets: Record<string, Preset>
}

interface SymbolSummary {
  symbol: string
  profit: number
  trades: number
  winRate: number
}

const SAMPLE_MESSAGES: Record<string, { text: string; mention: boolean }> = {
  trade_win: {
    text: '✅ <b>BTCUSDT BUY — Win</b>\nPnL: <b>+12.34 USDT</b>\nEntry: 68,000.00 → Close: 68,450.00\nPreset: trail_15_from_30_full',
    mention: false,
  },
  trade_loss: {
    text: '❌ <b>ETHUSDT SELL — Loss</b>\nPnL: <b>−5.20 USDT</b>\nEntry: 3,200.00 → Close: 3,218.50\nPreset: trail_15_from_30_full',
    mention: false,
  },
  emergency: {
    text: '🚨 <b>Test emergency alert</b>\nThis is a test of the emergency notification.',
    mention: true,
  },
  balance_warning: {
    text: '⚠️ <b>Low balance warning</b>\nBalance 42.10 USDT is below threshold 50.00 USDT.',
    mention: false,
  },
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function sign(n: number): string {
  return n >= 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`
}

function buildConnectionMessage(): string {
  let files: string[]
  try {
    files = fs.readdirSync(PUBLIC_DIR).filter(f => f.startsWith('backtest_results_') && f.endsWith('.json'))
  } catch {
    return '🤖 <b>Binance Futures Bot</b>\n\n✅ Notifier connected — no backtest data yet.'
  }

  const summaries: SymbolSummary[] = []
  for (const file of files) {
    try {
      const data: BacktestFile = JSON.parse(fs.readFileSync(path.join(PUBLIC_DIR, file), 'utf8'))
      const presets = Object.values(data.presets ?? {})
      if (presets.length === 0) continue
      const best = presets.reduce((a, b) => b.total_profit_pct > a.total_profit_pct ? b : a)
      summaries.push({ symbol: data.symbol, profit: best.total_profit_pct, trades: best.total_trades, winRate: best.win_rate })
    } catch { /* skip */ }
  }

  if (summaries.length === 0) {
    return '🤖 <b>Binance Futures Bot</b>\n\n✅ Notifier connected — no backtest data yet.'
  }

  const sorted = [...summaries].sort((a, b) => b.profit - a.profit)
  const profitable = summaries.filter(s => s.profit > 0).length
  const avgProfit = summaries.reduce((a, s) => a + s.profit, 0) / summaries.length
  const totalTrades = summaries.reduce((a, s) => a + s.trades, 0)
  const medals = ['🥇', '🥈', '🥉']

  const rows = sorted.map((s, i) => {
    const medal = medals[i] ?? '  '
    const sym = esc(s.symbol.replace('USDT', ''))
    const profit = sign(s.profit)
    const wr = Math.round(s.winRate * 100)
    const profitTag = s.profit >= 0 ? `<b>${profit}</b>` : profit
    return `${medal} <b>${sym}</b>  ${profitTag}  ${s.trades}T  WR ${wr}%`
  })

  return [
    '🤖 <b>Binance Futures Bot — Backtest Highlights</b>',
    '',
    `Best preset profit per symbol (${summaries.length} active):`,
    '',
    ...rows,
    '',
    `📈 Avg profit: <b>${sign(avgProfit)}</b>   Total trades: <b>${totalTrades}</b>`,
    `✅ <b>${profitable}/${summaries.length}</b> symbols profitable`,
    '',
    '<i>Notifier connected — alerts are live.</i>',
  ].join('\n')
}

export async function POST(req: NextRequest) {
  let token = ''
  let chatId = ''
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
    token = cfg?.telegram?.token?.trim() ?? ''
    chatId = String(cfg?.telegram?.chat_id ?? '').trim()
  } catch {
    return NextResponse.json({ ok: false, error: 'Could not read risk_config.json' }, { status: 500 })
  }

  if (!token || !chatId) {
    return NextResponse.json(
      { ok: false, error: 'Telegram token or chat ID not configured. Save them in Settings first.' },
      { status: 400 },
    )
  }

  let msgType = 'connection'
  try {
    const body = await req.json()
    if (body?.type && typeof body.type === 'string') msgType = body.type
  } catch { /* default to connection */ }

  let text: string
  let mention = false

  if (msgType === 'connection') {
    text = buildConnectionMessage()
  } else if (msgType in SAMPLE_MESSAGES) {
    ;({ text, mention } = SAMPLE_MESSAGES[msgType])
  } else {
    return NextResponse.json({ ok: false, error: `Unknown message type: ${msgType}` }, { status: 400 })
  }

  if (mention) text = `@bo_pal ${text}`

  try {
    const url = `https://api.telegram.org/bot${token}/sendMessage`
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML' }),
    })
    const data = await res.json()
    if (!res.ok || !data.ok) {
      return NextResponse.json({ ok: false, error: data.description ?? 'Telegram API error' }, { status: 502 })
    }
    return NextResponse.json({ ok: true })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 502 })
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/app/api/telegram/test/route.ts
git commit -m "feat: telegram test route accepts message type param (trade_win/loss/emergency/balance_warning)"
```

---

## Task 7: Update Settings page UI

**Files:**
- Modify: `dashboard/app/settings/page.tsx`

- [ ] **Step 1: Add `notifyInterval` and `testMsgType` state**

Find the existing telegram state declarations (around line 65):

```typescript
  const [telegram, setTelegram] = useState({ token: '', chat_id: '' })
  const [telegramStatus, setTelegramStatus] = useState<'idle'|'testing'|'ok'|'error'>('idle')
  const [telegramError, setTelegramError] = useState('')
```

Add two lines immediately after:

```typescript
  const [notifyInterval, setNotifyInterval] = useState(120)
  const [testMsgType, setTestMsgType] = useState('connection')
```

- [ ] **Step 2: Load `notifyInterval` from config**

Find the `useEffect` that loads config and sets `telegram` (around line 76):

```typescript
      if (d.config?.telegram) setTelegram(d.config.telegram)
```

Add directly after:

```typescript
      if (d.config?.telegram_notify_interval_s != null) {
        setNotifyInterval(Number(d.config.telegram_notify_interval_s))
      }
```

- [ ] **Step 3: Add `saveInterval` function**

Find `saveTelegram` (around line 127). Add a new function immediately after it:

```typescript
  const saveInterval = async (value: number) => {
    await fetch('/api/risk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telegram_notify_interval_s: value }),
    })
  }
```

- [ ] **Step 4: Update `testTelegram` to send the selected type**

Find the `testTelegram` function (around line 137). Change:

```typescript
      const r = await fetch('/api/telegram/test', { method: 'POST' })
```

to:

```typescript
      const r = await fetch('/api/telegram/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: testMsgType }),
      })
```

- [ ] **Step 5: Add interval selector and message type dropdown to the Telegram section**

Find the Telegram section's inner `<div>` with `className="rounded-lg border..."` (around line 462). It currently has: token input, chat_id input, send button row, help text.

Add the following two blocks between the chat_id `<div>` and the send button `<div>`:

```tsx
          <div>
            <label className="block text-xs text-gray-400 mb-1">Alert interval (min gap between messages)</label>
            <select
              value={notifyInterval}
              onChange={e => {
                const v = Number(e.target.value)
                setNotifyInterval(v)
                saveInterval(v)
              }}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
            >
              <option value={30}>30 seconds</option>
              <option value={120}>2 minutes</option>
              <option value={300}>5 minutes</option>
              <option value={600}>10 minutes</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Test message type</label>
            <select
              value={testMsgType}
              onChange={e => setTestMsgType(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
            >
              <option value="connection">Connection (backtest highlights)</option>
              <option value="trade_win">Trade Win</option>
              <option value="trade_loss">Trade Loss</option>
              <option value="emergency">Emergency (with @bo_pal)</option>
              <option value="balance_warning">Balance Warning</option>
            </select>
          </div>
```

- [ ] **Step 6: Verify dashboard builds without TypeScript errors**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npm run build 2>&1 | tail -20
```

Expected: exit 0, no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/app/settings/page.tsx
git commit -m "feat: settings page — alert interval selector and test message type dropdown"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| Trade win/loss notification with USDT PnL | Task 3 (`notify_trade_close`) + Task 4 (OrderExecutor wiring) |
| Per-category rate limit | Task 3 (`_rate_limit_ok`, `_last_sent`) |
| `telegram_notify_interval_s` config key | Task 1 (Python + TS) |
| Interval configurable in Settings | Task 7 (interval selector) |
| Emergency bypasses rate limit | Task 3 (`is_emergency or self._rate_limit_ok`) |
| `@bo_pal` mention on emergency | Task 3 (`_send_telegram(mention=True)`) |
| Parse mode Markdown → HTML | Task 3 (`parse_mode: "HTML"`) |
| `send_test(msg_type)` — 5 types | Task 3 |
| `send_test` bypasses rate limit | Task 3 (no `_rate_limit_ok` call in `send_test`) |
| Test route accepts `type` param | Task 6 |
| Message type dropdown in Settings | Task 7 |
| 8 new tests | Task 2 |
| main.py passes `min_interval_s` | Task 5 |

All requirements covered. No gaps found.
