import dataclasses
import logging
from typing import Dict, List, Optional

from bot.analyzer import Analyzer
from bot.fake_order import FakeOrder
from bot.recommendation_engine import RecommendationEngine
from config.settings import Settings

logger = logging.getLogger(__name__)


class PresetResult:
    def __init__(self, name: str):
        self.name = name
        self.trades: List[FakeOrder] = []

    def add(self, order: FakeOrder) -> None:
        self.trades.append(order)

    # ------------------------------------------------------------------ #
    # Aggregate stats                                                      #
    # ------------------------------------------------------------------ #

    def total(self) -> int:
        return len(self.trades)

    def wins(self) -> int:
        return sum(1 for t in self.trades if t.result == 'win')

    def partials(self) -> int:
        return sum(1 for t in self.trades if t.result == 'partial')

    def trails(self) -> int:
        return sum(1 for t in self.trades if t.result == 'trail')

    def losses(self) -> int:
        return sum(1 for t in self.trades if t.result == 'loss')

    def win_rate(self) -> float:
        good = self.wins() + self.partials() + self.trails()
        return good / self.total() if self.total() else 0.0

    def total_profit_pct(self) -> float:
        return sum(t.profit_pct() or 0.0 for t in self.trades)

    def avg_rr(self) -> float:
        rrs = []
        for t in self.trades:
            profit_dist = abs(t.tp - t.entry_price)
            loss_dist = abs(t.sl - t.entry_price)
            if loss_dist > 0:
                rrs.append(profit_dist / loss_dist)
        return sum(rrs) / len(rrs) if rrs else 0.0

    def max_consecutive_losses(self) -> int:
        best = current = 0
        for t in self.trades:
            if t.result == 'loss':
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best

    def total_profit_pts(self) -> float:
        """Actual profit/loss in price points across all closed trades."""
        total = 0.0
        for t in self.trades:
            if t.close_price is None:
                continue
            if t.side == 'BUY':
                total += t.close_price - t.entry_price
            else:
                total += t.entry_price - t.close_price
        return total

    def potential_win_pts(self) -> float:
        """Total price-point gain if every trade had hit TP."""
        return sum(abs(t.tp - t.entry_price) for t in self.trades)

    def potential_loss_pts(self) -> float:
        """Total price-point loss if every trade had hit SL."""
        return sum(abs(t.sl - t.entry_price) for t in self.trades)

    def avg_max_tp_reach_pct(self) -> float:
        """
        Average % of TP distance reached on non-winning trades.
        High values here suggest partial_take_pct or trailing_stop_pct
        should be lowered — price kept getting close to TP without quite reaching it.
        """
        candidates = [t for t in self.trades if t.result != 'win']
        if not candidates:
            return 0.0
        return sum(t.max_tp_reach_pct for t in candidates) / len(candidates)

    def to_dict(self) -> dict:
        return {
            'preset': self.name,
            'total_trades': self.total(),
            'wins': self.wins(),
            'partials': self.partials(),
            'trails': self.trails(),
            'losses': self.losses(),
            'win_rate': round(self.win_rate(), 4),
            'total_profit_pct': round(self.total_profit_pct(), 4),
            'avg_rr': round(self.avg_rr(), 4),
            'max_consecutive_losses': self.max_consecutive_losses(),
            'total_profit_pts': round(self.total_profit_pts(), 2),
            'potential_win_pts': round(self.potential_win_pts(), 2),
            'potential_loss_pts': round(self.potential_loss_pts(), 2),
            'avg_max_tp_reach_pct': round(self.avg_max_tp_reach_pct(), 2),
            'trades': [t.to_dict() for t in self.trades],
        }


