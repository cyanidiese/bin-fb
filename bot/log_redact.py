"""Keep secrets out of log files.

requests puts the full URL into its exception text, and Telegram's Bot API carries the
token in the path (https://api.telegram.org/bot<id>:<secret>/getUpdates). Every
TelegramMenu poll error therefore wrote the token into bot.log — ~30k times between
2026-09-23 and 09-26. Redacting the final formatted line covers exception text and
tracebacks alike, wherever the message was built.
"""
from __future__ import annotations

import logging
import re

_TELEGRAM_TOKEN_RE = re.compile(r'bot\d{6,}:[A-Za-z0-9_-]{20,}')


def redact(text: str) -> str:
    return _TELEGRAM_TOKEN_RE.sub('bot<redacted>', text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
