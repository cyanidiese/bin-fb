"""Gate A: run the REAL backtester with the mr_fade preset on the allow-list
symbols and confirm the toy-sim edge (session 61) survives real sizing/fees/gates.

The toy-sim probe (/tmp/s60/mr_refine.py) showed ~+0.2%/trade, 7/8 symbols
OOS-positive. This harness re-checks that through bot/backtester.py — real
fee model (0.04% taker, both sides), real gate chain, real FakeOrder exits.

NOTE: the allow-list has 6 symbols but /tmp/s60/fullklines/ ships only 5 of
them (no SOLUSDT.json). We count ONLY symbols with data and hold the plan's
2/3-majority bar against that count — never against the full allow-list.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import load_settings          # noqa: E402
from config.presets import ALL_PRESETS             # noqa: E402
from bot.backtester import Backtester              # noqa: E402

ALLOW = ['TIAUSDT', 'EIGENUSDT', 'INJUSDT', 'THETAUSDT', '1000PEPEUSDT', 'SOLUSDT']
KDIR = Path('/tmp/s60/fullklines')


def main():
    preset = dict(ALL_PRESETS['mr_fade'])
    have = 0
    pos = 0
    print(f"{'sym':13} {'n':>4} {'WR':>6} {'net%':>8} {'avg%/t':>8}")
    print('-' * 45)
    for s in ALLOW:
        f = KDIR / f'{s}.json'
        if not f.exists():
            print(f'{s:13}   -- no klines (excluded from Gate A)')
            continue
        have += 1
        kl = json.load(open(f))
        bt = Backtester(load_settings(s), initial_balance=1000.0)
        r = bt.run(kl, {'mr_fade': preset})['mr_fade']
        n = r.total()
        net = r.total_profit_pct()
        wr = r.win_rate()
        avg = net / n if n else 0.0
        pos += 1 if net > 0 else 0
        print(f'{s:13} {n:>4} {wr:>5.0%} {net:>8.2f} {avg:>8.3f}')

    # Plan's bar is a 2/3 majority; apply it to symbols WITH data, not the
    # full allow-list (SOLUSDT has no klines this run).
    need = math.ceil(have * 2 / 3)
    print('-' * 45)
    print(f"\nnet-positive on {pos}/{have} allow-list symbols with data "
          f"(need >= {need} for a 2/3 majority)")
    assert pos >= need, (
        f"Gate A FAILED: MR edge did not survive the real backtester "
        f"({pos}/{have} net-positive, needed {need})"
    )
    print("Gate A PASSED")


if __name__ == '__main__':
    main()
