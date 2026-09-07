# bot/mode_manager.py
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from bot.notifier import Notifier

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODE_PATH = _PROJECT_ROOT / "data" / "bot_mode.json"
_DEFAULT_COMMAND_PATH = _PROJECT_ROOT / "data" / "bot_command.json"
_DEFAULT_RESULT_PATH = _PROJECT_ROOT / "data" / "bot_command_result.json"

POLL_INTERVAL = 2.0  # seconds


def opposite_mode(mode: str) -> str:
    """The mode a mirror instance runs, given the primary's mode.

    Anything other than an explicit 'live' mirrors to 'live'. Defaulting the unknown
    case to 'live' is deliberate rather than tidy: the primary also defaults to 'test'
    for anything it cannot read, so 'live' is the only answer that cannot leave both
    instances in the same mode — which would have them writing the same mode-suffixed
    files and corrupting the preset statistics the mirror exists to gather.
    """
    return 'test' if mode == 'live' else 'live'


def read_mode_file(path: Path = _DEFAULT_MODE_PATH) -> str:
    """The primary's mode as recorded on disk, defaulting to 'test'.

    Module-level so logging setup can resolve a filename before ModeManager exists.
    Out-of-vocabulary values collapse to 'test' to match ModeManager's own fallback —
    the two must agree or a log file would be named after a mode nothing else uses.
    """
    try:
        m = json.loads(path.read_text()).get('mode')
        return m if m in ('test', 'live') else 'test'
    except (json.JSONDecodeError, ValueError, OSError):
        return 'test'


class ModeManager:
    def __init__(
        self,
        mode_path: Path = _DEFAULT_MODE_PATH,
        command_path: Path = _DEFAULT_COMMAND_PATH,
        result_path: Path = _DEFAULT_RESULT_PATH,
        notifier: Notifier | None = None,
        mirror: bool = False,
    ) -> None:
        self._mode_path = mode_path
        self._command_path = command_path
        self._result_path = result_path
        self._notifier = notifier
        self._lock = asyncio.Lock()
        # A mirror instance runs whatever mode the primary is not running, so the two
        # can never share a file suffix — and current_mode names every data file.
        # It resolves this once at startup and re-resolves by exiting and letting the
        # container restart; see mirror_target_changed().
        self._mirror = mirror
        self.current_mode: str = (
            opposite_mode(self._read_mode()) if mirror else self._read_mode()
        )

    # ------------------------------------------------------------------ #
    # Mode state                                                           #
    # ------------------------------------------------------------------ #

    def _read_mode(self) -> str:
        """The mode recorded on disk, or 'test'.

        Validates the vocabulary rather than passing the value through. current_mode
        names every data file, so an unrecognised value used to give the primary a set
        of '_garbage'-suffixed files that nothing else would ever look for — and it
        would disagree with read_mode_file(), which names the log. Both must answer
        'test' for anything they cannot recognise.
        """
        if self._mode_path.exists():
            try:
                mode = json.loads(self._mode_path.read_text()).get("mode")
                if mode in ("test", "live"):
                    return mode
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return "test"

    def mirror_target_changed(self) -> bool:
        """True when a mirror instance is no longer the opposite of the primary.

        The mirror acts on this by exiting, so every uncertain case must answer False —
        a restart loop has no way out. Returns False for a primary (only the mirror
        self-exits on a mode change), and False for an absent, torn or
        out-of-vocabulary file. The existence check matters because _read_mode()
        swallows errors and returns 'test', which would make a momentarily-missing file
        look like a real flip to test.
        """
        if not self._mirror or not self._mode_path.exists():
            return False
        try:
            raw = json.loads(self._mode_path.read_text()).get('mode')
        except (json.JSONDecodeError, ValueError, OSError):
            return False
        if raw not in ('test', 'live'):
            return False
        return opposite_mode(raw) != self.current_mode

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
                    elif cmd_type == "test_telegram":
                        if self._notifier is not None:
                            ok, error = self._notifier.send_test()
                            self._write_result(cmd_id, ok=ok, error=error)
                        else:
                            self._write_result(cmd_id, ok=False, error="Notifier not configured")
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
