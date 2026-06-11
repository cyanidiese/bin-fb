# bot/notifier.py
from __future__ import annotations

import hashlib
import html
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

_TEST_SAMPLES: dict[str, tuple[str, bool]] = {
    "connection": (
        "ℹ️ <b>Test notification</b>\nBot notifier is working.",
        False,
    ),
    "trade_win": (
        "✅ <b>BTCUSDT BUY — Win</b>\n"
        "PnL: <b>+12.34 USDT</b>\n"
        "Balance: 1,234.56 USDT\n"
        "Entry: 68,000.00 → Close: 68,450.00\n"
        "Preset: trail_15_from_30_full",
        False,
    ),
    "trade_loss": (
        "❌ <b>ETHUSDT SELL — Loss</b>\n"
        "PnL: <b>-5.20 USDT</b>\n"
        "Balance: 1,229.36 USDT\n"
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


class Notifier:
    def __init__(
        self,
        log_path: Path,
        alert_path: Path,
        telegram_token: str,
        telegram_chat_id: str,
        min_interval_s: float = 120.0,
        emergency_repeat_interval_s: float = 1800.0,
        warning_repeat_interval_s: float = 14400.0,
    ) -> None:
        self._log_path = log_path
        self._alert_path = alert_path
        self._token = telegram_token
        self._chat_id = telegram_chat_id
        self._min_interval_s = min_interval_s
        self._emergency_repeat_s = emergency_repeat_interval_s
        self._warning_repeat_s = warning_repeat_interval_s
        self._last_sent: dict[str, float] = {}
        # content-hash → last monotonic time sent, so the same message is never spammed
        self._last_sent_content: dict[str, float] = {}

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
            if self._content_rate_limit_ok(level, title, body):
                emoji = {"info": "ℹ️", "warning": "⚠️", "emergency": "🚨"}.get(level, "")
                text = f"{emoji} <b>{html.escape(title)}</b>\n{html.escape(body)}"
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

    @staticmethod
    def _fmt_price(price: float) -> str:
        """Format a price with enough decimal places to be meaningful regardless of magnitude."""
        if price == 0:
            return "0"
        if price < 0.0001:
            return f"{price:,.8f}"
        if price < 0.01:
            return f"{price:,.6f}"
        if price < 1:
            return f"{price:,.5f}"
        if price < 100:
            return f"{price:,.4f}"
        if price < 10000:
            return f"{price:,.2f}"
        return f"{price:,.0f}"

    def notify_trade_close(
        self,
        symbol: str,
        side: str,
        pnl_usdt: float,
        entry_price: float,
        close_price: float,
        preset_name: str,
        balance_after: float = 0.0,
    ) -> None:
        win = pnl_usdt >= 0
        emoji = "✅" if win else "❌"
        result = "Win" if win else "Loss"
        sign = "+" if pnl_usdt >= 0 else ""
        text = (
            f"{emoji} <b>{html.escape(symbol)} {html.escape(side)} — {result}</b> <i>[Real]</i>\n"
            f"PnL: <b>{sign}{pnl_usdt:.2f} USDT</b>\n"
            f"Balance: {balance_after:,.2f} USDT\n"
            f"Entry: {self._fmt_price(entry_price)} → Close: {self._fmt_price(close_price)}\n"
            f"Preset: {html.escape(preset_name)}"
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
        if not self._rate_limit_ok(f"trade:{symbol}"):
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

        if msg_type not in _TEST_SAMPLES:
            return False, f"Unknown message type: {msg_type}"

        text, mention = _TEST_SAMPLES[msg_type]
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

    def _content_rate_limit_ok(self, level: str, title: str, body: str) -> bool:
        """True if this exact (title, body) hasn't been sent recently."""
        key = hashlib.md5(f"{title}\x00{body}".encode()).hexdigest()
        interval = {
            "emergency": self._emergency_repeat_s,
            "warning": self._warning_repeat_s,
        }.get(level, self._min_interval_s)
        now = time.monotonic()
        if now - self._last_sent_content.get(key, 0.0) < interval:
            return False
        self._last_sent_content[key] = now
        return True

    def _append_alert(self, level: str, title: str, body: str, source: str) -> None:
        state = self._read_alert_state()
        dismissed = set(state.get("dismissed_ids", []))
        # Skip if an identical active (not dismissed) alert already exists
        for existing in state["alerts"]:
            if (
                existing.get("id") not in dismissed
                and existing.get("title") == title
                and existing.get("body") == body
            ):
                return
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
