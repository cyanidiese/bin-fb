# Stop the watchdogs prolonging bans, and write complete klines from the WebSocket

**Date:** 2026-09-08
**Status:** spec — approved for implementation
**Related:** `bot/data_feed.py`, `bot/rate_limit_guard.py`, TODO.md session 68

---

## What it does

Two contained changes to `bot/data_feed.py`:

1. **Both watchdogs go through the rate-limit guard** and stop retrying on a 5s/30s loop
   while banned.
2. **The WebSocket writes all 12 kline fields** instead of 7, and short rows already on
   disk are normalised when read.

## Why — measured, not assumed

### The kline cadence is already fixed; this is not about volume

`KLINE_REFRESH_EVERY` went 4 → 96 candles and closed WS candles are appended to the cache
(`append_kline`), so measured REST kline traffic on 2026-09-08 was:

| source | calls |
|---|---|
| `load_klines` at startup | 9 (7 of 16 symbols had a current cache and skipped) |
| `refresh_klines` | 1 |
| candle watchdog | 0 |
| price watchdog | 0 |

**~10 calls/day** against a 2,400 weight/minute IP budget, at weight 1 each. There is
nothing left to save by fetching klines less often, and this spec does not try to.

### Klines is the most ban-hit endpoint because it reads most often, not because it bans us

665 `-1003` responses across all logs: 470 klines (71%), 191 balance (29%). Those are
responses received **while already banned**. The addresses in the messages are
`15.158.242.71/76/103` — CloudFront shared-edge IPs, not our egress `185.237.14.105`. We
inherit these bans from other tenants behind the same edge.

### What actually prolongs a ban

`_fetch()` is guarded and refuses to touch the network while banned:

```python
_wait = rl_guard.blocked_for(_key)
if _wait > 0:
    raise RateLimited(_key, _wait)
```

Both watchdogs bypass it, calling the client directly:

- `data_feed.py:429` candle watchdog → `self._client.futures_klines`
- `data_feed.py:408` price watchdog → `self._client.futures_symbol_ticker`

And `_last_candle_ts[symbol]` / `_last_price_ts[symbol]` are updated **only on success**, so
a symbol that fails keeps qualifying every tick:

| watchdog | tick | staleness gate | worst case, 16 symbols |
|---|---|---|---|
| candle | 30s | > 1.5 × timeframe (22.5 min) | 32 calls/min |
| price | 5s | > 15s | **192 calls/min** |

Each request while banned adds exactly 120s to the ban. That is a self-sustaining
amplifier: it converts a short inherited edge-ban into a long one. It is dormant while the
WebSocket is healthy — 0 fires on 2026-09-08 — which is why it has gone unnoticed.

This is the mechanism behind bans lasting far longer than our own traffic can explain.

### The 7-field kline rows

REST returns 12 fields; both WS handlers build 7:

```python
candle = [int(k["t"]), k["o"], k["h"], k["l"], k["c"], k["v"], int(k["T"])]
```

Verified on disk — the caches genuinely hold both shapes:

```
SOLUSDT_test  rows=5000  row-lengths={12: 4935, 7: 65}
INJUSDT_test  rows=5000  row-lengths={12: 4936, 7: 64}
BTCUSDT_live  rows=1501  row-lengths={12: 1500, 7: 1}
```

Harmless today: no consumer reads an index above 6 (the analyser only uses `[4]`; data_feed
uses `[0]` and `[6]`). It is a latent `IndexError` for the first feature that wants trade
count or taker volume, and it silently discards data the exchange already sent us for free.

## Design

### 1. Watchdogs use the guarded paths

The candle watchdog calls `self._fetch(symbol, timeframe, 3)` instead of the raw client.
This inherits the guard, `note_success` (which clears a block early on a working probe) and
`note_exception` (which arms it). `RateLimited` is caught and the symbol skipped quietly.

Switching to `_fetch` also fixes a latent endpoint bug: the watchdog used `self._client`
(the **trading** client) for klines, while `_fetch` uses `self._klines_client`. With
`live_klines` enabled those differ, and the watchdog would have fetched testnet klines into
a cache the rest of the system fills from production.

