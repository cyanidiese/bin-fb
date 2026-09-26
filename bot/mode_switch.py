"""Close-everything step of a mode switch (primary only).

main.py::_primary_mode_watch notices that data/bot_mode.json asks for another mode,
stops new real orders, confirms, and then calls close_out(). Only a 'flat' outcome lets
the process exit and restart in the new mode — it must never exit holding positions of
the old mode, which would be left unmanaged on the old exchange.
Spec: docs/specs/2026-09-26-mode-switch-restart-and-per-mode-backtests.md

Kept free of main.py's closures so the sequence can be tested with fakes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional

logger = logging.getLogger(__name__)

Outcome = Literal['refused', 'postponed', 'flat']


@dataclass
class CloseOutResult:
    outcome: Outcome
    still_open: Optional[list[str]] = field(default=None)   # None = could not check


async def close_out(
    target: str,
    *,
    keys_present: Callable[[str], bool],
    close_virtual: Callable[[], Awaitable[object]],
    close_real: Callable[[], Awaitable[object]],
    open_on_exchange: Callable[[], Awaitable[Optional[list[str]]]],
    close_untracked: Callable[[], Awaitable[object]],
    sleep: Callable[[float], Awaitable[object]],
    attempts: int = 3,
) -> CloseOutResult:
    """Refuse, or close every position and prove the exchange is flat.

    - 'refused': the target mode cannot run (API keys missing) — nothing was closed.
    - 'postponed': positions may remain (still open, or the exchange could not be asked)
      after `attempts` rounds — the caller keeps new orders stopped and retries.
    - 'flat': every position closed and the exchange confirms it.
    """
    if not keys_present(target):
        return CloseOutResult('refused')
    try:
        await close_virtual()
    except Exception as exc:          # virtual positions hold no money; keep going
        logger.error(f"Mode switch: closing virtual positions failed: {exc}")
    left: Optional[list[str]] = None
    for attempt in range(1, attempts + 1):
        try:
            await close_real()
        except Exception as exc:
            logger.error(f"Mode switch: market close failed: {exc}")
        left = await open_on_exchange()
        if left:
            # close_all_orders_at_market() forgets an order even when its close failed,
            # so anything still on the exchange is now untracked: close it as such.
            try:
                await close_untracked()
            except Exception as exc:
                logger.error(f"Mode switch: closing untracked positions failed: {exc}")
            left = await open_on_exchange()
        if left == []:
            return CloseOutResult('flat', [])
        logger.warning(f"Mode switch: not flat yet (attempt {attempt}/{attempts}): {left}")
        if attempt < attempts:
            await sleep(10.0)
    return CloseOutResult('postponed', left)
