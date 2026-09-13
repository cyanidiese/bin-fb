"""Measured execution slippage, so virtual PnL is charged what real fills cost.

Virtual orders open at the signalled price. Real orders are MARKET orders and fill at
whatever the book gives. Measured over 137 real fills (2026-09-13, 60 days):

    all symbols   mean +0.0980%   median +0.0183%   p90 +0.3575%
    REZUSDT       mean +0.0134%        TIAUSDT      mean +0.1775%

Not one of those 137 fills came in better than signalled. The distribution is one-sided,
so ignoring it overstates virtual results systematically rather than symmetrically — and
at 5x leverage +0.098% of notional is ~0.49% of margin per trade, an order of magnitude
above the measured virtual edge of -0.052%/trade. Symbol-selection decisions taken off
unadjusted virtual numbers are therefore unsafe, which is what this module fixes.

The charge is applied to money only, never to trigger geometry — see
`docs/specs/2026-09-13-slippage-modelling.md`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Samples kept per symbol. Slippage tracks liquidity, which drifts, so the estimate
#: follows a rolling window rather than being anchored to fills from months ago.
WINDOW = 50

#: Used when a symbol has no measured history of its own. Roughly the cross-symbol mean
#: (+0.098%). The symbols we most want to judge are exactly the ones with no real fills,
#: so this must never flatter them.
DEFAULT_PCT = 0.10

#: Below this many samples a symbol's own mean is too noisy to trust over the default.
MIN_SAMPLES = 5


def adverse_pct(signalled: float, filled: float, side: str) -> float:
    """How much worse than signalled the fill was, in percent.

    Positive means we paid worse. Negative (a favourable fill) is preserved rather than
    clamped so the stored mean stays an honest measurement; the clamp belongs on the
    estimate, not on the data.
    """
    if signalled <= 0 or filled <= 0:
        return 0.0
    if side == 'BUY':
        return (filled - signalled) / signalled * 100.0
    return (signalled - filled) / signalled * 100.0


def record(
    path: Path,
    symbol: str,
    signalled: float,
    filled: float,
    side: str,
) -> None:
    """Append one measured fill to the rolling window. Never raises.

    Runs on the order-placement path, so a disk problem must not stop trading.
    """
    try:
        pct = adverse_pct(signalled, filled, side)
        if signalled <= 0 or filled <= 0:
            return
        store = _read(path)
        samples = list(store.get(symbol, {}).get('samples', []))
        samples.append(round(pct, 6))
        if len(samples) > WINDOW:
            samples = samples[-WINDOW:]
        store[symbol] = {
            'samples': samples,
            'n': len(samples),
            'mean': round(sum(samples) / len(samples), 6),
        }
        _write(path, store)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"slippage: could not record {symbol}: {exc}")


def estimate(path: Path, symbol: str, cfg: Optional[dict] = None) -> float:
    """The adverse percentage to charge this symbol. Never negative, never raises.

    Resolution order: explicit per-symbol override, then the symbol's own measured mean
    once it has enough samples, then the configured default.
    """
    cfg = cfg or {}
    try:
        if not cfg.get('slippage_model_enabled', True):
            return 0.0

        override = (cfg.get('slippage_per_symbol') or {}).get(symbol)
        if override is not None:
            return max(0.0, float(override))

        default = float(cfg.get('slippage_default_pct', DEFAULT_PCT))
        min_samples = int(cfg.get('slippage_min_samples', MIN_SAMPLES))

        entry = _read(path).get(symbol) or {}
        if int(entry.get('n', 0)) >= min_samples:
            return max(0.0, float(entry.get('mean', default)))
        return max(0.0, default)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"slippage: estimate failed for {symbol}: {exc}")
        return max(0.0, float((cfg or {}).get('slippage_default_pct', DEFAULT_PCT)))


def effective_entry(entry: float, side: str, slip_pct: float) -> float:
    """The price to charge PnL against, given the signalled entry.

    Worsens the entry in the direction of the trade. TP/SL are deliberately NOT derived
    from this: real slippage does not move the exchange's trigger levels, and neither may
    this (bot/order_executor.py:301).
    """
    if entry <= 0 or slip_pct <= 0:
        return entry
    factor = 1.0 + slip_pct / 100.0 if side == 'BUY' else 1.0 - slip_pct / 100.0
    return entry * factor


def stats(path: Path) -> dict:
    """Per-symbol {n, mean} for reporting. Never raises."""
    try:
        return {
            sym: {'n': int(v.get('n', 0)), 'mean': float(v.get('mean', 0.0))}
            for sym, v in _read(path).items()
            if isinstance(v, dict)
        }
    except Exception:  # pragma: no cover - defensive
        return {}


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path: Path, store: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(store))
    tmp.replace(path)