class Backtester:
    """
    Replays historical klines for one or more parameter presets.

    Each preset is a dict of Settings field overrides (e.g.
    {'min_profit_pct': 0.3, 'proximity_zone_pct': 15.0}).
    The base Settings object is never mutated — each preset gets its own
    copy via dataclasses.replace().

    Simulation rules:
    - Feed klines[0..i] to a fresh Analyzer on every candle close.
    - Entry: check synthetic price path through candle i+1 (open→low→high→close for
      ascending candles, open→high→low→close for descending). Enter at the first price
      that produces a valid signal, mirroring the 60-second tick checks in paper trading.
    - While a fake order is open: skip new signals (no stacking).
    - Same-candle TP+SL hit → loss (SL takes priority).
    """

    def __init__(self, base_settings: Settings):
        self._base = base_settings

    def run(
        self, klines: list, presets: Dict[str, dict]
    ) -> Dict[str, PresetResult]:
        results: Dict[str, PresetResult] = {}
        for name, overrides in presets.items():
            logger.info(f"Backtesting preset '{name}' over {len(klines)} candles …")
            results[name] = self._run_preset(klines, name, overrides)
            r = results[name]
            logger.info(
                f"  {name}: trades={r.total()} wins={r.wins()} partials={r.partials()} "
                f"trails={r.trails()} losses={r.losses()} win_rate={r.win_rate():.1%} "
                f"profit={r.total_profit_pct():.2f}% max_dd={r.max_consecutive_losses()}"
            )
        return results

    # ------------------------------------------------------------------ #
    # Single-preset replay                                                 #
    # ------------------------------------------------------------------ #

    def _run_preset(
        self, klines: list, name: str, overrides: dict
    ) -> PresetResult:
        settings = dataclasses.replace(self._base, **overrides)
        engine = RecommendationEngine(settings)
        analyzer = Analyzer(settings.swing_neighbours, engine)
        result = PresetResult(name)
        open_order: Optional[FakeOrder] = None

        # Candle-based directional cooldown state (only active when loss_streak_max > 0)
        consecutive_losses: Dict[str, int] = {'BUY': 0, 'SELL': 0}
        blocked_until: Dict[str, int] = {'BUY': 0, 'SELL': 0}
        last_loss_candle: Dict[str, int] = {'BUY': -9999, 'SELL': -9999}
        global_blocked_until: int = 0

        for i in range(len(klines)):
            candle = klines[i]
            open_price = float(candle[1])
            high = float(candle[2])
            low = float(candle[3])
            close_price = float(candle[4])

            # ── Check open order first ───────────────────────────────────
            if open_order is not None:
                outcome = open_order.check(high, low, i, candle_open=open_price, candle_close=close_price)
                if outcome is not None:
                    result.add(open_order)
                    if settings.loss_streak_max > 0:
                        side_closed = open_order.side
                        other_side = 'SELL' if side_closed == 'BUY' else 'BUY'
                        if outcome == 'loss':
                            consecutive_losses[side_closed] += 1
                            last_loss_candle[side_closed] = i
                            if consecutive_losses[side_closed] >= settings.loss_streak_max:
                                blocked_until[side_closed] = i + settings.loss_streak_cooldown_candles
                                consecutive_losses[side_closed] = 0
                                logger.debug(f"  {name}: {side_closed} blocked until candle {blocked_until[side_closed]}")
                            # Global pause: both sides lost close together
                            if (settings.global_pause_trigger_candles > 0
                                    and last_loss_candle[other_side] > 0
                                    and (i - last_loss_candle[other_side]) <= settings.global_pause_trigger_candles):
                                global_blocked_until = i + settings.global_pause_candles
                                logger.debug(f"  {name}: global pause until candle {global_blocked_until}")
                        else:
                            consecutive_losses[side_closed] = 0
                    open_order = None

            # ── Feed this candle to the analyzer ────────────────────────
            if i == 0:
                analyzer.build_from_klines([candle])
            else:
                analyzer.update_price(open_price)
                analyzer.add_candle(candle)

            # ── Try to open a new order if none is open ──────────────────
            if open_order is None:
                if i + 1 >= len(klines):
                    continue

                trend = analyzer.get_trend()
                if trend is None:
                    continue

                # Synthetic price path through candle i+1.
                # Ascending: open → low → high → close (price likely dips before rising).
                # Descending: open → high → low → close.
                # Enter at the first price that produces a valid signal, mirroring the
                # 60-second tick-based checks in paper trading.
                nk = klines[i + 1]
                nxt_o, nxt_h, nxt_l, nxt_c = float(nk[1]), float(nk[2]), float(nk[3]), float(nk[4])
                price_path = [nxt_o, nxt_l, nxt_h, nxt_c] if nxt_c >= nxt_o else [nxt_o, nxt_h, nxt_l, nxt_c]

                for entry_price in price_path:
                    analyzer.update_price(entry_price)
                    rec = engine.generate(trend, entry_price)
                    if rec is None:
                        continue

                    side = rec.getSide()
                    raw_tp = rec.getTarget()
                    sl = rec.getStop()

                    # Cooldown gate
                    if settings.loss_streak_max > 0:
                        if i < global_blocked_until or i < blocked_until.get(side, 0):
                            continue

                    if side == 'BUY':
                        if raw_tp <= entry_price or sl is None or sl >= entry_price:
                            continue
                        tp = entry_price + (raw_tp - entry_price) * settings.tp_multiplier
                        sl_dist_pct = (entry_price - sl) / entry_price * 100
                        profit_dist_pct = (tp - entry_price) / entry_price * 100
                    else:
                        if raw_tp >= entry_price or sl is None or sl <= entry_price:
                            continue
                        tp = entry_price - (entry_price - raw_tp) * settings.tp_multiplier
                        # SELL SL spikes are harsher; apply ×1.5 when checking min_sl_pct floor
                        sl_dist_pct = (sl - entry_price) / entry_price * 100 * 1.5
                        profit_dist_pct = (entry_price - tp) / entry_price * 100

                    # Absolute floor: SL must be at least 0.01% of entry away
                    if abs(sl - entry_price) < entry_price * 0.0001:
                        continue

                    if settings.max_profit_pct > 0 and profit_dist_pct > settings.max_profit_pct:
                        continue
                    if settings.min_sl_pct > 0 and sl_dist_pct < settings.min_sl_pct:
                        continue
                    if settings.max_sl_pct > 0 and sl_dist_pct > settings.max_sl_pct:
                        continue

                    profit_dist = abs(tp - entry_price)
                    loss_dist = abs(sl - entry_price)

                    if loss_dist > 0 and profit_dist / loss_dist < settings.min_profit_loss_ratio:
                        if settings.sl_adjust_to_rr and profit_dist > 0:
                            required_loss_dist = profit_dist / settings.min_profit_loss_ratio
                            if side == 'BUY':
                                sl = entry_price - required_loss_dist
                                new_sl_pct = required_loss_dist / entry_price * 100
                            else:
                                sl = entry_price + required_loss_dist
                                new_sl_pct = required_loss_dist / entry_price * 100 * 1.5
                            if settings.min_sl_pct > 0 and new_sl_pct < settings.min_sl_pct:
                                continue
                            loss_dist = required_loss_dist
                        else:
                            continue
                    elif loss_dist == 0:
                        continue

                    open_order = FakeOrder(
                        side=side,
                        entry_price=entry_price,
                        tp=tp,
                        sl=sl,
                        level=rec.getLevel(),
                        signal_type=rec.getType().value,
                        candle_index=i + 1,
                        partial_take_pct=settings.partial_take_pct,
                        trailing_stop_pct=settings.trailing_stop_pct,
                    )
                    break  # entered — stop checking this candle's price path

        # Any order still open at end-of-data is discarded.
        return result
