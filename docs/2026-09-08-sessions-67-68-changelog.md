# Sessions 67–68 changelog — 2026-09-07 and 2026-09-08

44 commits over two days. Three themes: the **mirror instance**, **API bans**, and
**making silent failures visible** — the last of which found two bugs that had been
quietly costing real orders.

Times are commit author times. Every claim here was measured on the server, not inferred;
the numbers are kept so future sessions can check whether they still hold.

**Related:** [`docs/specs/2026-09-07-rank1-statistics-gap.md`](specs/2026-09-07-rank1-statistics-gap.md),
[`docs/specs/2026-09-07-stop-evicting-on-rank-change.md`](specs/2026-09-07-stop-evicting-on-rank-change.md),
[`docs/specs/2026-09-08-watchdog-guard-and-full-ws-klines.md`](specs/2026-09-08-watchdog-guard-and-full-ws-klines.md),
`FEATURES.md`, `TODO.md` (session 67 SL bands, session 68 deployable budget).

---

## 2026-09-07 (session 67)

### 1. `12:37–14:06` — The second bot instance ("the mirror")
`d6d7bb3` `661a5e7` `1daf213` `7033125` `602c2a7` `ea624f5`

A second container running the **opposite** mode to the primary (live-market data while
the primary is on testnet), virtual-only, no API keys, no Telegram. A Primary/Shadow
toggle on the Trades page switches which instance's data you view, instantly — changing
the bot's *mode* still needs a restart, changing the *view* does not.

- **Bug caused:** both instances wrote the same backtest-result and data files, so the
  mirror would have overwritten the primary's numbers. Fixed in `850bda5` + `7033125` by
  suffixing files per **instance**. The suffix is keyed on `virtual_only`, *not* on mode —
  keying on mode would have handed the mirror the primary's `risk_state.json` (balance 0)
  the moment the primary went live.
- **Bug caused:** the mirror crash-looped, demanding API credentials it must not have.
  Fixed in `78a50ee` — a virtual-only instance is exempt from the credentials check and
  actively blanks any credentials it is given.

### 2. `14:00` — Symbol registry writes made safe
`bf34e38`

Atomic write, never destroys a file it cannot parse, survives a read-only mount.

- **Bug caused:** the "atomic" write used `rename`, which fails with **EBUSY** on a single
  file bind-mounted into Docker — the path is itself a mount point. Registry saves would
  have silently stopped. Fixed in `7f0ebe0` (`config/safe_write.py`): try tmp+rename, fall
  back to an in-place write on `OSError`. Verified on the server.

### 3. `14:26` — Telegram ban alerts readable
`b510916`

No raw markup tags, and ban expiry shown as UTC date/time instead of a bare epoch number.

### 4. `14:58` — Ban recovery redesigned
`20875e4`

After a ban lifts, traffic settles for a few seconds before all symbols resume, instead of
15 symbols firing at once.

- **Bug caused:** my first design required three consecutive successful probes. Replaying
  the real ban log showed it added **~1,800s of unnecessary suppression**. Replaced with
  time-based settling; then measured on the live server that 3s was too short (a burst
  arrived at 4.99s) and set `_SETTLE_S = 8.0`.

### 5. `15:54` — Instance-aware dots on the Trades symbol picker
`926806f`

Bright yellow = live real order, faint amber = virtual only, red = disabled, dimmed =
has data in the primary but not in the selected instance.

### 6. `17:07–17:54` — Optional stop-loss clamping
`50a4ae4` `5966bb8`

An over-wide stop can be clamped to `max_sl_pct` instead of the signal being discarded.
**Shipped OFF** — opt-in per symbol via `sl_clamp_enabled`.

### 7. `18:16–19:06` — Startup cost cut
`c950a1e` `6ba2c2b` `4af40a5`

**35 startup API calls → 5.** `leverageBracket` is one batch call (the `symbol` parameter
is optional, weight 1 either way), `exchangeInfo` is cached and populates every symbol's
lot data from one response, and the startup backtest became opt-in. Dockerfile layers
reordered so a Python-only change no longer rebuilds the dashboard (~40s per deploy).

