# Enable duplicate-signal skip by default

**Date:** 2026-09-13
**Status:** approved, not yet implemented
**Author:** session 70

---

## Why

After a stop-out, the strategy frequently re-signals the *same* setup — same side, near
the same entry/SL/TP — within a candle or three. `duplicate_skip_candles` exists to
refuse that re-entry (main.py:1135, and the simulator's own copy at
virtual_order_simulator.py:521). It is the single best-performing filter we have.

It is switched on for only **27 of 87 presets**. The other 60 inherit the base default of
`0`, which means disabled.

Measured 2026-09-13 on the 6 funded symbols, 60 days. A "duplicate" is a re-entry within
3 candles of a loss close, same side, entry/SL/TP all within 3%:

| population | n | win rate | avg return on margin |
|---|---:|---:|---:|
| duplicates on presets **without** the filter | **8,885** | **28%** | **−0.258%** |
| all virtual orders (baseline) | 57,874 | 42% | −0.052% |
| duplicates that leaked through on presets **with** the filter | 423 | 48% | +0.372% |

Duplicates on unfiltered presets are **5x worse than baseline**. The third row is the
control: on presets that do filter, the re-entries that fall outside their particular
window/tolerance are *positive* — which is what a working filter looks like, catching the
bad ones and letting the rest through.

Real money agrees, on a small but unambiguous sample:

| | n | winners | net |
|---|---:|---:|---:|
| real orders that were duplicates on an unfiltered preset (60d) | **9** | **0** | **−57.38** |

**Zero winners out of nine.** There is no winning trade to lose by enabling this — which
is the specific risk that has burned us before (the EIGENUSDT `max_sl_pct` removal was
reverted within a day because the order it would have blocked made +48.93). Seven of the
nine were EIGENUSDT.

## What it does

Changes the base default so the 60 presets that never opted in get the same protection as
the 27 that did.

## Chosen approach

**Change one default:** `config/settings.py:255`, `DUPLICATE_SKIP_CANDLES` default
`'0'` → `'3'`.

Presets are applied as overrides onto base `Settings` via `dataclasses.replace`
(config/settings.py:334, main.py:922, virtual_order_simulator.py:426). A preset that does
not name `duplicate_skip_candles` inherits the base value; the 27 that do name it
(values 1, 2, 3, 4, 10) are untouched and keep their own tuning.

`duplicate_skip_pct` already defaults to `2.0` (config/settings.py:256), which is the most
common value among the presets that set it explicitly. No change needed.

Why 3 candles: it is the modal value among the 27 opted-in presets (used by 14 of them),
and the measurement above used a 3-candle window, so the evidence and the setting match.

## Rejected alternatives

* **Edit all 60 preset definitions.** What was originally asked for. Rejected: 60 nearly
  identical edits, easy to get wrong, and it hard-codes the value into each preset so
  reverting means another 60 edits. The base default achieves the same thing in one line
  and stays overridable per preset and per deployment.
* **A global risk_config key with a runtime override.** Hot-reloadable, which is
  attractive, but `duplicate_skip_candles` is read off `preset_settings` on the signal
  path in two places; adding a third source of truth for the same value invites the exact
  class of bug that `sl_clamp_enabled` caused (a key set at the wrong level, read by
  nothing, silently doing the opposite of what was intended). The env default is already
  the single source and needs no new plumbing.
* **Leave it off and blocklist the worst presets instead.** Blunter: it would discard
  those presets' good trades along with the duplicates, where this discards only the
  duplicates.

## Touch points

| File | Change |
|---|---|
| `config/settings.py:255` | `DUPLICATE_SKIP_CANDLES` default `'0'` → `'3'` |
| `tests/test_duplicate_skip_default.py` | **new** |
| `FEATURES.md` | update the existing "Duplicate-Signal Skip" entry |

No change to `main.py` or `virtual_order_simulator.py` — both already read the setting and
already implement the skip. This turns on existing, exercised code.

## Risk flags

* **Virtual trade counts drop ~15%.** 8,885 of 57,874 virtual orders over 60 days are
  duplicates on unfiltered presets. They are the worst 15%, but preset sample sizes shrink
  and some presets will take longer to clear `min_trades_for_ranking` (8).
* **The efficiency scoreboard shifts.** Presets that were being dragged down by duplicate
  re-entries will rank higher. That is the intended correction, but pre- and post-change
  preset rankings are not directly comparable.
* **Real order frequency falls slightly** — 9 fewer orders over 60 days on this evidence,
  all of which lost money.
* Reversible instantly: set `DUPLICATE_SKIP_CANDLES=0` in the environment, no redeploy of
  code needed.

## Test plan

* a preset that does not specify the field inherits 3, not 0
* the 27 presets that specify their own value keep it exactly (assert each)
* `duplicate_skip_pct` still defaults to 2.0
* the env var still overrides, and `0` restores the previous behaviour
* count presets with the filter effectively active: 87, not 27
