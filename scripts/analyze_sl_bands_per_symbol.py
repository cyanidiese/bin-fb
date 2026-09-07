"""Per-symbol optimum stop-loss band, from 163k closed real+virtual orders."""
import json

rows = json.load(open('/tmp/sl_rows.json'))
BANDS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 7), (7, 999)]
LBL = [f'{lo}-{hi}' if hi < 999 else f'{lo}+' for lo, hi in BANDS]
syms = sorted({r[0] for r in rows})

print('AVG USDT PER TRADE by SL band, per symbol   (n in brackets)')
print('  ' + 'symbol'.ljust(14) + ''.join(l.rjust(13) for l in LBL) + '   best')
print('  ' + '-' * 106)
best = {}
for s in syms:
    sr = [r for r in rows if r[0] == s]
    line = '  ' + s.ljust(14)
    scores = {}
    for (lo, hi), l in zip(BANDS, LBL):
        b = [r for r in sr if lo <= r[1] < hi]
        if len(b) < 30:                      # too thin to trust
            line += '—'.rjust(13)
            continue
        avg = sum(r[2] for r in b) / len(b)
        scores[l] = (avg, len(b))
        line += f'{avg:+.2f}({len(b)//1000}k)'.rjust(13) if len(b) >= 1000 \
            else f'{avg:+.2f}({len(b)})'.rjust(13)
    if scores:
        b = max(scores, key=lambda k: scores[k][0])
        best[s] = (b, scores[b][0], scores[b][1])
        line += f'   {b}%'
    print(line)

print()
print('PER-SYMBOL OPTIMUM (bands with >=30 trades)')
print(f"  {'symbol':<14}{'best band':>12}{'avg/trade':>12}{'n':>9}{'current floor':>15}")
cur = {'THETAUSDT': 1.5, '1000SHIBUSDT': 0.8}
for s in syms:
    if s not in best:
        continue
    b, avg, n = best[s]
    print(f'  {s:<14}{b+"%":>12}{avg:>+12.3f}{n:>9,}{cur.get(s, 0.7):>15}')

print()
print('HOW MUCH IS BELOW THE PROFITABLE BAND TODAY?')
print(f"  {'symbol':<14}{'trades <4%':>12}{'share':>8}{'their net':>13}{'trades >=4%':>13}{'their net':>13}")
for s in syms:
    sr = [r for r in rows if r[0] == s]
    lo_ = [r for r in sr if r[1] < 4]
    hi_ = [r for r in sr if r[1] >= 4]
    if not sr:
        continue
    print(f'  {s:<14}{len(lo_):>12,}{len(lo_)/len(sr)*100:>7.0f}%'
          f'{sum(r[2] for r in lo_):>+13.0f}{len(hi_):>13,}{sum(r[2] for r in hi_):>+13.0f}')
