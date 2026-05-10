from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bot.fake_order import FakeOrder
from bot.recommendation_engine import RecommendationEngine

if TYPE_CHECKING:
    from bot.analyzer import Analyzer
    from bot.data_feed import DataFeed
    from bot.leverage_tracker import LeverageTracker
    from bot.virtual_tracker import VirtualTracker
    from config.settings import Settings

logger = logging.getLogger(__name__)

_MAX_CLOSED = 500
_DEFAULT_MIN_NOTIONAL = 5.0


class VirtualOrderSimulator:
    """
    Tracks virtual positions for all non-best presets.
    Uses a shared virtual balance pool sized by Option A: min_notional / leverage.
    Presets are sorted by efficiency before opening so capital goes to best performers first.
    Persists virtual_orders_{symbol}_{mode}.json and virtual_balance_{mode}.json.
    """

    def __init__(
        self,
        mode: str,
        all_presets: dict,
        project_root: Path,
        leverage_tracker: 'LeverageTracker',
        initial_balance: float,
        virtual_tracker: 'VirtualTracker',
        min_notionals: dict[str, float],
    ) -> None:
        self._mode = mode
        self._all_presets = all_presets
        self._project_root = project_root
        self._leverage_tracker = leverage_tracker
        self._virtual_tracker = virtual_tracker
        self._min_notionals = min_notionals
        # symbol -> {preset_name: order_record_dict}
        self._open: dict[str, dict[str, dict]] = {}
        # symbol -> {preset_name: FakeOrder}
        self._fake_orders: dict[str, dict[str, FakeOrder]] = {}
        # per-symbol write locks to prevent concurrent _append_closed races
        self._write_locks: dict[str, asyncio.Lock] = {}

        self._virtual_balance: float = initial_balance
        self._virtual_committed: float = 0.0
        self._balance_path = project_root / 'data' / f'virtual_balance_{mode}.json'
        self._load_virtual_balance()

    def _load_virtual_balance(self) -> None:
        if not self._balance_path.exists():
            return
        try:
            data = json.loads(self._balance_path.read_text())
            self._virtual_balance = float(data.get('virtual_balance', self._virtual_balance))
            self._virtual_committed = float(data.get('virtual_committed', 0.0))
        except Exception as exc:
            logger.warning(f"VirtualOrderSimulator: failed to load balance state: {exc}")

    def _save_virtual_balance(self) -> None:
        self._balance_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._balance_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({
            'virtual_balance': self._virtual_balance,
            'virtual_committed': self._virtual_committed,
        }))
        tmp.replace(self._balance_path)

    async def on_candle_close(
        self,
        symbol: str,
        analyzer: 'Analyzer',
        best_preset_name: Optional[str],
        base_settings: 'Settings',
    ) -> None:
        open_for_symbol = self._open.setdefault(symbol, {})
        fake_for_symbol = self._fake_orders.setdefault(symbol, {})

        lev = self._leverage_tracker.get_current_level()
        min_notional = self._min_notionals.get(symbol, _DEFAULT_MIN_NOTIONAL)
        margin = min_notional / lev if lev > 0 else min_notional

        # Sort presets by efficiency descending — best preset gets capital first
        sorted_presets = sorted(
            self._all_presets.items(),
            key=lambda kv: self._virtual_tracker.get_preset_efficiency(symbol, kv[0]),
            reverse=True,
        )

        for preset_name, overrides in sorted_presets:
            if preset_name == best_preset_name:
                continue  # handled as real order
            if preset_name in open_for_symbol:
                continue  # already open for this preset

            available = self._virtual_balance - self._virtual_committed
            if available < margin:
                logger.debug(
                    f"[{symbol}][{preset_name}] Virtual skip: "
                    f"available={available:.2f} < margin={margin:.2f}"
                )
                continue

            try:
                preset_settings = dataclasses.replace(base_settings, **overrides)
                engine = RecommendationEngine(preset_settings)
                rec = engine.generate(analyzer.get_trend(), analyzer.get_current_price())
            except Exception as exc:
                logger.debug(f"[{symbol}][{preset_name}] Recommendation error: {exc}")
                continue

            if rec is None:
                continue

            entry = rec.getEntryPrice()
            tp = rec.getTarget()
            sl = rec.getStop() or 0.0
            if entry <= 0 or tp <= 0:
                continue

            quantity = min_notional / entry if entry > 0 else 0.0
            if quantity <= 0:
                continue

            self._virtual_committed += margin
            side = rec.getSide()
            partial_pct = float(getattr(preset_settings, 'partial_take_pct', 0.0))
            trail_pct = float(getattr(preset_settings, 'trailing_stop_pct', 0.0))

            record = {
                'preset_name': preset_name,
                'side': side,
                'entry_price': entry,
                'tp': tp,
                'sl': sl,
                'quantity': quantity,
                'leverage': lev,
                'virtual_margin': margin,
                'virtual_balance_at_open': self._virtual_balance,
                'open_time': datetime.now(timezone.utc).isoformat(),
                'status': 'open',
                'close_price': None,
                'close_time': None,
                'pnl_usdt': None,
                'result': None,
            }
            self._save_virtual_balance()
            open_for_symbol[preset_name] = record
            fake_for_symbol[preset_name] = FakeOrder(
                side=side,
                entry_price=entry,
                tp=tp,
                sl=sl if sl else entry * (0.99 if side == 'BUY' else 1.01),
                level=rec.getLevel(),
                signal_type=rec.getType().value,
                candle_index=0,
                partial_take_pct=partial_pct,
                trailing_stop_pct=trail_pct,
            )
            logger.debug(f"[{symbol}] Virtual order opened: {preset_name} {side} @ {entry}")

    async def check_prices(self, symbol: str, price: float) -> list[dict]:
        closed = []
        open_for_symbol = self._open.get(symbol, {})
        fake_for_symbol = self._fake_orders.get(symbol, {})

        for preset_name in list(open_for_symbol.keys()):
            fake = fake_for_symbol.get(preset_name)
            if fake is None:
                open_for_symbol.pop(preset_name, None)
                continue

            result = fake.check_price(price)
            if result is None:
                continue

            record = open_for_symbol.pop(preset_name)
            fake_for_symbol.pop(preset_name, None)

            close_price = fake.close_price or price
            pnl = self._calc_pnl(record, close_price)

            virtual_margin = record.get('virtual_margin', 0.0)
            self._virtual_committed = max(0.0, self._virtual_committed - virtual_margin)
            self._virtual_balance += pnl
            self._save_virtual_balance()

            record.update({
                'status': 'closed',
                'close_price': close_price,
                'close_time': datetime.now(timezone.utc).isoformat(),
                'pnl_usdt': pnl,
                'result': result,
                'virtual_balance_after_close': self._virtual_balance,
            })
            await self._append_closed(symbol, record)
            closed.append({
                'preset_name': preset_name,
                'pnl_usdt': pnl,
                'result': result,
                'entry_price': record['entry_price'],
                'close_price': close_price,
                'side': record['side'],
            })
            logger.debug(f"[{symbol}] Virtual order closed: {preset_name} {result} pnl={pnl:.2f}")

        return closed

    async def close_all_open(self, symbols: list[str], feed: 'DataFeed') -> None:
        for symbol in symbols:
            open_for_symbol = self._open.get(symbol, {})
            if not open_for_symbol:
                continue
            try:
                ticker = await asyncio.to_thread(
                    feed.client.futures_symbol_ticker, symbol=symbol
                )
                close_price = float(ticker.get('price', 0) or 0)
            except Exception as exc:
                logger.warning(f"[{symbol}] Failed to fetch price for virtual close: {exc}")
                close_price = 0.0

            for preset_name, record in list(open_for_symbol.items()):
                row_close = close_price if close_price > 0 else record['entry_price']
                pnl = self._calc_pnl(record, row_close) if close_price > 0 else 0.0
                virtual_margin = record.get('virtual_margin', 0.0)
                self._virtual_committed = max(0.0, self._virtual_committed - virtual_margin)
                self._virtual_balance += pnl
                record.update({
                    'status': 'closed',
                    'close_price': row_close,
                    'close_time': datetime.now(timezone.utc).isoformat(),
                    'pnl_usdt': pnl,
                    'result': 'closed_early',
                    'virtual_balance_after_close': self._virtual_balance,
                })
                await self._append_closed(symbol, record)

            self._save_virtual_balance()
            open_for_symbol.clear()
            self._fake_orders.get(symbol, {}).clear()
            logger.info(f"[{symbol}] All virtual orders closed (bot stop/mode switch)")

    def _calc_pnl(self, record: dict, close_price: float) -> float:
        entry = record['entry_price']
        qty = record['quantity']
        if record['side'] == 'BUY':
            return (close_price - entry) * qty
        return (entry - close_price) * qty

    def _path(self, symbol: str) -> Path:
        return self._project_root / 'data' / f'virtual_orders_{symbol}_{self._mode}.json'

    def _get_write_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._write_locks:
            self._write_locks[symbol] = asyncio.Lock()
        return self._write_locks[symbol]

    async def _append_closed(self, symbol: str, record: dict) -> None:
        async with self._get_write_lock(symbol):
            path = self._path(symbol)
            path.parent.mkdir(parents=True, exist_ok=True)
            existing: list = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except Exception:
                    existing = []
            open_records = [r for r in existing if r.get('status') == 'open']
            closed_records = [r for r in existing if r.get('status') != 'open']
            closed_records.append(record)
            if len(closed_records) > _MAX_CLOSED:
                closed_records = closed_records[-_MAX_CLOSED:]
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(open_records + closed_records))
            tmp.replace(path)
