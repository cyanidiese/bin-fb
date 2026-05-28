#!/usr/bin/env python3
"""
Sweep range_position_max across all presets and active symbols to find the
optimal value. Compares baseline (1.0 = disabled) vs tested values.

Usage:
    python sweep_range_position.py

Output:
    sweep_range_position_results.csv  — full per-preset per-symbol table
    Printed summary table in terminal
"""
import csv
import json
import logging
import os
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)

from dotenv import load_dotenv
load_dotenv()

from bot.backtester import Backtester
from config.presets import ALL_PRESETS
from config.settings import Settings

SWEEP_VALUES = [1.0, 0.8, 0.65, 0.5, 0.3, 0.1]
OUTPUT_CSV = Path('sweep_range_position_results.csv')

REGISTRY_PATH = Path('symbol_registry.json')


def get_active_symbols() -> list[str]:
    if REGISTRY_PATH.exists():
        data = json.loads(REGISTRY_PATH.read_text())
        disabled = set(data.get('disabled', {}).keys())
        return [s for s in data.get('symbols', []) if s not in disabled]
    raise RuntimeError('symbol_registry.json not found')


def find_klines(symbol: str, timeframe: str = '15m') -> list | None:
    for name in (f'{symbol}_{timeframe}_test.json', f'{symbol}_{timeframe}.json'):
        p = Path('data') / name
        if p.exists():
            with open(p) as f:
                return json.load(f)
    return None


def make_base_settings(symbol: str) -> Settings:
    return Settings(
        trading_mode='test',
        api_key='', api_secret='',
        symbol=symbol,
        timeframe='15m',
        kline_limit=1000,
        kline_cache_limit=5000,
        timezone='UTC',
        swing_neighbours=2,
        min_swing_points=3,
        proximity_zone_pct=10.0,
        min_profit_pct=0.5,
        min_profit_loss_ratio=1.5,
        precision_similarity_threshold=0.10,
        projection_lookback=4,
        tp_multiplier=1.0,
        max_profit_pct=0.0,
        min_sl_pct=0.0,
        max_sl_pct=0.0,
        sl_adjust_to_rr=False,
        partial_take_pct=0.0,
        trailing_stop_pct=0.0,
        correction_weight=0.0,
        loss_streak_max=0,
        loss_streak_cooldown_candles=5,
        global_pause_trigger_candles=0,
        global_pause_candles=10,
        duplicate_skip_candles=0,
        duplicate_skip_pct=2.0,
        max_losing_pct=0.0,
        max_losing_amount_usdt=0.0,
        max_losing_candles=0,
        lower_high_sell=False,
        higher_low_buy=False,
        min_sl_atr_mult=0.0,
        atr_lookback=20,
        trail_activation_pct=0.0,
        trail_min_distance_pct=0.0,
        min_precision_score=0.0,
        zone_sl_max=0,
        zone_sl_cooldown_candles=16,
        range_position_max=1.0,
    )