A new `_fetch_ticker(symbol)` gives the price watchdog the same treatment, keyed on the
trading endpoint (`'testnet' if self._is_testnet else 'production'`) because the ticker is
served by the trading host.

### 2. Failure records a timestamp, so a failing symbol does not spin

On any failure — banned or otherwise — the watchdog sets that symbol's `_last_*_ts` to now.
The next attempt then waits a full staleness window (22.5 min for candles, 15s for prices)
rather than retrying on the next tick. The guard already prevents the network call; this
prevents the busy-loop around it.

### 3. The WebSocket writes all 12 fields

Verified empirically against the live stream (`btcusdt@kline_1m`, testnet) — every field is
present, and the types match REST exactly:

| REST index | REST type | WS key | WS type |
|---|---|---|---|
| 0 open time | int | `t` | int |
| 1–5 o,h,l,c,volume | str | `o,h,l,c,v` | str |
| 6 close time | int | `T` | int |
| 7 quote volume | str | `q` | str |
| 8 trade count | int | `n` | int |
| 9 taker buy base | str | `V` | str |
| 10 taker buy quote | str | `Q` | str |
| 11 ignore | str | `B` | str |

So a WS-built row is byte-compatible with a REST row. Fields are read with `.get()` and a
neutral fallback so a schema change at the exchange degrades to a short row rather than
raising inside the stream loop.

The candle the watchdog passes to `on_candle_close` stops being truncated to 7 — it comes
from REST and already has all 12.

### 4. Two versions of the data — how it is handled

This is the part worth being explicit about. After the change, three kinds of row exist:

| row | where from | length |
|---|---|---|
| historical REST | `load_klines` / `refresh_klines` | 12 |
| **legacy WS** | `append_kline` before this change | **7** |
| new WS | `append_kline` after this change | 12 |

**`_read_cache` normalises every row to length 12 on load.** Consumers therefore never see a
short row, and no consumer needs to know which era a row came from.

Missing trailing fields are padded with **`None`, not `0`**. A future consumer must be able
to distinguish "this candle predates full capture" from "this candle genuinely had zero
taker volume"; padding with zeros would let an average over `[9]` silently return a wrong
number instead of failing loudly. Nothing reads those indices today, so `None` costs
nothing now and preserves the distinction later.

Normalisation is applied on read, so the next `_write_cache` for a symbol persists the
padded rows and the file self-heals. Independently, `kline_cache_limit` (5000) rotates the
~65 legacy rows per symbol out within about 52 days at 15m.

## Touch points

- `bot/data_feed.py` — `_fetch_ticker` (new), candle watchdog via `_fetch`, price watchdog
  via `_fetch_ticker`, failure timestamps, both WS handlers build 12 fields, watchdog candle
  untruncated, `_read_cache` normalises row length
- `tests/test_watchdog_rate_limit_guard.py` (new)
- `tests/test_ws_kline_full_fields.py` (new)

## Risks

- **The watchdog is the WS failure fallback.** If the guard is armed spuriously the
  watchdog goes quiet and a stale symbol stays stale. Mitigated by `note_success` clearing
  the block on any working probe, and by the guard's existing time-based settling.
- **Neutral fallbacks could mask a schema change** at the exchange. Accepted: a short row
  is normalised on read, so the failure mode is missing optional fields, never a crash in
  the stream loop.
- **`_fetch` raises where the old code returned a list.** Both watchdog call sites are
  already inside `try/except`; `RateLimited` gets its own quiet branch so a normal ban does
  not log an error every tick.
- **Shared with the mirror.** Lands on both instances via the same image, so the
  live-vs-test comparison stays valid.

## Success criteria

1. Neither watchdog issues a network call while `rl_guard.blocked_for()` is positive.
2. A watchdog fetch that fails does not retry on the next tick.
3. The candle watchdog fetches klines from the kline endpoint, not the trading endpoint.
4. A closed WS candle produces a 12-element row whose types match a REST row.
5. A cache file containing 7-element rows loads with every row at length 12, trailing
   fields `None`.
6. No consumer of index 0–6 changes behaviour.
