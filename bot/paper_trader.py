"""
Paper trading engine: runs all preset configurations simultaneously against
live market data using FakeOrder simulation. No real orders are placed.

State is persisted to disk so the session survives bot restarts.
"""
import dataclasses
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from bot.analyzer import Analyzer
from bot.backtester import PresetResult
from bot.fake_order import FakeOrder
from bot.recommendation_engine import RecommendationEngine
from config.settings import Settings

logger = logging.getLogger(__name__)

# Minimum seconds between entry attempts per preset on price ticks.
# At 15m candles this gives ~15 checks per candle instead of 1.
_ENTRY_CHECK_INTERVAL = 60.0


class PaperTrader:
    """
    Maintains one independent FakeOrder per preset and accumulates trade
    statistics across live candle closes.

    Entry checks run on every price tick (throttled to once per
    _ENTRY_CHECK_INTERVAL seconds per preset) so entries happen at the
    actual market price when a signal fires, not only at candle close.

    Call sequence:
        trader.build_from_klines(klines)       # initialise analyzer
        trader.on_price_update(price)           # on every price tick
        await trader.on_candle(kline)           # on each candle close
    """

    def __init__(
        self,
        base_settings: Settings,
        presets: Dict[str, dict],
        state_path: Path,
        export_path: Path,
    ):
        self._base = base_settings
        self._presets = presets
        self._state_path = state_path
        self._export_path = export_path

        self._started_at = datetime.now(timezone.utc).isoformat()
        self._candle_index: int = 0
        self._current_price: float = 0.0
        self._analyzer: Optional[Analyzer] = None
        self._trend = None  # refreshed after each candle close; used by tick checks

        # Per-preset runtime state (initialised below, overwritten from disk if exists)
        self._results: Dict[str, PresetResult] = {}
        self._open_orders: Dict[str, Optional[FakeOrder]] = {}
        self._engines: Dict[str, RecommendationEngine] = {}
        self._cooldown: Dict[str, dict] = {}
        self._last_check_ts: Dict[str, float] = {}  # monotonic time of last entry attempt

        self._init_presets()
        self._load_state()

    # ------------------------------------------------------------------ #
    # Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def _init_presets(self) -> None:
        for name, overrides in self._presets.items():
            settings = dataclasses.replace(self._base, **overrides)
            self._engines[name] = RecommendationEngine(settings)
            self._results.setdefault(name, PresetResult(name))
            self._open_orders.setdefault(name, None)
            self._cooldown.setdefault(name, {
                'consecutive_losses': {'BUY': 0, 'SELL': 0},
                'blocked_until': {'BUY': 0, 'SELL': 0},
                'last_loss_candle': {'BUY': -9999, 'SELL': -9999},
                'global_blocked_until': 0,
            })
            self._last_check_ts[name] = 0.0

    def build_from_klines(self, klines: list) -> None:
        """Seed the shared analyzer from historical klines. Call once on startup."""
        self._analyzer = Analyzer(self._base.swing_neighbours)
        self._analyzer.build_from_klines(klines)
        self._candle_index = len(klines)
        if klines:
            self._analyzer.update_price(float(klines[-1][4]))
        self._trend = self._analyzer.get_trend()
        logger.info(f"PaperTrader seeded from {len(klines)} klines (index={self._candle_index})")

    # ------------------------------------------------------------------ #
    # Live updates                                                         #
    # ------------------------------------------------------------------ #

    def on_price_update(self, price: float) -> None:
        """Called on every WebSocket tick. Attempts entry for idle presets."""
        self._current_price = price
        if self._analyzer is None or self._trend is None:
            return

        now = time.monotonic()
        for name, overrides in self._presets.items():
            if self._open_orders[name] is not None:
                continue
            if now - self._last_check_ts[name] < _ENTRY_CHECK_INTERVAL:
                continue
            self._last_check_ts[name] = now
            self._try_open(name, overrides, self._trend, price, self._candle_index)

    async def on_candle(self, kline: list) -> None:
        """Process one completed candle."""
        if self._analyzer is None:
            logger.warning("PaperTrader: analyzer not seeded, skipping candle")
            return

        open_p = float(kline[1])
        high   = float(kline[2])
        low    = float(kline[3])
        close  = float(kline[4])
        self._current_price = close
        self._candle_index += 1
        i = self._candle_index

        # ── 1. Check all open orders (full OHLC needed for accurate TP/SL) ─
        for name, order in list(self._open_orders.items()):
            if order is None:
                continue
            outcome = order.check(high, low, i, candle_open=open_p, candle_close=close)
            if outcome is not None:
                self._results[name].add(order)
                self._apply_cooldown(name, order.side, outcome, i)
                self._open_orders[name] = None
                # Brief pause before re-entry so we don't re-enter immediately
                # on the same price tick that closed the order.
                self._last_check_ts[name] = time.monotonic()
                logger.info(f"[{name}] closed {outcome} @ {order.close_price:.2f}")

        # ── 2. Advance analyzer ──────────────────────────────────────────
        self._analyzer.update_price(open_p)
        self._analyzer.add_candle(kline)
        entry_price = close
        self._analyzer.update_price(entry_price)

        # ── 3. Refresh cached trend for between-candle tick checks ───────
        self._trend = self._analyzer.get_trend()

        # ── 4. Candle-close entry attempt with fresh swing structure ─────
        if self._trend is not None:
            for name, overrides in self._presets.items():
                if self._open_orders[name] is not None:
                    continue
                self._try_open(name, overrides, self._trend, entry_price, i)
            # Align tick cooldown to candle close so first tick check fires
            # _ENTRY_CHECK_INTERVAL seconds after the candle, not sooner.
            now = time.monotonic()
            for name in self._presets:
                self._last_check_ts[name] = now

        open_count = sum(1 for o in self._open_orders.values() if o is not None)
        logger.info(
            f"Candle {i}: close={close:.2f}  open_orders={open_count}/{len(self._presets)}"
        )

        # ── 5. Persist and publish ───────────────────────────────────────
        self._save_state()
        self._export()

    # ------------------------------------------------------------------ #
    # Order opening logic (mirrors backtester._run_preset validation)     #
    # ------------------------------------------------------------------ #

    def _try_open(
        self,
        name: str,
        overrides: dict,
        trend,
        entry_price: float,
        candle_index: int,
    ) -> None:
        settings = dataclasses.replace(self._base, **overrides)
        engine   = self._engines[name]
        cd       = self._cooldown[name]

        rec = engine.generate(trend, entry_price)
        if rec is None:
            return

        side = rec.getSide()

        if settings.loss_streak_max > 0:
            if candle_index < cd['global_blocked_until']:
                return
            if candle_index < cd['blocked_until'].get(side, 0):
                return

        raw_tp = rec.getTarget()
        sl     = rec.getStop()

        if side == 'BUY':
            if raw_tp <= entry_price or sl is None or sl >= entry_price:
                return
            tp             = entry_price + (raw_tp - entry_price) * settings.tp_multiplier
            sl_dist_pct    = (entry_price - sl) / entry_price * 100
            profit_dist_pct = (tp - entry_price) / entry_price * 100
        else:
            if raw_tp >= entry_price or sl is None or sl <= entry_price:
                return
            tp             = entry_price - (entry_price - raw_tp) * settings.tp_multiplier
            sl_dist_pct    = (sl - entry_price) / entry_price * 100 * 1.5
            profit_dist_pct = (entry_price - tp) / entry_price * 100

        if abs(sl - entry_price) < entry_price * 0.0001:
            return
        if settings.max_profit_pct > 0 and profit_dist_pct > settings.max_profit_pct:
            return
        if settings.min_sl_pct > 0 and sl_dist_pct < settings.min_sl_pct:
            return
        if settings.max_sl_pct > 0 and sl_dist_pct > settings.max_sl_pct:
            return

        profit_dist = abs(tp - entry_price)
        loss_dist   = abs(sl - entry_price)

        if loss_dist > 0 and profit_dist / loss_dist < settings.min_profit_loss_ratio:
            if settings.sl_adjust_to_rr and profit_dist > 0:
                required = profit_dist / settings.min_profit_loss_ratio
                sl       = entry_price - required if side == 'BUY' else entry_price + required
                new_sl_pct = required / entry_price * 100 * (1.0 if side == 'BUY' else 1.5)
                if settings.min_sl_pct > 0 and new_sl_pct < settings.min_sl_pct:
                    return
                loss_dist = required
            else:
                return
        elif loss_dist == 0:
            return

        self._open_orders[name] = FakeOrder(
            side=side,
            entry_price=entry_price,
            tp=tp,
            sl=sl,
            level=rec.getLevel(),
            signal_type=rec.getType().value,
            candle_index=candle_index,
            partial_take_pct=settings.partial_take_pct,
            trailing_stop_pct=settings.trailing_stop_pct,
        )
        logger.info(
            f"[{name}] opened {side} @ {entry_price:.2f}  TP={tp:.2f}  SL={sl:.2f}"
        )

    # ------------------------------------------------------------------ #
    # Cooldown (same logic as backtester)                                  #
    # ------------------------------------------------------------------ #

    def _apply_cooldown(
        self, name: str, side: str, outcome: str, candle_index: int
    ) -> None:
        settings = dataclasses.replace(self._base, **self._presets[name])
        if settings.loss_streak_max <= 0:
            return
        cd    = self._cooldown[name]
        other = 'SELL' if side == 'BUY' else 'BUY'
        if outcome == 'loss':
            cd['consecutive_losses'][side] += 1
            cd['last_loss_candle'][side] = candle_index
            if cd['consecutive_losses'][side] >= settings.loss_streak_max:
                cd['blocked_until'][side] = candle_index + settings.loss_streak_cooldown_candles
                cd['consecutive_losses'][side] = 0
            if (
                settings.global_pause_trigger_candles > 0
                and cd['last_loss_candle'][other] > 0
                and (candle_index - cd['last_loss_candle'][other])
                    <= settings.global_pause_trigger_candles
            ):
                cd['global_blocked_until'] = candle_index + settings.global_pause_candles
        else:
            cd['consecutive_losses'][side] = 0

    # ------------------------------------------------------------------ #
    # Export to dashboard                                                  #
    # ------------------------------------------------------------------ #

    def _export(self) -> None:
        presets_out: dict = {}
        for name, result in self._results.items():
            d = result.to_dict()
            d['settings'] = self._presets[name]

            order = self._open_orders[name]
            if order is not None:
                unreal = (
                    (self._current_price - order.entry_price) / order.entry_price * 100
                    if order.side == 'BUY'
                    else (order.entry_price - self._current_price) / order.entry_price * 100
                )
                d['open_order'] = {
                    'side': order.side,
                    'entry': order.entry_price,
                    'tp': order.tp,
                    'sl': order.sl,
                    'partial_price': order._partial_price,
                    'open_candle': order.open_candle,
                    'best_price': round(order._best_price, 2),
                    'worst_price': round(order._worst_price, 2),
                    'max_tp_reach_pct': round(order.max_tp_reach_pct, 2),
                    'unrealized_pct': round(unreal, 4),
                    'armed': order._partial_armed,
                }
            else:
                d['open_order'] = None

            presets_out[name] = d

        payload = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'started_at': self._started_at,
            'symbol': self._base.symbol,
            'timeframe': self._base.timeframe,
            'current_price': self._current_price,
            'candle_index': self._candle_index,
            'presets': presets_out,
        }
        try:
            self._export_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._export_path, 'w') as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"Export error: {e}")

    # ------------------------------------------------------------------ #
    # State persistence                                                    #
    # ------------------------------------------------------------------ #

    def _save_state(self) -> None:
        state: dict = {
            'started_at': self._started_at,
            'candle_index': self._candle_index,
            'presets': {},
        }
        for name in self._presets:
            order = self._open_orders[name]
            state['presets'][name] = {
                'trades':     [t.to_dict() for t in self._results[name].trades],
                'open_order': order.get_state() if order is not None else None,
                'cooldown':   self._cooldown[name],
            }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"State save error: {e}")

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path) as f:
                state = json.load(f)
            self._started_at  = state.get('started_at', self._started_at)
            self._candle_index = state.get('candle_index', 0)

            for name, ps in state.get('presets', {}).items():
                if name not in self._presets:
                    continue
                pr = PresetResult(name)
                for t in ps.get('trades', []):
                    pr.trades.append(FakeOrder.from_closed_dict(t))
                self._results[name] = pr
                ost = ps.get('open_order')
                if ost is not None:
                    self._open_orders[name] = FakeOrder.from_state(ost)
                cd = ps.get('cooldown')
                if cd is not None:
                    self._cooldown[name] = cd

            open_count = sum(1 for o in self._open_orders.values() if o is not None)
            total_trades = sum(len(r.trades) for r in self._results.values())
            logger.info(
                f"State restored: {total_trades} closed trades, "
                f"{open_count} open orders, candle_index={self._candle_index}"
            )
        except Exception as e:
            logger.error(f"State load error (starting fresh): {e}")
