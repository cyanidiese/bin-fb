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
            except (json.JSONDecodeError, ValueError, OSError):
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

    # ------------------------------------------------------------------ #
    # High-level sequences                                                 #
    # ------------------------------------------------------------------ #

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
