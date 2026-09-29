"""Quantity rounding exactly as a real order gets it, for the virtual simulator.

Real orders (main._try_place_order + OrderExecutor.place_order) size in this order:
  1. qty = margin x leverage x 1.02 / entry   (2 % buffer so rounding cannot drop the
     notional under the exchange floor)
  2. floor to the LOT_SIZE step, cap at maxQty, below minQty -> not placeable
  3. still under min notional -> bump ONE step, else the order is refused
  4. notional above max_order_notional_usdt -> cap, floored to the step
Virtual orders used the unrounded margin x leverage / entry, so their size — and every
PnL figure — differed from what a real order of the same signal would hold.
Spec: docs/specs/2026-09-29-virtual-real-parity.md (part 2, V3).
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

QTY_BUFFER = 1.02


def _floor(qty: float, step: str) -> float:
    return float(Decimal(str(qty)).quantize(Decimal(str(step)), rounding=ROUND_DOWN))


def real_quantity(margin: float, leverage: int, entry: float, lot: dict,
                  max_notional: float = 0.0) -> float:
    """The quantity a real order would be placed with, or 0.0 when it would be refused.

    `lot`: the OrderExecutor lot-cache entry (step_size, min_qty, max_qty, min_notional).
    """
    if entry <= 0 or margin <= 0 or leverage <= 0:
        return 0.0
    step = str(lot.get('step_size') or '0.001')
    min_qty = float(lot.get('min_qty') or 0.0)
    max_qty = float(lot.get('max_qty') or 0.0)
    min_notional = float(lot.get('min_notional') or 0.0)

    qty = _floor(margin * leverage * QTY_BUFFER / entry, step)
    if max_qty > 0 and qty > max_qty:
        qty = _floor(max_qty, step)
    if qty < min_qty or qty <= 0:
        return 0.0
    if min_notional > 0 and qty * entry < min_notional:
        bumped = float(Decimal(str(qty)) + Decimal(step))
        if bumped * entry < min_notional:
            return 0.0
        qty = bumped
    if max_notional > 0 and qty * entry > max_notional:
        qty = _floor(max_notional / entry, step)
    return qty
