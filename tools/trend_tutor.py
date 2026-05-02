#!/usr/bin/env python3
"""
tools/trend_tutor.py

Interactive CLI for manually reviewing L2+ trend candidate points and
improving the bot/trend.py algorithm.

Self-contained and read-only — does not modify any production files.
Delete this file when research is done.

Usage:
    python tools/trend_tutor.py
    python tools/trend_tutor.py --file data/BTCUSDT_15m_test.json
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.kline_processor import KlineProcessor
from bot.point import Point
from bot.trend import Trend
from bot.utils import Utils


# ─────────────────────────────────────────────────────────────────────────────
# Color helpers  (auto-disabled when stdout is not a TTY)
# ─────────────────────────────────────────────────────────────────────────────

_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

def bold(t):    return _c('1',    t)
def dim(t):     return _c('2',    t)
def red(t):     return _c('91',   t)
def green(t):   return _c('92',   t)
def yellow(t):  return _c('93',   t)
def blue(t):    return _c('94',   t)
def cyan(t):    return _c('96',   t)
def white(t):   return _c('97',   t)
def bred(t):    return _c('1;91', t)   # bold red
def bgreen(t):  return _c('1;92', t)   # bold green
def byellow(t): return _c('1;93', t)   # bold yellow
def bcyan(t):   return _c('1;96', t)   # bold cyan

def color_dir(d: str) -> str:
    return bgreen(d) if d == 'ASC' else (bred(d) if d == 'DESC' else yellow(d))

def color_label(label: str) -> str:
    return {
        'HH': bgreen('HH'),
        'HL': green('HL'),
        'LH': red('LH'),
        'LL': bred('LL'),
    }.get(label, white(label))

def color_prediction(text: str) -> str:
    if 'flips' in text:
        return byellow(text)
    if '⚠' in text:
        return yellow(text)
    if 'HH stored' in text or 'LL stored' in text:
        return green(text)
    if 'not stored' in text or 'ignored' in text or 'no high between' in text or 'no low between' in text:
        return dim(text)
    return white(text)


# ─────────────────────────────────────────────────────────────────────────────
# Decision record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    level: int
    kind: str
    label: str
    price: float
    dt: str
    accepted: bool
    reason: str
    direction: str
    bos: Optional[float]
    prediction: str


# ─────────────────────────────────────────────────────────────────────────────
# Outcome predictor (read-only)
# ─────────────────────────────────────────────────────────────────────────────

def predict_outcome(trend: 'Trend', point: Point, kind: str) -> str:
    bos     = trend.getBreakOfStructure()
    bos_str = f"{bos:.2f}" if bos is not None else 'none'

    if kind == 'HIGH':
        last_high = trend.getLastHigh()
        last_low  = trend.getLastLow()

        if last_high is None:
            return "first high stored — direction not yet defined"

        is_higher       = point.getHighValue() > last_high.getHighValue()
        has_low_between = trend.hasLowBetween(last_high.getTime(), point.getTime())

        if has_low_between:
            if not trend.hasDefinedTrend():
                if is_higher:
                    ll = last_low.getLowValue() if last_low else None
                    return (f"trend → ASC  ·  BoS → {ll:.2f} (last low)"
                            if ll else "trend → ASC  ·  no last low for BoS")
                return f"trend → DESC  ·  BoS → {point.getHighValue():.2f} (this high)"

            if trend.isAscending():
                if is_higher:
                    ll      = last_low.getLowValue() if last_low else None
                    new_bos = f"{ll:.2f}" if ll else "none"
                    return f"HH stored  ·  BoS {bos_str} → {new_bos} (last HL)"
                return f"LH stored  ·  BoS unchanged ({bos_str})  ⚠ weakens ASC structure"

            if trend.isDescending():
                if bos is not None and point.getHighValue() > bos:
                    ll      = last_low.getLowValue() if last_low else None
                    new_bos = f"{ll:.2f}" if ll else "none"
                    return (f"HIGH > BoS {bos_str}  ·  trend flips → ASC  ·  BoS → {new_bos}"
                            f"  ·  sends point to L{trend.getLevel() + 1}")
                return f"LH stored  ·  BoS unchanged ({bos_str})"

        else:
            if is_higher:
                return (f"replaces last high {last_high.getHighValue():.2f} → {point.getHighValue():.2f}"
                        f"  (no low between, higher)")
            if trend.isDescending() and bos is not None and point.getHighValue() > bos:
                ll      = last_low.getLowValue() if last_low else None
                new_bos = f"{ll:.2f}" if ll else "none"
                return (f"HIGH > BoS {bos_str} even without new low  ·  trend flips → ASC"
                        f"  ·  BoS → {new_bos}")
            return f"high not stored (not higher than {last_high.getHighValue():.2f}, no low between)"

    else:  # LOW
        last_low  = trend.getLastLow()
        last_high = trend.getLastHigh()

        if last_low is None:
            return "first low stored — direction not yet defined"

        is_lower         = point.getLowValue() < last_low.getLowValue()
        has_high_between = trend.hasHighBetween(last_low.getTime(), point.getTime())

        if has_high_between:
            if not trend.hasDefinedTrend():
                if is_lower:
                    hh = last_high.getHighValue() if last_high else None
                    return (f"trend → DESC  ·  BoS → {hh:.2f} (last high)"
                            if hh else "trend → DESC  ·  no last high for BoS")
                return f"trend → ASC  ·  BoS → {point.getLowValue():.2f} (this low)"

            if trend.isDescending():
                if is_lower:
                    hh      = last_high.getHighValue() if last_high else None
                    new_bos = f"{hh:.2f}" if hh else "none"
                    return f"LL stored  ·  BoS {bos_str} → {new_bos} (last LH)"
                return f"HL stored  ·  BoS unchanged ({bos_str})  ⚠ weakens DESC structure"

            if trend.isAscending():
                if bos is not None and point.getLowValue() < bos:
                    hh      = last_high.getHighValue() if last_high else None
                    new_bos = f"{hh:.2f}" if hh else "none"
                    return (f"LOW < BoS {bos_str}  ·  trend flips → DESC  ·  BoS → {new_bos}"
                            f"  ·  sends point to L{trend.getLevel() + 1}")
                return f"HL stored  ·  BoS unchanged ({bos_str})"

        else:
            if is_lower:
                return (f"replaces last low {last_low.getLowValue():.2f} → {point.getLowValue():.2f}"
                        f"  (no high between, lower)")
            if trend.isAscending() and bos is not None and point.getLowValue() < bos:
                hh      = last_high.getHighValue() if last_high else None
                new_bos = f"{hh:.2f}" if hh else "none"
                return (f"LOW < BoS {bos_str} even without new high  ·  trend flips → DESC"
                        f"  ·  BoS → {new_bos}")
            return f"low not stored (not lower than {last_low.getLowValue():.2f}, no high between)"

    return "unknown outcome"


# ─────────────────────────────────────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────────────────────────────────────

class Session:
    def __init__(self) -> None:
        self.interactive: bool = False
        self.decisions: List[Decision] = []

    @staticmethod
    def direction(trend: 'Trend') -> str:
        if trend.isAscending():  return 'ASC'
        if trend.isDescending(): return 'DESC'
        return 'UNDEFINED'

    @staticmethod
    def point_label(trend: 'Trend', point: Point, kind: str) -> str:
        if kind == 'HIGH':
            last = trend.getLastHigh()
            if last is None: return 'HH'
            return 'HH' if point.getHighValue() > last.getHighValue() else 'LH'
        else:
            last = trend.getLastLow()
            if last is None: return 'LL'
            return 'LL' if point.getLowValue() < last.getLowValue() else 'HL'

    # ── Display ────────────────────────────────────────────────────────────────

    def display(self, trend: 'Trend', candidate: Point, kind: str) -> str:
        level   = trend.getLevel()
        dir_str = self.direction(trend)
        bos     = trend.getBreakOfStructure()
        bos_t   = trend.getBreakOfStructureTime()

        bos_str = dim('none')
        if bos is not None:
            bos_str = cyan(f"{bos:.2f}")
            if bos_t:
                bos_str += dim(f"  @ {Utils.time_to_str(bos_t)}")

        sep = dim('─' * 64)
        print(f"\n{sep}")
        print(f"  {bold(f'L{level} Trend')}   {color_dir(dir_str)}   "
              f"{dim('BoS:')} {bos_str}")
        print(sep)

        # Last 4 confirmed points
        highs   = trend.getHighPoints()
        lows    = trend.getLowPoints()
        all_pts = sorted(highs + lows, key=lambda p: p.getTime())[-4:]

        if all_pts:
            print(f"  {dim('#   TYPE       PRICE           DATETIME')}")
            print(f"  {dim('─' * 46)}")
            for idx, p in enumerate(all_pts, 1):
                if p.isHigh():
                    prev  = next((q for q in reversed(highs) if q.getTime() < p.getTime()), None)
                    ptype = 'HH' if prev is None or p.getHighValue() > prev.getHighValue() else 'LH'
                    price = p.getHighValue()
                else:
                    prev  = next((q for q in reversed(lows) if q.getTime() < p.getTime()), None)
                    ptype = 'LL' if prev is None or p.getLowValue() < prev.getLowValue() else 'HL'
                    price = p.getLowValue()
                # pad based on visual width (ptype is always 2 chars), not escape-code width
                pad = ' ' * (4 - len(ptype))
                print(f"  {dim(str(idx)):<3}  {color_label(ptype)}{pad}"
                      f"  {white(f'{price:>12.2f}')}  {dim(Utils.time_to_str(p.getTime()))}")
        else:
            print(f"  {dim('(no confirmed points yet)')}")

        # Candidate
        label      = self.point_label(trend, candidate, kind)
        cand_price = candidate.getHighValue() if kind == 'HIGH' else candidate.getLowValue()

        warn = ''
        if trend.isAscending()  and kind == 'HIGH' and label == 'LH':
            warn = f"  {yellow('⚠ lower high in ASC trend')}"
        elif trend.isDescending() and kind == 'LOW'  and label == 'HL':
            warn = f"  {yellow('⚠ higher low in DESC trend')}"

        kind_colored  = cyan(kind)
        label_colored = color_label(label)
        price_colored = bold(white(f"{cand_price:.2f}"))
        dt_colored    = dim(Utils.time_to_str(candidate.getTime()))

        print(f"\n  {bold('►')} Candidate {kind_colored} [{label_colored}]"
              f"  {price_colored}  {dt_colored}{warn}")

        # Outcome prediction
        prediction = predict_outcome(trend, candidate, kind)
        print(f"    {dim('If accepted →')} {color_prediction(prediction)}")

        return prediction

    # ── Ask ────────────────────────────────────────────────────────────────────

    def ask(self, trend: 'Trend', point: Point, kind: str) -> Tuple[bool, str]:
        prediction = self.display(trend, point, kind)
        print()
        print(f"  {bgreen('[Enter]')} Accept   "
              f"{red('[n <reason>]')} Reject")

        while True:
            try:
                raw = input(f"  {bold('>')} ").strip()
            except (EOFError, KeyboardInterrupt):
                raise KeyboardInterrupt

            if raw == '' or raw.lower() in ('y', 'yes'):
                return True, ''

            if raw.lower().startswith('n'):
                reason = raw[1:].strip()
                if not reason:
                    try:
                        reason = input(f"  {dim('Reason (Enter to skip):')} ").strip()
                    except (EOFError, KeyboardInterrupt):
                        raise KeyboardInterrupt
                return False, reason

            print(f"  {dim('→ Press Enter to accept, or type  n <reason>  to reject.')}")

    def record(self, trend: 'Trend', point: Point, kind: str,
               accepted: bool, reason: str, prediction: str) -> None:
        self.decisions.append(Decision(
            level=trend.getLevel(),
            kind=kind,
            label=self.point_label(trend, point, kind),
            price=point.getHighValue() if kind == 'HIGH' else point.getLowValue(),
            dt=Utils.time_to_str(point.getTime()),
            accepted=accepted,
            reason=reason,
            direction=self.direction(trend),
            bos=trend.getBreakOfStructure(),
            prediction=prediction,
        ))

    # ── Summary ────────────────────────────────────────────────────────────────

    def summary(self) -> None:
        print(f"\n{dim('═' * 64)}")
        print(bold(white('  SESSION SUMMARY')))
        print(f"{dim('═' * 64)}\n")

        if not self.decisions:
            print(f"  {dim('No decisions were recorded during this session.')}")
            return

        accepted = [d for d in self.decisions if d.accepted]
        rejected = [d for d in self.decisions if not d.accepted]
        print(f"  Total decisions : {bold(str(len(self.decisions)))}")
        print(f"  Accepted        : {bgreen(str(len(accepted)))}")
        print(f"  Rejected        : {bred(str(len(rejected)))}\n")

        for header, group, hdr_fn in [
            ('ACCEPTED', accepted, bgreen),
            ('REJECTED', rejected, bred),
        ]:
            if not group:
                continue
            print(f"  {hdr_fn(header)}:")
            for d in group:
                reason_str = f"  {dim('—')} {yellow(d.reason)}" if d.reason else ''
                bos_str    = f"{d.bos:.2f}" if d.bos is not None else 'none'
                print(f"    {bold(f'L{d.level}')} {color_label(d.label):<20}"
                      f"  {white(f'{d.price:>12.2f}')}"
                      f"  {dim(d.dt)}"
                      f"  {dim(f'[{d.direction}  BoS={bos_str}]')}"
                      f"{reason_str}")
            print()

        print(f"  {byellow('ALGORITHM SUGGESTIONS:')}")
        rej_patterns: dict = {}
        for d in rejected:
            key = f"L{d.level} {d.label} in {d.direction} trend"
            if key not in rej_patterns:
                rej_patterns[key] = []
            rej_patterns[key].append(d.reason or '(no reason given)')

        if rej_patterns:
            for pattern, reasons in rej_patterns.items():
                print(f"\n    {yellow('→')} {white(str(len(reasons)))}× rejected "
                      f"{bold(pattern)}")
                for r in reasons:
                    print(f"        {dim('•')} {r}")
        else:
            print(f"    {green('No rejections — algorithm matches your judgement.')}")

        acc_with_reason = [d for d in accepted if d.reason]
        if acc_with_reason:
            print(f"\n  {bcyan('ACCEPTED WITH NOTES')} {dim('(patterns to preserve)')}:")
            for d in acc_with_reason:
                print(f"    {bold(f'L{d.level}')} {color_label(d.label)}  "
                      f"{dim('—')} {d.reason}")

        log_path = Path('data/trend_tutor_log.json')
        with open(log_path, 'w') as f:
            json.dump(
                [
                    {
                        'level': d.level, 'kind': d.kind, 'label': d.label,
                        'price': d.price, 'dt': d.dt, 'accepted': d.accepted,
                        'reason': d.reason, 'direction': d.direction,
                        'bos': d.bos, 'prediction': d.prediction,
                    }
                    for d in self.decisions
                ],
                f, indent=2,
            )
        print(f"\n  {dim('Full log saved →')} {cyan(str(log_path))}")


# ─────────────────────────────────────────────────────────────────────────────
# InteractiveTrend
# ─────────────────────────────────────────────────────────────────────────────

class InteractiveTrend(Trend):
    def __init__(self, level: int, smaller: Optional['Trend'] = None,
                 session: Optional[Session] = None) -> None:
        super().__init__(level, smaller)
        self._session = session

    def getBiggerTrend(self) -> 'InteractiveTrend':
        if self._bigger_trend is None:
            self._bigger_trend = InteractiveTrend(self._level + 1, self, self._session)
        return self._bigger_trend

    def setHighPoint(self, point: Point) -> None:
        if self._level >= 2 and self._session and self._session.interactive:
            prediction = predict_outcome(self, point, 'HIGH')
            accepted, reason = self._session.ask(self, point, 'HIGH')
            self._session.record(self, point, 'HIGH', accepted, reason, prediction)
            if not accepted:
                return
        super().setHighPoint(point)

    def setLowPoint(self, point: Point) -> None:
        if self._level >= 2 and self._session and self._session.interactive:
            prediction = predict_outcome(self, point, 'LOW')
            accepted, reason = self._session.ask(self, point, 'LOW')
            self._session.record(self, point, 'LOW', accepted, reason, prediction)
            if not accepted:
                return
        super().setLowPoint(point)


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def parse_datetime(s: str) -> int:
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return int(datetime.strptime(s.strip(), fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise ValueError(f"Cannot parse: {s!r}  (expected YYYY-MM-DD HH:MM)")


def fmt_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


def find_kline_file() -> Path:
    data_dir = Path('data')
    for pat in ('BTCUSDT*15m*.json', '*.json'):
        files = sorted(data_dir.glob(pat))
        if files:
            return files[-1]
    raise FileNotFoundError("No kline JSON files found in data/")


def print_trend_summary(label: str, trend: 'Trend') -> None:
    d   = Session.direction(trend)
    bos = trend.getBreakOfStructure()
    print(f"  {bold(label)}: {color_dir(d)}"
          f"  {dim('BoS=')} {cyan(f'{bos:.2f}') if bos else dim('none')}"
          f"  {dim('highs=')} {white(str(len(trend.getHighPoints())))}"
          f"  {dim('lows=')} {white(str(len(trend.getLowPoints())))}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Trend Tutor — interactive L2+ point reviewer')
    parser.add_argument('--file', help='Path to kline JSON cache file')
    args = parser.parse_args()

    print(cyan("╔══════════════════════════════════╗"))
    print(cyan("║") + bold("       Trend Tutor  v1.0          ") + cyan("║"))
    print(cyan("║") + dim("  Interactive L2+ point reviewer  ") + cyan("║"))
    print(cyan("╚══════════════════════════════════╝") + "\n")

    try:
        kline_file = Path(args.file) if args.file else find_kline_file()
        print(f"  {dim('Kline file :')} {white(str(kline_file))}")
        with open(kline_file) as f:
            klines = json.load(f)
    except Exception as e:
        sys.exit(f"Error loading klines: {e}")

    print(f"  {dim('Candles    :')} {white(str(len(klines)))}")
    print(f"  {dim('Range      :')} {white(fmt_ms(klines[0][0]))} "
          f"{dim('→')} {white(fmt_ms(klines[-1][0]))}\n")

    while True:
        raw = input(f"  {bold('Start interactive review from')} "
                    f"{dim('(YYYY-MM-DD HH:MM)')} : ").strip()
        try:
            start_ts_sec = parse_datetime(raw)
            break
        except ValueError as e:
            print(f"  {red(str(e))}\n")

    silent_klines = [k for k in klines if k[0] <  start_ts_sec * 1000]
    active_klines = [k for k in klines if k[0] >= start_ts_sec * 1000]

    if not active_klines:
        sys.exit(red("No candles at or after that datetime — pick an earlier date."))

    print(f"\n  {dim('Silent replay :')} {white(str(len(silent_klines)))} candles")
    print(f"  {dim('Interactive   :')} {white(str(len(active_klines)))} candles\n")

    session   = Session()
    processor = KlineProcessor(neighbours=2)
    trend     = InteractiveTrend(1, session=session)
    buf: list = []

    print(f"  {dim('Replaying silently…')}", end='', flush=True)
    for k in silent_klines:
        buf.append(k)
        for pt in processor.check_last_confirmed(buf):
            trend.checkPointObject(pt)
    print(f" {green('done.')}\n")

    print(f"  {bold('Initial trend state:')}")
    l2 = trend.getBiggerTrend()
    print_trend_summary('L2', l2)
    if l2.hasBiggerTrend():
        print_trend_summary('L3', l2.getBiggerTrend())

    print(f"\n  {dim('Press')} {bgreen('Enter')} {dim('to accept a candidate.')}")
    print(f"  {dim('Type')}  {red('n <reason>')} {dim('to reject and explain why.')}")
    print(f"  {dim('Ctrl+C to stop early and see the summary.')}\n")

    session.interactive = True
    candle_num = 0
    try:
        for k in active_klines:
            buf.append(k)
            candle_num += 1
            for pt in processor.check_last_confirmed(buf):
                trend.checkPointObject(pt)
    except KeyboardInterrupt:
        print(f"\n\n  {dim(f'Stopped after {candle_num} of {len(active_klines)} interactive candles')}")

    session.summary()


if __name__ == '__main__':
    main()
