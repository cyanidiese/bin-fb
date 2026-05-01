from typing import Optional


class FakeOrder:
    """
    Simulates an open futures order during backtesting.

    BUY: wins when high >= tp, loses when low <= sl.
    SELL: wins when low <= tp, loses when high >= sl.
    Same-candle TP+SL hit → loss (SL priority, conservative) when not armed.

    ── Partial take (two-stage) ─────────────────────────────────────────────
    Requires partial_take_pct > 0, trailing_stop_pct == 0.

      Stage 1 — arm: the first candle whose favorable extreme reaches
        partial_price marks the order as armed. Captured BEFORE this
        candle's outcome checks — same candle cannot both arm and trigger.
      Stage 2 — trigger: on any later candle, if the unfavorable extreme
        crosses back below/above partial_price the order closes at
        partial_price as a 'partial' win.

    ── Trailing stop ────────────────────────────────────────────────────────
    Requires partial_take_pct > 0 AND trailing_stop_pct > 0.

      Arming is identical: partial_price is the activation threshold.
      Once armed, _max_favorable tracks the highest high (BUY) /
        lowest low (SELL) seen.
      On each subsequent candle:
        trail_price = _max_favorable - trailing_stop_pct * (_max_favorable - entry)  [BUY]
        trail_price = _max_favorable + trailing_stop_pct * (entry - _max_favorable)  [SELL]
      If the unfavorable extreme crosses trail_price → close at trail_price
        as a 'trail' win. This replaces the fixed partial retrace trigger.

    Priority when armed: TP > trail/partial > SL (SL is a safety net;
      trail_price is always above SL so SL is logically unreachable when armed).
    Priority when not armed: SL > TP (conservative default).
      Exception: when candle_open and candle_close are supplied to check(),
      candle direction determines same-candle priority. Ascending candle
      (close > open) → high reached before low → BUY TP wins; SELL SL wins.
      Descending candle (close < open) → low reached before high → BUY SL
      wins; SELL TP wins. Without candle_open/close the conservative
      SL-first default is preserved.

    ── Price tracking (informational) ───────────────────────────────────────
      best_price   — most favorable price seen (max high BUY / min low SELL)
      worst_price  — most adverse price seen (min low BUY / max high SELL)
      max_tp_reach_pct — % of TP distance covered by best_price (0 – 100+)
    """

    def __init__(
        self,
        side: str,
        entry_price: float,
        tp: float,
        sl: float,
        level: Optional[int],
        signal_type: str,
        candle_index: int,
        partial_take_pct: float = 0.0,
        trailing_stop_pct: float = 0.0,
    ):
        self.side = side
        self.entry_price = entry_price
        self.tp = tp
        self.sl = sl
        self.level = level
        self.signal_type = signal_type
        self.open_candle = candle_index
        self.close_candle: Optional[int] = None
        self.result: Optional[str] = None   # 'win' | 'partial' | 'trail' | 'loss'
        self.close_price: Optional[float] = None

        # Price tracking
        self._best_price = entry_price
        self._worst_price = entry_price

        # Partial / trailing stop
        self._trailing_stop_pct = trailing_stop_pct
        self._partial_armed = False

        if partial_take_pct > 0:
            if side == 'BUY':
                self._partial_price: Optional[float] = entry_price + partial_take_pct * (tp - entry_price)
            else:
                self._partial_price = entry_price - partial_take_pct * (entry_price - tp)
            # _max_favorable starts at the arm threshold; only updated while armed
            self._max_favorable = self._partial_price
        else:
            self._partial_price = None
            self._max_favorable = entry_price

    # ------------------------------------------------------------------ #
    # Per-candle check                                                     #
    # ------------------------------------------------------------------ #

    def check(
        self,
        high: float,
        low: float,
        candle_index: int,
        candle_open: Optional[float] = None,
        candle_close: Optional[float] = None,
    ) -> Optional[str]:
        """
        Check whether this candle closes the order.
        Returns 'win', 'partial', 'trail', 'loss', or None (still open).
        Provide candle_open/candle_close to use candle-direction priority on
        same-candle TP+SL hits (ascending → high before low; descending → vice-versa).
        """
        # Track best/worst prices every candle.
        if self.side == 'BUY':
            self._best_price = max(self._best_price, high)
            self._worst_price = min(self._worst_price, low)
        else:
            self._best_price = min(self._best_price, low)
            self._worst_price = max(self._worst_price, high)

        # Capture armed state BEFORE updating it for this candle.
        was_armed = self._partial_armed
        if self._partial_price is not None and not self._partial_armed:
            if (self.side == 'BUY' and high >= self._partial_price) or \
               (self.side == 'SELL' and low <= self._partial_price):
                self._partial_armed = True

        # Update _max_favorable while armed (including the arming candle itself).
        if self._partial_armed:
            if self.side == 'BUY':
                self._max_favorable = max(self._max_favorable, high)
            else:
                self._max_favorable = min(self._max_favorable, low)

        # Outcome flags
        if self.side == 'BUY':
            sl_hit = low <= self.sl
            tp_hit = high >= self.tp
        else:
            sl_hit = high >= self.sl
            tp_hit = low <= self.tp

        if was_armed:
            if tp_hit:
                self._close('win', self.tp, candle_index)
                return 'win'

            if self._trailing_stop_pct > 0 and self._partial_price is not None:
                trail_result = self._check_trail(high, low, candle_index)
                if trail_result is not None:
                    return trail_result
            else:
                # Fixed partial retrace trigger (original behaviour)
                if self.side == 'BUY' and low < self._partial_price:  # type: ignore[operator]
                    self._close('partial', self._partial_price, candle_index)  # type: ignore[arg-type]
                    return 'partial'
                if self.side == 'SELL' and high > self._partial_price:  # type: ignore[operator]
                    self._close('partial', self._partial_price, candle_index)  # type: ignore[arg-type]
                    return 'partial'

            if sl_hit:  # safety net — logically unreachable when trail is active
                self._close('loss', self.sl, candle_index)
                return 'loss'
        else:
            if sl_hit and tp_hit and candle_open is not None and candle_close is not None:
                is_asc = candle_close > candle_open
                if self.side == 'BUY':
                    winner = 'win' if is_asc else 'loss'
                else:
                    winner = 'loss' if is_asc else 'win'
                if winner == 'win':
                    self._close('win', self.tp, candle_index)
                else:
                    self._close('loss', self.sl, candle_index)
                return winner
            # Conservative: SL beats TP on same-candle spikes
            if sl_hit:
                self._close('loss', self.sl, candle_index)
                return 'loss'
            if tp_hit:
                self._close('win', self.tp, candle_index)
                return 'win'

        return None

    def _check_trail(self, high: float, low: float, candle_index: int) -> Optional[str]:
        if self.side == 'BUY':
            gained = self._max_favorable - self.entry_price
            if gained <= 0:
                return None
            trail_price = self._max_favorable - self._trailing_stop_pct * gained
            if low <= trail_price:
                self._close('trail', trail_price, candle_index)
                return 'trail'
        else:
            gained = self.entry_price - self._max_favorable
            if gained <= 0:
                return None
            trail_price = self._max_favorable + self._trailing_stop_pct * gained
            if high >= trail_price:
                self._close('trail', trail_price, candle_index)
                return 'trail'
        return None

    # ------------------------------------------------------------------ #
    # Stats helpers                                                        #
    # ------------------------------------------------------------------ #

    def profit_pct(self) -> Optional[float]:
        """Profit/loss as % of entry. Positive = win/partial/trail, negative = loss."""
        if self.close_price is None:
            return None
        raw = (self.close_price - self.entry_price) / self.entry_price * 100
        return raw if self.side == 'BUY' else -raw

    @property
    def max_tp_reach_pct(self) -> float:
        """How much of the TP distance best_price covered (0 – 100+%)."""
        tp_dist = abs(self.tp - self.entry_price)
        if tp_dist == 0:
            return 0.0
        favorable = (
            self._best_price - self.entry_price if self.side == 'BUY'
            else self.entry_price - self._best_price
        )
        return max(0.0, favorable / tp_dist * 100.0)

    @property
    def max_favorable_pct(self) -> float:
        """Best price move as % of entry (always positive)."""
        if self.side == 'BUY':
            return (self._best_price - self.entry_price) / self.entry_price * 100.0
        return (self.entry_price - self._best_price) / self.entry_price * 100.0

    @property
    def max_adverse_pct(self) -> float:
        """Worst price move against entry as % of entry (always positive)."""
        if self.side == 'BUY':
            return (self.entry_price - self._worst_price) / self.entry_price * 100.0
        return (self._worst_price - self.entry_price) / self.entry_price * 100.0

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def get_state(self) -> dict:
        """Serialize full internal state for persistence (open orders survive restarts)."""
        return {
            'side': self.side,
            'entry_price': self.entry_price,
            'tp': self.tp,
            'sl': self.sl,
            'level': self.level,
            'signal_type': self.signal_type,
            'open_candle': self.open_candle,
            '_partial_price': self._partial_price,
            '_trailing_stop_pct': self._trailing_stop_pct,
            '_partial_armed': self._partial_armed,
            '_best_price': self._best_price,
            '_worst_price': self._worst_price,
            '_max_favorable': self._max_favorable,
        }

    @classmethod
    def from_state(cls, state: dict) -> 'FakeOrder':
        """Reconstruct an open FakeOrder from a saved state dict."""
        obj = cls.__new__(cls)
        obj.side = state['side']
        obj.entry_price = state['entry_price']
        obj.tp = state['tp']
        obj.sl = state['sl']
        obj.level = state['level']
        obj.signal_type = state['signal_type']
        obj.open_candle = state['open_candle']
        obj.close_candle = None
        obj.result = None
        obj.close_price = None
        obj._partial_price = state['_partial_price']
        obj._trailing_stop_pct = state['_trailing_stop_pct']
        obj._partial_armed = state['_partial_armed']
        obj._best_price = state['_best_price']
        obj._worst_price = state['_worst_price']
        obj._max_favorable = state['_max_favorable']
        return obj

    @classmethod
    def from_closed_dict(cls, d: dict) -> 'FakeOrder':
        """Reconstruct a closed FakeOrder from to_dict() output (for stats restoration)."""
        obj = cls.__new__(cls)
        obj.side = d['side']
        obj.entry_price = d['entry']
        obj.tp = d['tp']
        obj.sl = d['sl']
        obj.level = d.get('level')
        obj.signal_type = d.get('signal_type', '')
        obj.open_candle = d.get('open_candle', 0)
        obj.close_candle = d.get('close_candle')
        obj.result = d.get('result')
        obj.close_price = d.get('close_price')
        obj._partial_price = d.get('partial_price')
        obj._trailing_stop_pct = 0.0
        obj._partial_armed = False
        obj._best_price = d.get('best_price', d['entry'])
        obj._worst_price = d.get('worst_price', d['entry'])
        obj._max_favorable = d.get('best_price', d['entry'])
        return obj

    def _close(self, result: str, price: float, candle_index: int) -> None:
        self.result = result
        self.close_price = round(price, 8)
        self.close_candle = candle_index

    def to_dict(self) -> dict:
        return {
            'side': self.side,
            'level': self.level,
            'signal_type': self.signal_type,
            'entry': self.entry_price,
            'tp': self.tp,
            'sl': self.sl,
            'partial_price': self._partial_price,
            'result': self.result,
            'close_price': self.close_price,
            'profit_pct': round(self.profit_pct(), 4) if self.profit_pct() is not None else None,
            'open_candle': self.open_candle,
            'close_candle': self.close_candle,
            'best_price': round(self._best_price, 8),
            'worst_price': round(self._worst_price, 8),
            'max_tp_reach_pct': round(self.max_tp_reach_pct, 2),
            'max_favorable_pct': round(self.max_favorable_pct, 4),
            'max_adverse_pct': round(self.max_adverse_pct, 4),
        }
