from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from bot.fake_order import FakeOrder
from bot.recommendation_engine import RecommendationEngine

if TYPE_CHECKING:
    from bot.analyzer import Analyzer
    from bot.data_feed import DataFeed
    from bot.virtual_tracker import VirtualTracker
    from config.settings import Settings

logger = logging.getLogger(__name__)

_MAX_CLOSED = 500
_DEFAULT_MIN_NOTIONAL = 5.0
_DEFAULT_RANK_MAX = 6   # fallback; callers pass len(all_presets) at runtime


def _tf_to_ms(timeframe: str) -> int:
    units = {'m': 60_000, 'h': 3_600_000, 'd': 86_400_000}
    return int(timeframe[:-1]) * units.get(timeframe[-1], 60_000)


class VirtualOrderSimulator:
    """
    Tracks rank-based virtual positions for the top N non-best presets per symbol.

    Rank 1 = best preset → real order (not tracked here).
    Ranks 2..rank_max → each rank has one independent balance pool shared across
    all symbols.  At any candle close, each symbol contributes at most one open
    position per rank.

    When the preset holding rank N for a symbol changes (efficiency rankings
    shift), the existing position is evicted at the current price and the new
    rank-N preset opens fresh.  This means the rank-N pool always tracks
    "how would you do if you always traded whichever preset is currently rank N?"

    Real balance (RiskManager) is never touched here.
    """

    def __init__(
        self,
        mode: str,
        all_presets: dict,
        project_root: Path,
        get_leverage: Callable[[str], int],
        initial_balance: float,
        virtual_tracker: 'VirtualTracker',
        min_notionals: dict[str, float],
        get_allocation: Optional[Callable[[str, float], float]] = None,
        get_bgf_allocation: Optional[Callable[[float, float], float]] = None,
        get_scenario: Optional[Callable[[], str]] = None,
        rank_max: int = _DEFAULT_RANK_MAX,
        is_rank_disabled: Optional[Callable[[str, int], bool]] = None,
    ) -> None:
        self._mode = mode
        self._all_presets = all_presets
        self._project_root = project_root
        self._get_leverage = get_leverage
        self._virtual_tracker = virtual_tracker
        self._min_notionals = min_notionals
        self._get_allocation = get_allocation
        self._get_bgf_allocation = get_bgf_allocation
        self._get_scenario = get_scenario
        self._rank_max = rank_max
        self._is_rank_disabled = is_rank_disabled
        self._initial_balance = initial_balance
        # Candle-level allocation context — set by main.py before each on_candle_close call
        self._uses_weight_alloc: bool = True
        self._bgf_fractions: dict[str, float] = {}

        # rank -> symbol -> open order record
        self._rank_open: dict[int, dict[str, dict]] = {}
        # rank -> symbol -> FakeOrder
        self._rank_fake: dict[int, dict[str, FakeOrder]] = {}
        # rank -> current balance
        self._rank_balance: dict[int, float] = {}
        # rank -> asyncio.Lock (for file writes)
        self._rank_locks: dict[int, asyncio.Lock] = {}

        # Duplicate-signal skip: records last SL-hit signal per "symbol:preset" key
        self._recent_sl_hit: dict[str, dict] = {}
        self._lot_cache: dict = {}

        for r in range(2, self._rank_max + 1):
            self._rank_open[r] = {}
            self._rank_fake[r] = {}
            self._rank_balance[r] = initial_balance
            self._rank_locks[r] = asyncio.Lock()
            self._load_rank_balance(r)

    def set_lot_cache(self, cache: dict) -> None:
        """Share OrderExecutor's lot cache so virtual orders respect exchange maxQty."""
        self._lot_cache = cache

    # ------------------------------------------------------------------ #
    # Balance persistence                                                  #
    # ------------------------------------------------------------------ #

    def _rank_balance_path(self, rank: int) -> Path:
        return self._project_root / 'data' / f'virtual_balance_rank{rank}_{self._mode}.json'

    def _rank_orders_path(self, rank: int, symbol: str) -> Path:
        return self._project_root / 'data' / f'virtual_orders_rank{rank}_{symbol}_{self._mode}.json'

    def _load_rank_balance(self, rank: int) -> None:
        path = self._rank_balance_path(rank)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._rank_balance[rank] = float(data.get('balance', self._rank_balance[rank]))
        except Exception as exc:
            logger.warning(f"Rank-{rank} balance load failed: {exc}")

    def _save_rank_balance(self, rank: int) -> None:
        path = self._rank_balance_path(rank)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({'balance': self._rank_balance[rank]}))
        tmp.replace(path)

    def get_rank_balances(self) -> dict[int, float]:
        """Return a snapshot of all rank pool balances."""
        return dict(self._rank_balance)

    def get_open_positions(self) -> list[dict]:
        """Return all currently open virtual positions as serialisable dicts."""
        result = []
        for rank, by_sym in self._rank_open.items():
            for symbol, record in by_sym.items():
                result.append({**record, 'symbol': symbol})
        return result

    def sync_real_balance_on_start(self, balance: float) -> None:
        """Sync every rank pool balance to the real account balance on each bot start.
        Ensures virtual pools begin each session from the same baseline as the real account."""
        if balance <= 0:
            return
        for rank in range(2, self._rank_max + 1):
            self._rank_balance[rank] = balance
            self._save_rank_balance(rank)
            logger.info(f"Rank-{rank} virtual balance synced to real account: {balance:.2f} USDT")

    def set_candle_alloc_context(
        self, uses_weight_alloc: bool, bgf_fractions: dict[str, float]
    ) -> None:
        """Set the allocation context for the current candle batch.
        Must be called by main.py before on_candle_close so _try_open uses the correct formula."""
        self._uses_weight_alloc = uses_weight_alloc
        self._bgf_fractions = bgf_fractions

    # ------------------------------------------------------------------ #
    # Candle close — open / evict rank positions                          #
    # ------------------------------------------------------------------ #

    async def on_candle_close(
        self,
        symbol: str,
        analyzer: 'Analyzer',
        best_preset_name: Optional[str],
        base_settings: 'Settings',
    ) -> None:
        sorted_presets = sorted(
            self._all_presets.items(),
            key=lambda kv: self._virtual_tracker.get_preset_efficiency(symbol, kv[0]),
            reverse=True,
        )
        current_price = analyzer.get_current_price()
        lev = self._get_leverage(symbol)
        min_notional = self._min_notionals.get(symbol, _DEFAULT_MIN_NOTIONAL)

        for rank in range(2, self._rank_max + 1):
            # Skip this rank if it has been disabled for this symbol
            if self._is_rank_disabled and self._is_rank_disabled(symbol, rank):
                if symbol in self._rank_open[rank]:
                    await self._evict(symbol, rank, current_price, 'rank_disabled')
                continue

            rank_idx = rank - 1  # 0-based: rank 1 = idx 0 (best), rank 2 = idx 1, …
            if rank_idx >= len(sorted_presets):
                # Not enough presets → evict if anything open
                if symbol in self._rank_open[rank]:
                    await self._evict(symbol, rank, current_price, 'insufficient_presets')
                continue

            preset_name, overrides = sorted_presets[rank_idx]

            # Evict if the preset at this rank changed
            existing = self._rank_open[rank].get(symbol)
            if existing and existing['preset_name'] != preset_name:
                await self._evict(symbol, rank, current_price, 'rank_change')

            # Open if slot is empty
            if symbol not in self._rank_open[rank]:
                await self._try_open(
                    symbol, rank, preset_name, overrides,
                    base_settings, lev, min_notional, analyzer,
                )

    async def _evict(self, symbol: str, rank: int, price: float, reason: str) -> None:
        record = self._rank_open[rank].pop(symbol, None)
        fake = self._rank_fake[rank].pop(symbol, None)
        if record is None:
            return
        close_price = (fake.close_price if fake and fake.close_price else None) or price
        pnl = self._calc_pnl(record, close_price)
        self._rank_balance[rank] += pnl
        self._save_rank_balance(rank)
        record.update({
            'status': 'closed',
            'close_price': close_price,
            'close_time': datetime.now(timezone.utc).isoformat(),
            'pnl_usdt': pnl,
            'result': reason,
            'rank_balance_after': self._rank_balance[rank],
        })
        await self._append_rank_closed(symbol, rank, record)
        logger.info(
            f"[{symbol}] Rank-{rank} evicted ({reason}): "
            f"{record['preset_name']} pnl={pnl:.2f} bal={self._rank_balance[rank]:.2f}"
        )

    async def _try_open(
        self,
        symbol: str,
        rank: int,
        preset_name: str,
        overrides: dict,
        base_settings: 'Settings',
        lev: int,
        min_notional: float,
        analyzer: 'Analyzer',
    ) -> None:
        try:
            preset_settings = dataclasses.replace(base_settings, **overrides)
            engine = RecommendationEngine(preset_settings)
            rec = engine.generate(analyzer.get_trend(), analyzer.get_current_price())
        except Exception as exc:
            logger.debug(f"[{symbol}][{preset_name}] Rank-{rank} rec error: {exc}")
            return

        if rec is None:
            return

        entry = rec.getEntryPrice()
        tp = rec.getTarget()
        sl = rec.getStop() or 0.0
        if entry <= 0 or tp <= 0 or sl <= 0:
            return

        # Duplicate-signal skip
        if preset_settings.duplicate_skip_candles > 0:
            _key = f"{symbol}:{preset_name}"
            _prev = self._recent_sl_hit.get(_key)
            if _prev:
                _now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                _dur = _tf_to_ms(base_settings.timeframe)
                _candles_since = (_now_ms - _prev['ts_ms']) // _dur if _dur else 0
                if _candles_since <= preset_settings.duplicate_skip_candles:
                    _side = rec.getSide()
                    if _side == _prev['side']:
                        _p = preset_settings.duplicate_skip_pct / 100.0
                        if (_prev['entry'] > 0 and abs(entry - _prev['entry']) / _prev['entry'] <= _p and
                                _prev['sl'] > 0 and abs(sl - _prev['sl']) / _prev['sl'] <= _p and
                                _prev['tp'] > 0 and abs(tp - _prev['tp']) / _prev['tp'] <= _p):
                            logger.debug(
                                f"[{symbol}] Rank-{rank} duplicate skip: "
                                f"similar to SL-hit signal {_candles_since} candle(s) ago"
                            )
                            return

        # Size from the rank pool balance using the same allocation formula as real orders,
        # substituting the rank pool's own balance instead of the real account balance.
        # For BGF scenarios: use score-proportional fraction of the rank pool's deployable budget.
        # For weight-allocation scenarios: use the weight-split formula (existing behaviour).
        rank_bal = self._rank_balance.get(rank, self._initial_balance)
        fraction = self._bgf_fractions.get(symbol) if not self._uses_weight_alloc else None
        if fraction is not None and self._get_bgf_allocation is not None:
            alloc = self._get_bgf_allocation(rank_bal, fraction)
        else:
            alloc = self._get_allocation(symbol, rank_bal) if self._get_allocation else rank_bal * 0.05
        quantity = max(alloc, min_notional) * lev / entry if entry > 0 else 0.0
        # Respect Binance per-symbol maxQty so virtual sizes match what real orders can place
        _max_qty = self._lot_cache.get(symbol, {}).get('max_qty', 0.0)
        if _max_qty > 0 and quantity > _max_qty:
            logger.debug(f"[{symbol}] Virtual qty {quantity:.0f} capped to exchange maxQty {_max_qty:.0f}")
            quantity = _max_qty
        if quantity <= 0:
            return

        side = rec.getSide()
        partial_pct = float(getattr(preset_settings, 'partial_take_pct', 0.0))
        trail_pct = float(getattr(preset_settings, 'trailing_stop_pct', 0.0))

        record = {
            'preset_name': preset_name,
            'rank': rank,
            'side': side,
            'entry_price': entry,
            'tp': tp,
            'sl': sl,
            'quantity': quantity,
            'leverage': lev,
            'scenario': self._get_scenario() if self._get_scenario else '',
            'rank_balance_at_open': self._rank_balance[rank],
            'open_time': datetime.now(timezone.utc).isoformat(),
            'status': 'open',
            'close_price': None,
            'close_time': None,
            'pnl_usdt': None,
            'result': None,
        }
        self._rank_open[rank][symbol] = record

        _max_losing_amt = float(getattr(preset_settings, 'max_losing_amount_usdt', 0.0))
        _early_loss_sl = 0.0
        if _max_losing_amt > 0 and quantity > 0:
            if side == 'BUY':
                _early_loss_sl = entry - _max_losing_amt / quantity
            else:
                _early_loss_sl = entry + _max_losing_amt / quantity

        self._rank_fake[rank][symbol] = FakeOrder(
            side=side,
            entry_price=entry,
            tp=tp,
            sl=sl,
            level=rec.getLevel(),
            signal_type=rec.getType().value,
            candle_index=0,
            partial_take_pct=partial_pct,
            trailing_stop_pct=trail_pct,
            max_losing_pct=float(getattr(preset_settings, 'max_losing_pct', 0.0)),
            max_losing_candles=int(getattr(preset_settings, 'max_losing_candles', 0)),
            early_loss_sl=_early_loss_sl,
        )
        logger.debug(
            f"[{symbol}] Rank-{rank} opened: {preset_name} {side} @ {entry} "
            f"bal={self._rank_balance[rank]:.2f}"
        )

    # ------------------------------------------------------------------ #
    # Price tick — check TP/SL                                            #
    # ------------------------------------------------------------------ #

    async def check_prices(self, symbol: str, price: float) -> list[dict]:
        closed: list[dict] = []
        for rank in range(2, self._rank_max + 1):
            fake = self._rank_fake[rank].get(symbol)
            record = self._rank_open[rank].get(symbol)
            if fake is None or record is None:
                continue

            result = fake.check_price(price)
            if result is None:
                continue

            self._rank_open[rank].pop(symbol)
            self._rank_fake[rank].pop(symbol)

            close_price = fake.close_price or price
            pnl = self._calc_pnl(record, close_price)
            self._rank_balance[rank] += pnl
            self._save_rank_balance(rank)

            record.update({
                'status': 'closed',
                'close_price': close_price,
                'close_time': datetime.now(timezone.utc).isoformat(),
                'pnl_usdt': pnl,
                'result': result,
                'rank_balance_after': self._rank_balance[rank],
            })
            if result == 'loss':
                self._recent_sl_hit[f"{symbol}:{record['preset_name']}"] = {
                    'ts_ms': int(datetime.now(timezone.utc).timestamp() * 1000),
                    'side': record['side'],
                    'entry': record['entry_price'],
                    'sl': record.get('sl', 0.0),
                    'tp': record.get('tp', 0.0),
                }
            await self._append_rank_closed(symbol, rank, record)
            closed.append({
                'preset_name': record['preset_name'],
                'rank': rank,
                'pnl_usdt': pnl,
                'result': result,
                'entry_price': record['entry_price'],
                'close_price': close_price,
                'side': record['side'],
            })
            logger.debug(
                f"[{symbol}] Rank-{rank} closed: {record['preset_name']} "
                f"{result} pnl={pnl:.2f} bal={self._rank_balance[rank]:.2f}"
            )
        return closed

    # ------------------------------------------------------------------ #
    # Shutdown — close all open positions at market                       #
    # ------------------------------------------------------------------ #

    async def close_all_open(self, symbols: list[str], feed: 'DataFeed') -> None:
        price_cache: dict[str, float] = {}
        for symbol in symbols:
            for rank in range(2, self._rank_max + 1):
                if symbol not in self._rank_open[rank]:
                    continue
                if symbol not in price_cache:
                    try:
                        ticker = await asyncio.to_thread(
                            feed.client.futures_symbol_ticker, symbol=symbol
                        )
                        price_cache[symbol] = float(ticker.get('price', 0) or 0)
                    except Exception as exc:
                        logger.warning(f"[{symbol}] Price fetch for rank-{rank} close failed: {exc}")
                        price_cache[symbol] = 0.0
                price = price_cache[symbol]
                record = self._rank_open[rank].pop(symbol)
                self._rank_fake[rank].pop(symbol, None)
                close_price = price if price > 0 else record['entry_price']
                pnl = self._calc_pnl(record, close_price) if price > 0 else 0.0
                self._rank_balance[rank] += pnl
                record.update({
                    'status': 'closed',
                    'close_price': close_price,
                    'close_time': datetime.now(timezone.utc).isoformat(),
                    'pnl_usdt': pnl,
                    'result': 'closed_early',
                    'rank_balance_after': self._rank_balance[rank],
                })
                await self._append_rank_closed(symbol, rank, record)
        self._save_all_rank_balances()
        logger.info("All rank virtual positions closed (shutdown)")

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    _TAKER_FEE_RATE: float = 0.0004

    def _calc_pnl(self, record: dict, close_price: float) -> float:
        entry = record['entry_price']
        qty = record['quantity']
        if record['side'] == 'BUY':
            raw = (close_price - entry) * qty
        else:
            raw = (entry - close_price) * qty
        fees = (entry + close_price) * qty * self._TAKER_FEE_RATE
        return raw - fees

    def _save_all_rank_balances(self) -> None:
        for rank in range(2, self._rank_max + 1):
            self._save_rank_balance(rank)

    async def _append_rank_closed(self, symbol: str, rank: int, record: dict) -> None:
        async with self._rank_locks[rank]:
            path = self._rank_orders_path(rank, symbol)
            path.parent.mkdir(parents=True, exist_ok=True)
            existing: list = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except Exception:
                    existing = []
            closed = [r for r in existing if r.get('status') != 'open']
            closed.append(record)
            if len(closed) > _MAX_CLOSED:
                closed = closed[-_MAX_CLOSED:]
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(closed))
            tmp.replace(path)