### 8. `19:34` — WebSocket candles feed the kline cache
`832e2f9`

`append_kline()` existed but was never called, so the on-disk cache only advanced on a
REST refresh. Now the cache tracks the stream, so `KLINE_REFRESH_EVERY` went **4 → 96**
candles (a 24× reduction in kline REST traffic). Also: fewer wallet reads, and the
yellow real-order dot.

### 9. `20:34` — Stop-loss width analysed over 163k orders, deliberately NOT applied
`791ba53`

Win rate is **flat at 41–46% across every SL band** — width changes how much you lose when
wrong, not how often you win. The optimum differs per symbol (2–3% up to 7%+), so no single
global floor fits. INJUSDT wants 4–5%; TIAUSDT wants 3–4% and its ≥4% population is
**−13,076 USDT**. Logged in `TODO.md` session 67.

> **Correction recorded:** earlier the same day I recommended a global `min_sl_pct` of 4.5
> from a 3-symbol *backtest* sweep. The 163k real-order data contradicted it for four
> symbols. Prefer recorded outcomes over simulation in this codebase.

### 10. `20:59` — Ban expiry survives a restart
`e7199db`

Persisted to disk, and the last unguarded API read paths brought under the guard.

### 11. `21:43` — Locked presets are per trading mode
`084f343`

Test and live keep separate locks (`{test: {...}, live: {...}}`, legacy flat dict read as
test-only).

- **Bug caused:** the Trades page **read** locks for the instance you were viewing but
  **wrote** to whichever mode the bot was running. Clicking the padlock in Shadow view
  silently locked the preset for test, and the icon never updated because the page re-read
  live. Fixed in `9f4499e` — the lock lands on the instance on screen.

### 12. `22:12` — Rank-1 virtual pool: a blocked signal is no longer lost
`89d97cb` (spec) `79c6cb4`

When the top or locked preset's real order is blocked by a filter, it now records a virtual
order. Before this, **TIAUSDT recorded 12 outcomes from 127 signals** — survivorship bias
in the one place that decides where money goes.

- **Bug caused:** rank-1 positions were opened but never closed — three separate loops
  (`check_prices`, `close_all_open`, `_save_all_rank_balances`) still started at rank 2.
  Fixed in `465ef22`.
- **Process note:** I claimed this was implemented before it actually was. The user asked
  "have you implemented the spec?" and the honest answer was no.

### 13. `22:51` — Practice positions survive a rank reshuffle
`f7169fd` (spec) `7e486c2`

A virtual position used to be closed at whatever price was current merely because the
rankings shifted — **60,266 of 163,668 closed virtual orders (36.8%)**, averaging +0.31.
The ranking key is `sum(recent_trades[-10:])`, so those near-zero artificial outcomes
**diluted** real ones and made good and bad presets look alike. Ranks ≥2 now run to a real
exit. Rank 1 still evicts, because it must represent whatever would trade right now.
Added: one open position per preset per symbol, and a `virtual_max_age_candles` valve
(default 96 = 24h) since the longest stuck position had run 11 days.

### 14. `23:13` — The deploy procedure itself was dangerous
`da25a63`

`/bfb-deploy` ran `git reset --hard HEAD && git clean -f dashboard/public/` before pulling.
On the server that would have **deleted 15 untracked `results_*_live.json` files — the
mirror's only copy of its backtest results** — and discarded local edits to
`symbol_registry.json`, which holds live symbol weights. Replaced with: inspect
`git status`, plain `git pull`, restore individual blocking files only if the pull refuses,
and stop and ask if the blocker is `symbol_registry.json`. Also documented the
dashboard-only deploy path and graceful stop for `bot_mirror`.

### 15. `23:21` — Bookkeeping closes explained on the dashboard
`fb7a153`

A preset showing "3 trades" with no wins, losses or trails is not a display bug — those
closes were bookkeeping. The trade-count cell now carries a dotted underline and a tooltip
breaking down the reasons. A dedicated column was rejected: the reasons are diagnostic and
would compete with real outcomes.

---

## 2026-09-08 (session 68)

### 16. `12:10` — Win rate counts decided outcomes
`4b3170d`

