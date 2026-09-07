"""Outcome by natural stop-loss width, from real + virtual order logs.

Run on the server:  python3 scripts/analyze_sl_bands.py
Then:               python3 scripts/analyze_sl_bands_per_symbol.py   (reads /tmp/sl_rows.json)

No API calls, no backtest — it reads the recorded outcomes in data/*_orders_*.json.
163,581 closed orders as of 2026-09-07. See TODO.md session 67 for the findings.

Real outcomes, not simulation. Virtual orders run the same geometry as real ones
(same presets, same filters), so they are the large sample; real orders are the
ground truth on a small one.
"""
import json, glob, os, re

def sl_pct(o):
    e, sl = o.get('entry_price'), o.get('sl')
    if not e or not sl:
        return None
    d = abs(float(e) - float(sl)) / float(e) * 100
    return d * 1.5 if o.get('side') == 'SELL' else d   # same x1.5 the filter uses

rows = []
for f in glob.glob('/opt/bot/data/virtual_orders_rank*_test.json'):
    m = re.match(r'virtual_orders_rank\d+_(.+)_test\.json', os.path.basename(f))
    if not m:
        continue
    sym = m.group(1)
    try:
        d = json.load(open(f))
    except Exception:
        continue
    for o in d:
        if o.get('status') != 'closed':
            continue
        p, pnl = sl_pct(o), o.get('pnl_usdt')
        if p is None or pnl is None:
            continue
        rows.append([sym, p, float(pnl), o.get('result') or '?', 'virtual'])

for f in glob.glob('/opt/bot/data/real_orders_*_test.json'):
    sym = os.path.basename(f).replace('real_orders_', '').replace('_test.json', '')
    try:
        d = json.load(open(f))
    except Exception:
        continue
    for o in d:
        p, pnl = sl_pct(o), o.get('pnl_usdt')
        if p is None or pnl is None:
            continue
        rows.append([sym, p, float(pnl), o.get('result') or '?', 'real'])

json.dump(rows, open('/tmp/sl_rows.json', 'w'))

nv = sum(1 for r in rows if r[4] == 'virtual')
nr = sum(1 for r in rows if r[4] == 'real')
print(f'closed orders analysed: {len(rows):,}   virtual {nv:,}   real {nr:,}')
print(f'symbols: {len(set(r[0] for r in rows))}')
print()

BANDS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 7), (7, 999)]

def label(lo, hi):
    return f'{lo}-{hi}%' if hi < 999 else f'{lo}%+'

def table(subset, title):
    print(title)
    print(f"  {'SL band':<10}{'n':>9}{'win%':>8}{'net USDT':>14}{'avg/trade':>12}")
    for lo, hi in BANDS:
        b = [r for r in subset if lo <= r[1] < hi]
        if not b:
            continue
        w = sum(1 for r in b if r[2] > 0)
        net = sum(r[2] for r in b)
        print(f'  {label(lo,hi):<10}{len(b):>9,}{w/len(b)*100:>7.1f}%'
              f'{net:>+14.1f}{net/len(b):>+12.3f}')
    print()

table(rows, 'ALL SYMBOLS COMBINED')
table([r for r in rows if r[4] == 'real'], 'REAL ORDERS ONLY (ground truth)')
