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
        resp = requests.post(
            url,
            json={"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
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
