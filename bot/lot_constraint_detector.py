"""
Detect symbols where Binance's maxQty prevents the bot from deploying
its full capital allocation, and compute optimal weight + leverage for each.

A symbol is considered constrained when its max single-order notional
(maxQty × current_price) at the risk tier's maximum leverage covers less
than CONSTRAINT_THRESHOLD of the symbol's standard single-unit allocation.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONSTRAINT_THRESHOLD = 0.50  # below 50% utilisation → constrained


def adjust_constrained_symbols(
    lot_cache: dict,
    bracket_maxes: dict,
    prices: dict[str, float],
    symbol_registry,
    risk_cfg: dict,
    active_symbols: list[str],
) -> list[str]:
    """
    Detect constrained symbols, update their weight and leverage override in
    symbol_registry, and return the list of adjusted symbol names.

    Parameters
    ----------
    lot_cache       : OrderExecutor._lot_cache  (shared reference)
    bracket_maxes   : OrderExecutor._bracket_max
    prices          : {symbol: last_close_price}
    symbol_registry : SymbolRegistry instance
    risk_cfg        : dict from load_risk_config()
    active_symbols  : list of symbols currently active (weight > 0)
    """
    weights = symbol_registry.get_weights()
    total_weight = sum(float(weights.get(s, 1)) for s in active_symbols if float(weights.get(s, 0)) > 0)
    if total_weight <= 0:
        return []

    tiers = risk_cfg.get("balance_tiers", [])
    min_bal_pct = float(risk_cfg.get("min_balance_pct", 15.0))
    deploy_pct = float(tiers[0].get("max_deploy_pct", 40)) if tiers else 40.0

    balance = float(risk_cfg.get('_detected_balance', 0.0))
    if balance <= 0:
        return []

    # Pick the highest-tier whose floor the balance meets
    for tier in reversed(tiers):
        if balance >= tier.get("min_balance_usdt", 0):
            deploy_pct = float(tier.get("max_deploy_pct", deploy_pct))
            break

    # standard_fraction: the fraction of total balance a single weight unit receives
    standard_fraction = (1 - min_bal_pct / 100) * (deploy_pct / 100) / total_weight

    # Margin assigned to a single weight-1 symbol
    standard_alloc_per_unit_margin = balance * standard_fraction

    adjusted = []
    for symbol in active_symbols:
        sym_weight = float(weights.get(symbol, 1))
        if sym_weight <= 0:
            continue

        lot = lot_cache.get(symbol, {})
        max_qty = float(lot.get('max_qty', 0.0))
        if max_qty <= 0:
            continue  # no exchange data yet for this symbol

        price = prices.get(symbol, 0.0)
        if price <= 0:
            continue

        max_lev = int(bracket_maxes.get(symbol, 20))
        if max_lev <= 0:
            max_lev = 20

        # Maximum margin deployable per order given exchange qty and leverage constraints
        max_notional = max_qty * price
        capacity_margin = max_notional / max_lev

        utilisation = capacity_margin / (sym_weight * standard_alloc_per_unit_margin)

        if utilisation >= CONSTRAINT_THRESHOLD:
            continue  # symbol can use >= 50% of its allocation — not constrained

        # Derive the weight that exactly saturates capacity at max leverage
        optimal_weight_float = max(
            capacity_margin / standard_alloc_per_unit_margin,
            0.001,  # floor to avoid zeroing out a symbol entirely
        )

        logger.info(
            f"[{symbol}] Constrained by exchange maxQty={max_qty:.0f}: "
            f"capacity={capacity_margin:.2f} USDT margin at {max_lev}× "
            f"vs standard={standard_alloc_per_unit_margin:.2f} USDT. "
            f"Adjusting weight {sym_weight:.4f} → {optimal_weight_float:.4f}, "
            f"leverage → {max_lev}×"
        )

        symbol_registry.set_weight(symbol, optimal_weight_float)
        symbol_registry.set_leverage_override(symbol, max_lev)
        adjusted.append(symbol)

    return adjusted