def main():
    symbols = get_active_symbols()
    print(f'Active symbols: {len(symbols)}')
    print(f'Presets: {len(ALL_PRESETS)}')
    print(f'Values to sweep: {SWEEP_VALUES}')
    print(f'Total runs: {len(symbols)} × {len(SWEEP_VALUES)} = {len(symbols) * len(SWEEP_VALUES)} batches\n')

    # rows: {symbol, preset, value, trades, wins, losses, win_rate, profit_pct}
    rows = []

    for symbol in symbols:
        klines = find_klines(symbol)
        if klines is None:
            print(f'  [{symbol}] No klines — skipping')
            continue

        print(f'[{symbol}] {len(klines)} candles', end='', flush=True)
        base = make_base_settings(symbol)

        for value in SWEEP_VALUES:
            # Inject range_position_max into every preset override
            presets_with_value = {
                name: {**overrides, 'range_position_max': value}
                for name, overrides in ALL_PRESETS.items()
            }
            backtester = Backtester(base)
            results = backtester.run(klines, presets_with_value)

            for preset_name, result in results.items():
                rows.append({
                    'symbol': symbol,
                    'preset': preset_name,
                    'value': value,
                    'trades': result.total(),
                    'wins': result.wins(),
                    'losses': result.losses(),
                    'win_rate': round(result.win_rate(), 4),
                    'profit_pct': round(result.total_profit_pct(), 4),
                })

            print('.', end='', flush=True)
        print()

    # Write full CSV
    if rows:
        with open(OUTPUT_CSV, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f'\nFull results saved to {OUTPUT_CSV}')

    # ── Summary table ─────────────────────────────────────────────────────────
    print('\n' + '=' * 72)
    print('SUMMARY — Average across all presets and symbols')
    print('=' * 72)
    print(f'{"value":>8}  {"avg_profit%":>11}  {"avg_win%":>9}  {"avg_trades":>10}  {"vs_baseline":>12}')
    print('-' * 72)

    # Baseline: value=1.0
    baseline_by_key: dict[tuple, float] = {}
    for r in rows:
        if r['value'] == 1.0:
            baseline_by_key[(r['symbol'], r['preset'])] = r['profit_pct']

    for value in SWEEP_VALUES:
        value_rows = [r for r in rows if r['value'] == value]
        if not value_rows:
            continue
        avg_profit = sum(r['profit_pct'] for r in value_rows) / len(value_rows)
        avg_win = sum(r['win_rate'] for r in value_rows) / len(value_rows) * 100
        avg_trades = sum(r['trades'] for r in value_rows) / len(value_rows)

        # Delta vs baseline (only for non-baseline values)
        if value == 1.0:
            delta_str = '(baseline)'
        else:
            deltas = []
            for r in value_rows:
                baseline = baseline_by_key.get((r['symbol'], r['preset']))
                if baseline is not None:
                    deltas.append(r['profit_pct'] - baseline)
            avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
            sign = '+' if avg_delta >= 0 else ''
            delta_str = f'{sign}{avg_delta:.4f}%'

        print(f'{value:>8.2f}  {avg_profit:>+11.4f}%  {avg_win:>8.2f}%  {avg_trades:>10.1f}  {delta_str:>12}')

    # ── Per-symbol summary ─────────────────────────────────────────────────────
    print('\n' + '=' * 72)
    print('PER-SYMBOL best value (by avg profit across all presets)')
    print('=' * 72)
    print(f'{"symbol":<20}  {"best_value":>10}  {"best_profit%":>12}  {"baseline_profit%":>16}')
    print('-' * 72)

    for symbol in symbols:
        sym_rows = [r for r in rows if r['symbol'] == symbol]
        if not sym_rows:
            continue

        by_value: dict[float, list[float]] = {}
        for r in sym_rows:
            by_value.setdefault(r['value'], []).append(r['profit_pct'])

        best_val = max(by_value, key=lambda v: sum(by_value[v]) / len(by_value[v]))
        best_avg = sum(by_value[best_val]) / len(by_value[best_val])
        baseline_avg = sum(by_value.get(1.0, [0])) / max(len(by_value.get(1.0, [1])), 1)

        print(f'{symbol:<20}  {best_val:>10.2f}  {best_avg:>+12.4f}%  {baseline_avg:>+16.4f}%')

    # ── Top preset × value combinations ───────────────────────────────────────
    print('\n' + '=' * 72)
    print('TOP 20 preset × value combinations (by avg profit across all symbols)')
    print('=' * 72)
    print(f'{"preset":<35}  {"value":>6}  {"avg_profit%":>11}  {"avg_win%":>9}  {"avg_trades":>10}')
    print('-' * 72)

    combo_stats: dict[tuple, dict] = {}
    for r in rows:
        key = (r['preset'], r['value'])
        if key not in combo_stats:
            combo_stats[key] = {'profits': [], 'wins': [], 'trades': []}
        combo_stats[key]['profits'].append(r['profit_pct'])
        combo_stats[key]['wins'].append(r['win_rate'])
        combo_stats[key]['trades'].append(r['trades'])

    ranked = sorted(
        combo_stats.items(),
        key=lambda x: sum(x[1]['profits']) / len(x[1]['profits']),
        reverse=True,
    )[:20]

    for (preset, value), stats in ranked:
        avg_p = sum(stats['profits']) / len(stats['profits'])
        avg_w = sum(stats['wins']) / len(stats['wins']) * 100
        avg_t = sum(stats['trades']) / len(stats['trades'])
        print(f'{preset:<35}  {value:>6.2f}  {avg_p:>+11.4f}%  {avg_w:>8.2f}%  {avg_t:>10.1f}')


if __name__ == '__main__':
    main()