The denominator was every close, bookkeeping included, so a preset with 40 reshuffles and
10 real exits read ~4× worse than it performed — and unevenly, since reshuffle counts vary
by rank. Now divides by wins + partials + trails + losses.

### 17. `13:06` — Both API watchdogs respect the rate-limit guard
`9b4b4f5` (spec) `781b454`

`_fetch()` was properly guarded; **both watchdogs bypassed it**, calling the client
directly, and updated their staleness timers only on *success*. So during a ban a stale
symbol retried forever:

| watchdog | interval | worst case, 16 symbols |
|---|---|---|
| candle | 30s | 32 requests/min |
| price | 5s | **192 requests/min** |

Each request while banned adds 120s to the ban — a self-sustaining amplifier, dormant
whenever the WebSocket is healthy, which is why it went unnoticed. Also fixed a latent
bug: the candle watchdog used the **trading** client for klines, so under `live_klines` it
would have pulled testnet candles into a production-fed cache.

### 18. `13:06` — WebSocket candles store all 12 fields
`781b454`

Both handlers built 7 of the 12 fields REST returns, so caches held two shapes (measured:
`SOLUSDT_15m_test` had 4,935 rows of 12 and 65 of 7). The WS payload was verified
field-by-field against the live stream — every field present, types matching. Legacy short
rows are padded to 12 on read with `None` (not `0`, so a later consumer can tell "predates
full capture" from "genuinely zero volume"). The files healed on first write, not over the
predicted 52 days.

### 19. `13:16` — Drag-and-drop can deactivate but never activate
`0b7cacf`

`onDragEnd` assigned `newWeights[sym] = n - i` to **every** row, so with 16 symbols one
drag set the bottom row to weight 1 — **silently switching on all nine zero-weight symbols
for real trading** and discarding the hand-picked weights above.

- **Bug nearly shipped:** my first version also reassigned the weight pool by position when
  deactivating, so switching SOLUSDT off would have moved TIAUSDT from 4 to 6 — changing
  six allocations the user never touched. A test caught it; deactivation now changes
  exactly one symbol.

### 20. `14:00–14:23` — A preset never holds a real and a virtual position at once
`08c7948` `84ce62a` `566460d`

Observed on the server: SOLUSDT held a real `l2_trend_buy` at 102.97 **and** a rank-1
virtual `l2_trend_buy` at 103.63 simultaneously — two correlated samples of one price move
feeding one preset's statistics, on a trade that could never have been taken. The gate was
candle-scoped, so it only suppressed the stand-in on the candle the order was placed.

- **Bug caused:** my second attempt (`84ce62a`) blocked **every** rank for a symbol in a
  real trade. Too broad — it stopped an actively-trading symbol collecting any comparison
  data at all (SOLUSDT had 63 legitimate virtual positions on other presets). Fixed in
  `566460d`: scoped to symbol **and** preset, enforced inside `_try_open` so it also holds
  when the rankings shift and that preset reappears at a different rank mid-trade.

### 21. `16:08` — Every real-order rejection records a reason
`11e9255`

**17 of the 27 exit paths in `_try_place_order` returned silently.** A funded, signalling
symbol could produce nothing all day with no trace of why. Measured: 118 `floor_sl_pct`
events with no follow-up decision, and ETHFIUSDT/REZUSDT logging "Using manually locked
preset" and then vanishing while holding 20.3% and 12.5% of allocated capital.

`decision_log.record()`'s own docstring already listed `skip_already_open` and
`skip_no_signal` as expected values — neither had ever been wired up.

**What it immediately revealed:** ETHFIUSDT (9×) and TIAUSDT (14×) never trade because
their **locked presets generate no signal**. The order path re-runs the engine under the
chosen preset's own settings; if that yields nothing it used to return silently, making
"the lock is the blocker" indistinguishable from "no signal occurred".

- **Bug caused:** the extra rows pushed the decision log's 5,000-row cap harder, and a
  plain tail-trim discards by recency — evicting **15 of 79 real-order records in four
  hours**, and shrinking the log's window from 27 days to 18. Fixed in `d72cf46`: the
  newest 1,000 `placed` rows are exempt from eviction; everything else still competes by
  recency. Row count unchanged, so per-decision I/O is unchanged.
  - My *first* version of that fix was also wrong — it treated the cap as a ceiling and
    shrank an all-`placed` log from 5,000 rows to 1,004. The pre-existing
    `test_caps_at_max_entries` caught it. I had written a new test asserting the wrong
    requirement and then changed working code to satisfy it.

### 22. `16:33` — A failed balance read no longer reports 0.00 and refuses every order
`406853e`

Found only because of #21. `_balance_cache_inner` started at `(0.0, 0.0)` and filled only
on a *successful* fetch; the startup read went into `RiskManager` but never into that
cache. So after any restart, one failed fetch made the order path see `balance=0.00`:

```
13:10:22  RiskManager: real balance seeded — balance=peak=3072.38 USDT
13:15     REZUSDT  skip_balance  'balance=0.00 < margin=1.00'
13:30     REZUSDT  skip_balance  'balance=0.00 < margin=1.00'
```

**Two real orders refused for insufficient funds on an account holding 3,072 USDT** — on a
symbol that had just been funded and had never placed a real order. The cache is now primed
from the startup read, and a failed fetch falls back to the TTL cache and then
`risk_manager.get_balance()`. This had been eating orders on every restart.

### 23. `17:29` — Disabled symbols persist their WebSocket candles
`c66f511`

`on_candle_close`'s disabled-symbol branch **returned before `append_kline`**, so those
caches never advanced from the stream and every restart re-fetched all of them. Measured:
**65 `load_klines` calls across 8 restarts — ~8 each, exactly the 8 disabled symbols.**
Verified after deploy: restarts now fetch **0** klines instead of 8.

### 24. `17:49` — The wallet is read mid-candle, not at the candle close
`78a9fc1` `0271fc9`

Of 15 `-1003` responses that day, **13 were the balance call and every one landed within
one second of a candle boundary** (+0.45s to +0.92s). Zero were klines. That instant is
when every bot on the exchange reads its account, and the banned addresses
(`15.158.242.x`) are **CloudFront edge addresses shared with other tenants** — not our
egress `185.237.14.105`, and not what `fapi`/`testnet` resolve to (`13.35.58.x` /
`65.8.131.x`). We were not causing those bans; we were arriving into them.

The 60s TTL *guaranteed* the boundary read missed the cache and hit the network at exactly
that spike. `_balance_prefetch_loop()` now reads at `candle_boundary + period/2` (:07:30,
:22:30, :37:30, :52:30) and `_BALANCE_TTL` spans a candle. **Same single call per candle,
moved to a quiet moment.** Verified: fired at `15:07:30.248`, and no balance call reached
the network at any boundary afterwards.

- **Bug caused:** I logged the pre-fetch at `debug` while the root logger runs at `INFO`,
  making the whole mitigation invisible — the exact failure mode that let #21 and #22
  survive so long. Fixed in `0271fc9`: success at INFO, a missed pre-fetch at WARNING.

### 25. `17:49–18:18` — Ban probing, kline persistence and log-trim fixes
`c66f511` `78a9fc1` `0271fc9` `d72cf46` — see items 23, 24 and 21 above.

### 26. `18:00` — Probe only near the end of a stated ban
`aab3c11`

Bans are **per-CloudFront-edge**, not per-account: three edges rejected us that day, each
with its own expiry (`.97` said 17:44:57 while `.71` said 17:34:57 at the same moment). A
probe therefore proves only that the edge *it* reached is clear; the next request routes
elsewhere, is rejected, and adds ~120s to that edge's ban. The guard then re-armed —
longer — and the loop repeated every candle. **Measured: +336 minutes of ban time in one
day from 39 probes**, with one episode going from "ends 15:46:54" to "ends 17:48:58".

`_PROBE_AFTER_FRAC = 0.9`: no probe until 90% of the stated ban has elapsed. Safe because
bans do not block trading — three real orders were placed *inside* stated ban windows and
the logs hold no order-placement failure at all.

> **Correction recorded:** I had previously concluded these bans came from congestion at
> the candle boundary (item 24) and moved the balance read for it. That was sampling bias —
> the call happened at the boundary, so every ban appeared there. After the move the bans
> moved with it. The ban follows the call; the probe policy was the real lever.

### 27. `19:10` — The probe gate must survive a restart *(committed, awaiting the next deploy)*
`b33234d`

Item 26 did not hold, and the log said why:

```
18:37:30  ARMED 3501s (ban until 19:35:51)
18:56:29  Reconciliation failed: -1003        <- restart, unguarded read
19:00:00  probing (stated ban has 36 min left) -> SUCCEEDED
19:01:49  CLEARED
```

Two holes, both exposed by the three restarts made right after deploying item 26:

- **`_probe_not_before` lived only in memory.** `load_state()` restored the ban expiry from
  disk but not the gate, and `blocked_for()` *skipped* an absent gate — so every restart
  during a ban put the flap straight back. It is now restored from what is left of the ban,
  and derived rather than skipped if any path sets the block directly.
- **`reconcile_with_exchange()` and `sync_positions_with_exchange()` were unguarded**, and
  reconciliation runs on every startup — that is the 18:56:29 line. Only 2 of 7 raw call
  sites in `order_executor` consulted the guard. Skipping is safe: an orphan blocks that
  symbol's signals but keeps its exchange stop, and the sync re-checks each candle.

Even while leaking, the gate limited the damage: that episode was extended by **0.0
minutes** (2 rejections), against +129 and +122 minutes earlier the same day.

### 28. `21:00` — Manual order close from the Trades page
`a6e7f65` (spec) `bf8d7e4`

`LIVE` → `NOW`, a hover tooltip carrying the whole order plus the live unrealised result,
and a red ✕ that market-closes a real or virtual position after inline confirmation.
Recorded as `manual_close`, excluded from `preset_efficiency`, and logged three ways.
Only the running instance can close its own positions.

> **Incident:** the commit used `git add -A` and swept in files that were never meant to be
> tracked — including `risk_config_template.json`, which contains a **live Telegram bot
> token**, and it was pushed. Untracked and gitignored in `1c60418`; **the token must be
> rotated**, since removing it from HEAD does not unpublish it. Checked and not exposed:
> `.env` (ignored), `.env.example` (placeholders only), `risk_config.json` (untracked), and
> no Binance key or secret in any tracked file.
>
> The same mistake then blocked the deploy — 17 committed bot-output files collided with
> the server's copies — and a mis-scoped recovery attempt reverted `symbol_registry.json`,
> silently removing BTCUSDT and re-disabling AVAXUSDT, ETHFIUSDT and REZUSDT. Caught in the
> startup line ("15 symbols" instead of 16) and repaired from the 08:46 backup with the
> AVAXUSDT re-enable re-applied. `risk_config.json` was never touched.

---

## Verified end state (2026-09-08 ~15:20 UTC)

| check | result |
|---|---|
| tests | 819 passing |
| errors since last deploy | 0 |
| klines, all 16 symbols | uniform 12-field, **0 gaps, 0 duplicates**, monotonic, current |
| klines fetched per restart | **0** (was 8) |
| balance calls at candle boundaries | **0** |
| real + virtual on same symbol+preset | none |
| real-order records protected from eviction | yes |

## What the two days actually established about income

- **Bans cost zero orders.** No `skip_balance` event traces to a ban: the balance getter
  falls back to the TTL cache and then to RiskManager's last-known-good figure
  (`main.py:786-794`), so a failed read never reaches sizing as 0.
  *Superseded 2026-09-09:* `skip_balance` is no longer absent — 42 events appeared, but
  none of them are balance failures. They are allocation zeros; see session 69 below. Our measured API usage is
  ~0 weight/minute against a 2,400 ceiling — read from Binance's own
  `X-MBX-USED-WEIGHT-1M` header, which incremented only for the probe requests.
- **`symbol_weights=0` blocked 4,510 signals** against 79 orders placed — 57× more. That,
  not bans, was the constraint. The weight change on 2026-09-08 produced real orders on
  SOLUSDT, AVAXUSDT and REZUSDT within hours.
- **Realised P&L turned positive in August**: June −685.35, July −242.19, **August +132.36,
  September +10.96**. Last 30 days: 96 orders, 43.8% win rate, +78.28.
- **The virtual simulation is trustworthy** — INJUSDT virtual +10.67/trade vs real
  +10.26/trade (gap 0.41), MEMEUSDT gap 0.27. That matters, because preset selection
  depends on it.
- **Open question:** four symbols hold 62.5% of allocated capital with no real trading
  history, because they had weight 0 until 2026-09-08. Two locked presets
  (ETHFIUSDT, TIAUSDT) generate no signal at all — see #21.


---

## Session 69 (2026-09-09) — the allocation zero, and what it was not

29. **`balance=0.00 < margin=1.00` was never a balance problem.** 42 `skip_balance` events
    on REZUSDT across 6 candles. `_try_place_order`'s fourth parameter is *named* `balance`,
    but every TATS call site passes an allocation (`sym_cap`, `deployable`, `remaining`) —
    only the BGF path at `main.py:1696` passes a real wallet figure. So the `0.00` meant
    "this symbol was allocated nothing", and `margin=1.00` (min_notional 5 / leverage 5)
    was healthy throughout. The misleading label cost two wrong diagnoses in one session.
    Parameter rename queued as a separate commit.

30. **Root cause: two weight sources disagree.** `risk_config.symbol_weights` decides
    candidacy; `symbol_registry.weights` sizes the sole-candidate branch. REZUSDT held
    risk_config 13 against registry 0, ETHFIUSDT 9 against 0 — together **22 of 54
    configured weight, 41% of allocated capital**, sized to zero whenever sole candidate.
    Fixed in `35b4fc8` by falling back to risk_config *only* when the registry has no
    weight. Verified live: REZUSDT 0 → 507.31, ETHFIUSDT 0 → 351.21, and the four working
    symbols unchanged at 421.45.

31. **`aa5f9e7` was too broad and is superseded by `35b4fc8`.** It applied the risk_config
    fraction unconditionally, which would have cut TIAUSDT 421 → 156 (63%) — the most
    productive symbol under the current locked presets. Its commit message also overstated
    the loss as "42 real orders"; the honest figure is ~2 signal episodes.

32. **AVAXUSDT: not a bypass, just a stale weight.** Seven real orders, 0 winners, −53.49
    over 27.2h (−47.11/day). Diagnosed initially as three bypassed guards — wrong.
    `main.py:1667-1668` drops zero-score candidates before the sole-candidate branch, so
    weight 0 is a hard gate. Those orders predate the weight change. Confirmed fixed by
    the user's own weight-0 setting: 7 consecutive candles discarded from 15:00.
    The same reasoning clears BTCUSDT — weight 0 makes it genuinely virtual-only.

33. **Locked presets are working; the lifetime loss is pre-lock churn.** Lifetime is
    −751.71 over 391 orders, but restricted to orders placed *since* each symbol's lock
    took hold: **21 orders, +118.19**, and every locked symbol is positive except AVAXUSDT.
    Excluding it: **14 orders, +171.68**. EIGENUSDT's −289.61 lifetime is almost entirely
    `lh_sell_trail15` (−326.75 over 16, 12% win), a preset it can no longer select.
    *Do not judge a symbol on pre-lock history.*

34. **H17-19 blackout validated, not extended.** H17 0% win over 11 orders, H18 0% over 10,
    H19 10% over 10 — keep it. The wider hourly pattern is a trap: lifetime H22 is −189.41,
    but the currently-locked symbol+preset pairs return **+106.57 at 75% win** there. The
    lifetime hourly signal reflects which presets traded when, not the hours. 38 locked-pair
    orders across 18 hours is not enough to act on.

35. **Idle capital is a signal-frequency problem, not a sizing one.** Balance 3,098.93 with
    **91.6% idle** — but 32% is structural (15% reserve + the tier's undeployed 20%), and
    only one position was open. Six funded symbols at ~350 margin each would use ~2,100 of
    the 2,107 deployable budget. INJUSDT (weight 14, the best symbol at +275.89 over 56
    orders) produced **zero decisions in 48h** — klines verified fresh at 5,000 candles, so
    it is simply not signalling.
