# Signal & Recommendation Precision Analysis — 2026-08-11 (Session 60)

**Goal:** Analyze how the bot turns klines into trends and recommendations, find what can raise
USDT profit, and evaluate whether **non-price-action** data can improve order-creation precision.
Method: 3 collaborating agents (researcher / architect / trader) proposed ideas, cross-reviewed
and approved/rejected each other's, then a chair converged on the survivors. All load-bearing
numbers were independently re-verified against the raw data before this log was written.

Data window: real orders, klines, decision log, balance history pulled from the server (`/opt/bot`)
covering **2026-07-22 → 2026-08-11**. Local copies staged under `/tmp/s60/`.

---

## 1. How signals are made today (code-grounded)

- **Data ingested:** `bot/data_feed.py` subscribes ONLY to `@kline_{tf}` websocket streams. No
  depth/orderbook, no aggTrade, no markPrice/funding, no open-interest.
- **Signal generation is 100% price-action:** `bot/trend.py` builds multi-level (L1/L2/L3)
  swing-point trends, break-of-structure (BoS), and Fibonacci corrections. `bot/kline_processor.py`
  confirms swings with a 2-candle (`neighbours=2`) lag. `bot/recommendation_engine.py` scores each
  candidate:
  `precision = projection_reliability·0.25 + parent_alignment(0/0.175/0.35) + entry_quality·0.40 + correction_bonus`.
- **Unused data already on disk:** every kline the bot downloads carries volume (idx5), quote
  volume (idx7), trade count (idx8), taker-buy-base (idx9), taker-buy-quote (idx10). **None is read**
  by the signal path (grep confirms only a format comment in `kline_processor.py`). No RSI/MACD/EMA
  in the live path; ATR exists only in `bot/backtester.py` SL sizing and is off by default.
- **Heavy gating, not signal starvation:** `risk_manager.can_open_sync` blocks a symbol every candle
  when its best preset's backtested profit-factor < `min_profit_factor` (live 1.08). Decision log
  since Jul 22: **43 placed, 2943 skip_profit_factor, 1583 floor_sl_pct, 271 skip_max_profit_pct.**

> **Live/backtest parity trap:** any new data gate must be implemented identically in the live path
> (`analyzer`/`recommendation_engine`) AND in `backtester.py` + `virtual_order_simulator.py`, or the
> preset-efficiency selector scores presets on a signal population the live bot never trades. This is
> the exact divergence that produced the `ignore_parent_alignment=True` workaround presets.

---

## 2. Why we are losing (verified numbers, Jul 22 → Aug 11)

Account: **$3,227.59, down from $3,724.46 peak (Jul 22), −13.3% drawdown.** 64 trades, 27% WR, **−$423 net.**

| Cut | Result |
|---|---|
| **BUY side** | **−$436 @ 11% WR** — buying into a falling market |
| SELL side | ≈ flat (+$13) |
| Result type | losses −$764, **trails +$308 (only profit source)**, structural TP hit ~never |
| BUY entered **below EMA200** | 15 of 18 trades, 13% WR, **−$319** *(independently recomputed)* |
| BUY entry candle with **takers net-selling** (taker ratio <50%) | 14 of 18, 7% WR, **−$369** *(independently recomputed)* |
| Net-positive presets | **all SELL-only:** `r5_sl_filter` +$103 (86% WR), `l2_bos_trend` +$44, `trail_15_from_30_full` +$6 |
| Worst preset | `l2_bos_entry` (trades both sides) **−$239** |

**Root cause:** the counter-trend BUY is the entire leak. The regime filter is ON, but it only checks
each signal's *own local* swing structure; the workaround presets set `ignore_parent_alignment=True`,
so macro direction is never enforced. Every net-positive preset this window was SELL-only — but that
is a *falling-market artifact*, so the correct fix is a **regime-adaptive** BUY gate (auto-re-enables
if the market turns up), not a blanket BUY ban that would overfit to this regime.

---

## 3. Panel outcome — approved vs rejected

The three agents cross-reviewed all ideas. **Approved by ≥2 of 3** and ranked by impact ÷ effort:

| # | Change | Mechanism | Effort |
|---|---|---|---|
| 1 | **Blocklist the −EV both-sided presets** (`l2_bos_entry` etc.) | hot-reload, ~$380 avoided, reversible | trivial |
| 2 | **BUY-side parent-opposing hard gate** (use existing `_parent_is_opposing`) | kills −$436 counter-trend longs; leaves profitable shorts; regime-adaptive | low |
| 3 | **Trail-first exit tuning** (de-emphasize unreachable far TPs) | amplifies the only proven profit source | low |
| 4 | **Volume + taker-imbalance entry gate** (data already in klines) | rejects thin-volume breaks / longs into net-selling | low code + data-plumbing fix\* |
| 5 | **EMA50/200 regime gate** | orthogonal catch for counter-trend entries | low |
| 6 | **Fix SL/TP level-sourcing bug** (6–15% SL artifact) | cuts the 1,583 SL-floor events; already spec'd 2026-07-16 | medium |
| 7 | Precision-score SL-width penalty; per-symbol loss kill-switch; ATR SL-floor | secondary robustness | medium |

\* The live websocket candle is truncated to 7 fields in `data_feed.py` (lines ~192-200/262-265/346-349),
dropping taker data. It must be widened to 12 fields before any taker gate works live.

**Explicitly REJECTED by the panel (with reasons):**

- **Funding-rate gate** — testnet funding is synthetic and has no replayable history → poisons the
  preset-efficiency sweep. (only 1/3 kept)
- **Open-interest confirmation** — OI history capped ~30 days vs multi-month klines = parity gap;
  testnet OI unrepresentative. (unanimous reject)
- **Orderbook / depth-imbalance as an engine gate** — no historical L2 book → un-backtestable, would
  corrupt preset selection; testnet books thin. (survives only as a hypothetical live-only executor
  check, out of scope)
- **Time-of-day / trade-count gate** — 24/7 alt perps + 64 trades = overfit.

> **Key constraint:** exotic external data (funding, OI, orderbook) is off the table **while on
> testnet** because it can't be backtested for parity. **Volume and taker-flow are the exception —
> free, already downloaded, and fully backtestable today.**

---

## 4. Recommended sequencing

- **(a) Quick wins — deployable in one session:** #1 blocklist (hot-reload, no deploy), #2 BUY
  parent-opposing gate (small code + deploy, backtest-validated), #3 trail tuning.
- **(b) Medium structural — spec first (big-feature rule):** #4–#7, anchored on a shared
  `MarketContext` object built once and consumed by both the live engine and the backtester loop
  (idea A1) so gates stay parity-honest. Sequence #6 (SL/TP sourcing) before the ATR floor.
- **(c) Research bets — validate before committing:** CVD slope (only after taker plumbing pays off),
  ADX chop filter, 1h multi-timeframe confluence (likely redundant with EMA gate), combined
  EMA+taker as a *tunable swept knob*, never a hard AND (the AND left only 15/64 trades = overfit).

**Single highest-leverage next action:** hot-reload the preset blocklist now (zero code, ~$380
avoided, reversible) and in the same session ship the BUY-side parent-opposing gate — the durable,
regime-adaptive fix for the −$436/11%-WR counter-trend-long leak, using the `_parent_is_opposing`
helper that already exists.

**Honest limits:** entry-gate numbers are joins on ~64 post-Jul-22 testnet trades — directionally
strong (the long-side bleed and trail-only edge also hold across the 260-trade history) but small
per-preset n. Treat every gate as a swept knob, not a hard-coded constant.

---

## 5. Status

Nothing was changed this session — this is analysis only. Awaiting user decision on (1) shipping the
two quick wins and (2) spec'ing the volume/taker-flow capability.

Full per-agent idea catalogue (all 22 unique ideas, including rejected) and the chair's verbatim
synthesis follow below.

---

## Appendix A — Full idea catalogue (all panelists, incl. rejected)

### Proposed by Researcher

**[A1] Add a shared MarketContext param to engine.generate() (enabler for all data gates)**  
- *Category:* other | *Effort:* medium  
- *Rationale:* RecommendationEngine only receives (trend, entry_price); it cannot see volume, taker flow, or ATR. Any data-driven gate would otherwise have to be implemented twice (analyzer live path + backtester loop), which is exactly the live/backtest parity trap that spawned the ignore_parent_alignment mess. One shared context object computed from klines by both callers keeps a single gate implementation.  
- *Mechanism:* Not profit on its own; it is the plumbing that lets every gate below (A2/A3/T1/R1) be written once inside _score_and_filter and be automatically replayed identically in backtester.py:309, keeping preset efficiency scores honest.  
- *Data:* Recent klines only (already in Analyzer._klines and backtester klines list). No new fetch.  
- *Integration:* recommendation_engine.py generate()/collect_all()/_score_and_filter() add optional ctx arg; build a small MarketContext (rolling vol median, ATR, taker ratio if available) in analyzer.py add_candle() and in backtester._run_preset() loop before engine.generate(). Both build it from the same fields → parity by construction.

**[A2] Volume-confirmation gate on entry (zero-plumbing data, blocks weak BoS entries)**  
- *Category:* volume | *Effort:* low  
- *Rationale:* Volume (kline idx5) is ALREADY present in both the live WS candle (data_feed lines 199/263) and REST/backtest klines — full parity with no data plumbing. The bot fires entries with no volume confirmation; BoS on thin volume is the classic false-break that produces the 43 losses at -$764. Currently NOTHING in the live signal path reads volume.  
- *Mechanism:* Require the signal/breakout candle volume > k x rolling median volume (e.g. 20-candle) to admit a continuation entry; otherwise skip. Converts to USDT by filtering out low-conviction BoS entries that immediately reverse into SL, directly attacking the loss cluster.  
- *Data:* volume idx5 — already in klines and live WS candle; needs A1 context to reach the engine.  
- *Integration:* recommendation_engine._score_and_filter (new gate after range-position gate); volume series supplied via A1 MarketContext. Backtester inherits it through engine.generate automatically.

**[A3] Taker-buy pressure gate to kill counter-trend BUYs (targets the -$436 BUY bleed)**  
- *Category:* taker-flow | *Effort:* medium  
- *Rationale:* BUY side is -$436 at 11% WR — buying into a falling market. Taker-buy-base/quote ratio (aggressor imbalance) is the most direct read of whether real flow is buying or selling, and it is the highest-signal field the bot ignores. It exists in REST klines (idx9/10) but is DROPPED by the live WS candle builder, so this needs the WS widening described below.  
- *Mechanism:* Block continuation BUY signals when recent taker-buy ratio (V/v) is below ~0.5 (net selling), and symmetrically block SELLs when net buying. Directly suppresses the counter-trend BUYs that ignore_parent_alignment re-admitted, which is the current bleed.  
- *Data:* taker-buy-base k['V'], base vol k['v'] (ratio). Present in REST klines; ABSENT from live WS candle — must widen the WS candle to 12-field REST layout ([t,o,h,l,c,v,T,q,n,V,Q,"0"]) in stream_klines, stream_combined AND watchdog fallback, so idx6 stays close-time and _merge/has_gap keep working.  
- *Integration:* data_feed.py (3 candle-construction sites widen to 12 fields); append_kline/cache now stores full rows; recommendation_engine._score_and_filter new gate via A1 context. PARITY TRAP: live caches currently hold mixed 7-field (WS) + 12-field (REST) rows — must handle short rows defensively and let backtester read the same widened field, or the taker gate silently no-ops in one path.

**[T1] ATR/volatility regime gate + ATR-scaled trailing (trails are the ONLY profit source)**  
- *Category:* exit-logic | *Effort:* medium  
- *Rationale:* Trailing exits are +$308 recently and +$1,456 historically at 70% WR while structural TP hit only twice ever — the trail IS the strategy's edge. It is currently a fixed pct. ATR is computable from klines (h-l), already used in backtester SL sizing (min_sl_atr_mult) but off by default and NOT in the live path.  
- *Mechanism:* Scale trail_activation_pct/trail_min_distance_pct by current ATR so trails don't get shaken out in high-vol regimes and lock profit sooner in calm regimes; optionally block entries when ATR spike makes the floored SL absurdly wide (the 6-15% SL problem). Squeezes more USDT out of the one exit type that already wins.  
- *Data:* ATR from klines h-l — already present both paths; needs A1 context.  
- *Integration:* FakeOrder trail params are already modeled in backtester (lines 434-435) so backtest parity is intact; live order_manager/order_executor trail logic must read the same ATR-scaled values. recommendation_engine for the entry-side ATR gate.

**[T2] Trade-count / thin-liquidity time gate (attacks the -$604 <1h-hold losers)**  
- *Category:* regime-filter | *Effort:* low  
- *Rationale:* Sub-1h holds lose -$604 historically and kline timestamps + trade-count (idx8) reveal thin, choppy periods where fast reversals happen. Trade-count is dropped by the WS candle like taker fields; timestamp-based session filtering needs no new data at all.  
- *Mechanism:* Skip entries during low-activity windows (by hour-of-day from kline[0], and/or trade-count below rolling median). Removes the churn entries that open and hit SL within a few candles, cutting the short-hold loss bucket.  
- *Data:* kline open time idx0 (free, both paths); trade-count idx8 (needs same WS widening as A3 if used).  
- *Integration:* recommendation_engine._score_and_filter time gate via A1 context (timestamp path is zero-plumbing); trade-count path piggybacks on A3's WS widening.

**[R1] Fix stop/target level-sourcing so SL/TP come from the SAME trend level as the signal**  
- *Category:* correctness | *Effort:* medium  
- *Rationale:* docs/specs/2026-07-16 documents that stops/targets are sometimes sourced from a different trend level, producing 6-15% SLs and 20-35% TPs; the global_min_sl_pct floor (1583 floor_sl_pct skips) and max_rr clipping are band-aids over this. This is a loss-producing structural bug, which CLAUDE.md ranks above adding any new filter.  
- *Mechanism:* Correct SL/TP to the generating level's own swing anchors so RR is real; eliminates the mispriced trades that get floored/clipped into negative-EV entries. Directly reduces losses and stops the workaround presets from needing ignore_parent_alignment=True (the counter-trend re-admission bleed).  
- *Data:* None — pure trend-structure logic already in trend.py/recommendation.getRecommendation.  
- *Integration:* trend.py getRecommendation() stop/target selection; recommendation_engine._score_and_filter floor/clip logic simplifies once sourcing is correct. Fully shared path, no parity risk.

**[R2] Re-enable parent-alignment as a hard gate and delete ignore_parent_alignment workaround presets**  
- *Category:* regime-filter | *Effort:* low  
- *Rationale:* ignore_parent_alignment=True re-admits counter-trend BUYs and is named in the context as 'the current bleed'; _parent_is_opposing already exists as the gate (recommendation_engine lines 211-216) but is bypassed. This is a config/preset change, not new code.  
- *Mechanism:* With R1 fixing the SL/TP sourcing that made those presets necessary, drop the workaround so counter-trend continuations are hard-blocked again — removes the 11% WR BUY entries mechanically.  
- *Data:* None — existing gate + preset config.  
- *Integration:* config/presets.py (remove/flip flag on offending presets); no code change. Backtest and live both read the flag identically → parity safe. Sequence AFTER R1 so signal droughts don't return.

**[F1] Funding-rate crowding gate (new REST poll, replayable via historical funding)**  
- *Category:* funding | *Effort:* high  
- *Rationale:* Persistent positive funding = crowded longs prone to long-squeezes; entering counter-funding-crowd is a known edge. Not in klines at all — needs a new fetch. Worth flagging as medium-high because it must be replayable in backtest to stay honest.  
- *Mechanism:* Block/penalize BUYs when funding is extreme-positive (crowded longs) and SELLs when extreme-negative; nudges entries toward the side with squeeze fuel.  
- *Data:* fapi premiumIndex/fundingRate — NEW REST poll (every candle or 8h). Backtest needs historical funding series (fapi/fundingRate) aligned to each candle timestamp and cached alongside klines, or the backtester scores this preset blind.  
- *Integration:* data_feed.py new poll method + cache file per symbol; recommendation_engine gate via A1 context. PARITY TRAP: this is the first feature requiring parallel plumbing in 3 places (live poll, historical backfill for backtest, engine gate) — do NOT ship it as an engine gate without the historical series or preset selection lies.

**[F2] Open-interest trend confirmation**  
- *Category:* open-interest | *Effort:* high  
- *Rationale:* OI rising with price = new money confirming the move; OI rising while price falls = shorts building (confirm SELL). Complements taker flow. Not in klines.  
- *Mechanism:* Admit continuation entries only when OI change agrees with price direction; filters exhausted moves.  
- *Data:* fapi openInterest (real-time) + openInterestHist (backtest). NEW fetch. Historical OI is limited to ~30 days on Binance, which caps backtest window and creates a parity gap vs the multi-month kline history.  
- *Integration:* data_feed.py new poll; engine gate via A1. Same 3-place plumbing as F1 with the added 30-day-history constraint — flag as high cost, lower priority than taker flow which is derivable from klines.

**[X1] Orderbook depth imbalance — LIVE-ONLY execution filter, never a preset-selected gate**  
- *Category:* orderbook | *Effort:* high  
- *Rationale:* @depth imbalance can time entries, but it is fundamentally NOT replayable: no historical L2 snapshots. If placed inside the engine it would make backtester efficiency scores meaningless because the backtest can't reproduce it — poisoning preset selection.  
- *Mechanism:* As a final live-only entry-timing check (skip fill if book is stacked against us), it can shave slippage/adverse fills; but it must live OUTSIDE the shared engine gate.  
- *Data:* @depth WS stream + snapshot REST sync (new WS subscription, snapshot/diff management). No historical equivalent.  
- *Integration:* data_feed.py new depth stream; a live-only check in the order-placement path (order_executor/main), explicitly NOT in recommendation_engine. Architecturally flagged: keeping it out of the engine is what preserves backtest honesty.

**[P1] Volume z-score as a precision-score input (soft, not a hard gate) for tie-breaking**  
- *Category:* indicator | *Effort:* low  
- *Rationale:* _precision currently blends reliability/alignment/entry_quality/correction. Volume conviction is a cheap additional axis with full parity (idx5). Adding it as a weighted term lets the selector prefer high-conviction candidates without hard-dropping trades, reducing drought risk vs a hard gate.  
- *Mechanism:* Add small weight for above-median entry-candle volume in _precision; raises average trade quality and shifts _select toward conviction, lifting WR without cutting trade count as aggressively as A2.  
- *Data:* volume idx5 via A1 context — zero new plumbing.  
- *Integration:* recommendation_engine._precision() new term (rebalance existing weights to keep sum≈1). Shared path, parity automatic. Good low-risk complement or A/B alternative to the A2 hard gate.


### Proposed by Architect

**[R1] EMA50/EMA200 regime gate as a hard directional filter**  
- *Category:* regime-filter | *Effort:* low  
- *Rationale:* The bot's only regime check is getTrendRegime (swing-structure based) in recommendation_engine._get_regime, which failed to stop the bleed: 15 of 18 post-Jul22 BUYs were placed with price BELOW EMA200 (13% WR, -$319) and 14 with EMA50<EMA200 (7% WR, -$342). This is the literal 'buying into a falling market' failure. A slope/MA regime built from klines[4] closes is orthogonal to swing structure and catches counter-trend entries the structural regime misses (especially when ignore_parent_alignment=True re-admits them).  
- *Mechanism:* Compute EMA50 & EMA200 over cached closes; block BUY unless EMA50>EMA200 (and/or close>EMA200), block SELL unless EMA50<EMA200. In my join: allowing only golden-cross BUYs cut BUY loss from -$436 to -$94; allowing only downtrend SELLs gave +$49 vs -$36 for uptrend SELLs. Removes counter-trend USDT bleed at the entry gate.  
- *Data:* Already-cached klines close price (idx4). No new fetch. Engine must be given kline access (currently gets only Trend+entry).  
- *Integration:* bot/recommendation_engine.py _score_and_filter (new gate alongside the trend_regime_filter block ~line 200); thread closes/EMAs from bot/analyzer.py which holds self._klines. Add preset flags ema_regime_filter/ema_fast/ema_slow in config/presets.py + config/settings.py.

**[T2] Taker buy/sell imbalance gate on the entry candle**  
- *Category:* taker-flow | *Effort:* low  
- *Rationale:* Klines already carry taker-buy-base (idx9) and volume (idx5) but nothing reads them (grep confirms zero usage outside ATR). This is real order-flow, free, already on disk. In the data, BUYs whose entry candle had taker-buy-ratio<50% (sellers hitting the bid) were 7% WR / -$369 across 14 trades, vs 25% WR / -$68 for the 4 with buyers active. Selling into a market where takers are still net-buying is the mirror risk on SELLs.  
- *Mechanism:* tbr = taker_buy_base(idx9)/volume(idx5) on the just-closed entry candle (optionally 3-candle avg). Block BUY when tbr<0.5, block SELL when tbr>0.5. Directly rejects entries where aggressive flow opposes the trade direction — the single cleanest per-trade predictor of the BUY losses.  
- *Data:* klines idx5 & idx9, already cached (12-field). Caveat: bot/data_feed.py truncates the WS candle to 7 fields (lines 192-200, 262-265, 346-349) so the LIVE path drops taker data — must read from the 12-field REST cache OR preserve idx7-10 in the WS/watchdog candle builder.  
- *Integration:* Data plumbing fix in bot/data_feed.py candle builders (preserve idx7-10); gate in bot/recommendation_engine.py _score_and_filter fed by analyzer klines. New preset flag taker_imbalance_filter / taker_min_ratio.

**[R3] Combined trend+flow entry gate (EMA regime AND taker imbalance)**  
- *Category:* regime-filter | *Effort:* low  
- *Rationale:* R1 and T2 are independent axes (MA regime = slower context, taker ratio = instantaneous aggression) and stack. Backtested join over all 64 post-Jul22 trades: requiring BUY -> (EMA50>EMA200 AND tbr>=0.5), SELL -> (EMA50<EMA200 AND tbr<0.5) kept 15 trades at 47% WR / +$26 and blocked 49 trades that collectively lost -$449. That converts the book from -$423 to breakeven-positive using only data already downloaded.  
- *Mechanism:* Two-condition AND gate at signal admission. It is aggressive (cuts volume ~75%) so ship it as a tunable preset knob and let the efficiency-score sweep (bot/backtester.py) choose per-symbol strictness rather than hard-coding. Mechanism: only take signals where both context and flow confirm direction.  
- *Data:* Same as R1+T2 (klines idx4, idx5, idx9). No new source.  
- *Integration:* bot/recommendation_engine.py _score_and_filter; expose as global_flow_regime_filter in risk_config.json so it hot-reloads (load_risk_config already read at line 94) without a redeploy, and as preset flags for the sweep.

**[A4] ATR-scaled stops and targets to fix the 6-15% SL / 20-35% TP mismatch**  
- *Category:* exit-logic | *Effort:* medium  
- *Rationale:* Spec docs/specs/2026-07-16-trend-structure-fixes.md documents SLs/TPs sourced from a different trend level producing 6-15% SLs and 20-35% TPs; decision_log shows 1583 floor_sl_pct and 271 skip_max_profit_pct events — the geometry is routinely broken, and the global_min_sl_pct/global_max_rr clipping in _score_and_filter is a blunt patch. ATR (avg true range) already exists in bot/backtester.py:369 and virtual_order_simulator.py:329 but min_sl_atr_mult defaults OFF. An ATR floor/cap makes stop distance reflect actual volatility instead of an unrelated structural level.  
- *Mechanism:* Enable min_sl_atr_mult (e.g. SL >= 1.5xATR, <= 3xATR) and cap TP at k*ATR so RR is computed on a realistic stop. Prevents both the tiny-SL-gets-floored-then-RR-collapses path and the unreachable 30% TP. Given trails are the only profit source (+$308) and structural TP hit only twice ever historically, right-sizing exits protects trail-captured gains.  
- *Data:* klines high/low/close ranges (idx2/3/4), already cached. ATR helper already implemented in backtester/simulator.  
- *Integration:* config/presets.py (turn on min_sl_atr_mult/atr_lookback for live presets), bot/backtester.py:369 and bot/virtual_order_simulator.py:329 (already wired for sim), plus main.py SL flooring path so live matches sim.

**[M5] Multi-timeframe (1h) confluence gate on 15m signals**  
- *Category:* multi-timeframe | *Effort:* medium  
- *Rationale:* All signals are single-timeframe 15m. BoS hard-wipes cause multi-day droughts and whipsaw counter-trend entries; a higher-timeframe anchor filters 15m signals that fight the dominant 1h trend. The same EMA200-below failure (15/18 losing BUYs) would be caught earlier by a 1h regime that is far slower to flip than 15m structure.  
- *Mechanism:* Fetch/aggregate 1h klines (or resample the 15m cache 4:1), compute 1h EMA regime or 1h swing direction; admit a 15m BUY only if 1h is not descending, SELL only if not ascending. Adds a slow confluence filter that suppresses signals during 1h counter-trend pushes — reduces the low-WR counter-trend cohort.  
- *Data:* 1h klines: cheapest is resampling existing 15m cache (no new fetch) or one extra futures_klines call per symbol per candle.  
- *Integration:* bot/data_feed.py (add 1h fetch or a resample helper), bot/analyzer.py (hold htf regime), bot/recommendation_engine.py _score_and_filter gate. New preset flag mtf_filter / htf_interval.

**[C6] CVD (cumulative volume delta) slope confluence**  
- *Category:* taker-flow | *Effort:* medium  
- *Rationale:* A single-candle taker ratio (T2) is noisy; the running sum of (taker_buy - taker_sell) per candle = CVD, whose slope reveals sustained accumulation/distribution. Divergence between price and CVD is a classic reversal tell and is computable entirely from idx9/idx5 already on disk. It upgrades T2 from a one-candle snapshot to a trend-of-flow signal.  
- *Mechanism:* Maintain rolling CVD = sum(2*taker_buy_base - volume). Require CVD slope over last N candles to agree with signal side (rising CVD for BUY). Blocks BUYs where price is bouncing but net aggressive flow is still distributing — the exact profile of the -$436 BUY cohort buying into selling.  
- *Data:* klines idx5 & idx9 (needs the same data_feed truncation fix as T2). No new source.  
- *Integration:* New small module bot/flow_metrics.py computing CVD from analyzer klines; consumed as a gate/precision term in bot/recommendation_engine.py. Reuses the taker-field plumbing from T2.

**[F7] Funding rate + open-interest filter**  
- *Category:* funding | *Effort:* medium  
- *Rationale:* Not in klines and completely unused (grep: zero funding/openInterest refs). On USD-M perps, extreme funding + rising OI marks crowded, late-cycle positioning — longing into strongly positive funding or shorting into strongly negative funding is paying to enter a crowded trade. Given the book is 5 alt perps prone to squeezes, a funding/OI guard blocks the worst late-trend entries that price-action alone can't see.  
- *Mechanism:* Pull premiumIndex (lastFundingRate) and openInterest via REST; block BUY when funding above a high threshold with OI rising (crowded longs), block SELL in the mirror case. Also usable as a mean-reversion confirm. Prevents entering exhausted trends that the pure-structure engine keeps signaling.  
- *Data:* NEW REST calls: futures_mark_price/premiumIndex (funding) and futures_open_interest — low frequency (once per candle per symbol), well within rate limits.  
- *Integration:* bot/data_feed.py new fetch_funding/fetch_oi helpers (client already available); cache on analyzer; gate in recommendation_engine._score_and_filter. New preset/risk_config flags funding_filter, funding_max, oi_filter.

**[O8] Order-book (depth) imbalance snapshot at entry**  
- *Category:* orderbook | *Effort:* medium  
- *Rationale:* The bot subscribes to zero depth streams. Resting bid/ask imbalance at the moment of entry is a direct read of near-term support/resistance pressure and complements taker (aggressive) flow. A thin bid stack under a BUY entry is exactly why counter-trend BUYs got run over for -$436.  
- *Mechanism:* On signal, snapshot top-N depth (REST futures_order_book or a @depth WS); compute bid/ask volume imbalance within X% of price. Block BUY when asks heavily outweigh bids near entry (wall above / thin support), mirror for SELL. Rejects entries with the book stacked against the trade.  
- *Data:* NEW: REST futures_order_book (limit=20/50) on-demand at signal time, or a @depth20 WS stream. On-demand REST is cheapest and only fires when a candidate exists.  
- *Integration:* bot/data_feed.py new fetch_depth helper; called from the signal path (bot/analyzer.py add_candle when a best rec exists) before order placement in main.py. Gate/veto rather than a scored term to keep it simple.

**[D9] RSI/MACD divergence + ADX chop filter for reversal vs continuation signals**  
- *Category:* indicator | *Effort:* medium  
- *Rationale:* No momentum/oscillator exists in the live path. The engine distinguishes continuation vs reversal types but scores them only on structure; a momentum confirm reduces false reversals and, via ADX, avoids trading structure in chop (BoS whipsaw droughts noted in the spec). Historically <1h holds were -$604 and L2 signals underperformed L3 — many are low-momentum chop entries.  
- *Mechanism:* For reversal-type recs require RSI/MACD divergence agreeing with the turn; for continuation-type recs require ADX above a floor (trend strength present). Block/penalize signals that fire in low-ADX chop. Cuts the fast-stop-out cohort by refusing entries when momentum/trend-strength doesn't back the structural read.  
- *Data:* klines close/high/low (idx2/3/4), already cached. Pure computation, no new source.  
- *Integration:* New bot/indicators.py (RSI, MACD, ADX) fed by analyzer klines; consumed in recommendation_engine._precision (add momentum term) and _score_and_filter (ADX floor gate). Preset flags adx_min, divergence_filter.

**[V10] Relative-volume confirmation on the entry/breakout candle**  
- *Category:* volume | *Effort:* low  
- *Rationale:* Volume (idx5) is downloaded and ignored. Break-of-structure and boundary entries are only trustworthy on participation; a BoS on below-average volume is the classic fakeout that the spec blames for hard-wipe droughts and bad re-entries. Low-volume entries are cheap to filter out.  
- *Mechanism:* rel_vol = entry-candle volume / SMA(volume, N). Require rel_vol >= threshold (e.g. 1.0-1.3) for continuation/BoS entries; optionally boost precision on high rel_vol. Suppresses low-conviction signals, tightening precision without new data.  
- *Data:* klines idx5, already cached (needs the same WS-truncation fix as T2/C6 for the live path).  
- *Integration:* bot/recommendation_engine.py _score_and_filter (volume gate) and/or _precision (volume term), fed by analyzer klines. Reuses T2's data-plumbing fix. Preset flag rel_vol_min.


### Proposed by Trader

**[R1] Hard-block counter-trend BUYs: neuter ignore_parent_alignment for the BUY side**  
- *Category:* regime-filter | *Effort:* low  
- *Rationale:* Verified in /tmp/s60/real_orders: since Jul22 BUY is 18 trades, 11% WR, net -$436 while SELL is 46 trades, 33% WR, net +$13. The bleed is entirely long-side. 8 presets set ignore_parent_alignment=True (config/presets.py L741/757/785/846/862/887), which bypasses the opposing-parent gate at recommendation_engine.py:210-213 and re-admits BUYs while L2/L3 is descending. The helper _parent_is_opposing() (line 307) already computes exactly the condition needed.  
- *Mechanism:* In the gate at recommendation_engine.py:210-213, when rec.getSide()=='BUY' AND _parent_is_opposing() is True, reject regardless of ignore_parent_alignment (keep the override for SELL only). Removes the ~$436 counter-trend-long leak without touching the profitable short flow.  
- *Data:* Already present: parent trend direction is computed live in trend.py; no new data source. Validate on real_orders BUY subset.  
- *Integration:* bot/recommendation_engine.py gate at lines 210-213 (+ _parent_is_opposing at 307); optionally flip ignore_parent_alignment on the 8 presets in config/presets.py

**[P1] Blocklist proven -EV presets, promote the winners (hot-reload, no deploy)**  
- *Category:* preset-tuning | *Effort:* low  
- *Rationale:* Per-preset net since Jul22: l2_bos_entry n=11 WR9% -$239 (biggest single leak), l2_regime_aware -$67, r5_sl_adjust -$74, hl_buy_prox15_trail15 -$83. Against these, r5_sl_filter n=7 WR86% +$103 and l2_bos_trend n=9 WR44% +$45 are the only consistent earners. l2_bos_entry is documented (presets.py:780) as 'ignore parent trend, trades any L2 BoS regardless of macro' — it is the counter-trend engine.  
- *Mechanism:* Add l2_bos_entry, l2_regime_aware, r5_sl_adjust to the risk_config.json preset blocklist via /bfb-config (hot-reload). Directly stops preset_efficiency selection from choosing negative-expectancy presets. ~$380 of avoided losses in this window alone.  
- *Data:* Already present: preset_name field in real_orders + preset_efficiency_test.json.  
- *Integration:* risk_config.json preset blocklist (bfb-config skill); consumed by preset selection in bot/backtester.py / virtual_tracker

**[R2] Per-symbol consecutive-loss kill switch**  
- *Category:* regime-filter | *Effort:* medium  
- *Rationale:* TIAUSDT since Jul22 is 8 trades, 0% WR, net -$158 — every single trade lost, yet the symbol stayed active. INJUSDT 19 trades 21% WR -$157 is a second persistent bleeder. There is no per-symbol circuit breaker; streak_state_test.json tracks streaks but does not auto-disable a symbol.  
- *Mechanism:* Auto-disable a symbol for the rest of the session (or N candles) after K consecutive losses OR net drawdown > $X on that symbol. Would have cut TIAUSDT after ~3-4 losses, saving ~$100 of the -$158.  
- *Data:* Already present: per-symbol result stream in real_orders and streak_state_test.json.  
- *Integration:* risk layer reading streak_state_test.json + order-placement gate in bot/analyzer.py / risk_state.json

**[E1] Trail-first exits: earlier trail activation, stop relying on structural TP**  
- *Category:* exit-logic | *Effort:* medium  
- *Rationale:* Structural take-profit is essentially a myth in live data: since Jul22 only 2 trades closed as 'win' (structural TP) for +$33, versus 19 'trail' closes for +$308 at 79% WR. Historically 2 structural TP hits EVER vs +$1,456 from trailing. Wide structural TPs (20-35% per the trend-structure spec) are never reached; positions round-trip back to SL instead.  
- *Mechanism:* Lower trail activation thresholds and de-emphasize structural TP on the winning presets so gains convert to locked trailing profit sooner. Captures more of the only proven profit source and reduces give-back from winners that reverse before an unreachable TP.  
- *Data:* Already present: result field ('trail' vs 'win') and hold times in real_orders; exit sim in virtual_order_simulator.py.  
- *Integration:* config/presets.py trail_activation / trail_distance / max_profit_pct on r5_sl_filter, l2_bos_trend, trail_15_* families; exit path in bot/virtual_order_simulator.py

**[T1] Taker-flow veto filter using kline taker-buy volume (idx9/10)**  
- *Category:* taker-flow | *Effort:* medium  
- *Rationale:* Verified taker-buy-base/quote are POPULATED on testnet (~99% of candles: INJ 4969/5000, DOGE 4999/5000, EIGEN 4954, MEME 4883 non-zero) — the earlier '0' reading was only the in-progress candle. Currently NONE of it is used. Counter-trend BUYs (the -$436 leak) are precisely entries made while sellers dominate the tape.  
- *Mechanism:* Compute rolling taker-buy ratio = sum(idx9)/sum(idx5) over last N candles. Veto BUY when ratio < 0.5 (sell-dominant distribution) and veto SELL when ratio > 0.5. Independent, order-flow-based confirmation that blocks buying into selling pressure — directly targets the same cohort as R1 but from a different signal, catching cases where trend structure lags.  
- *Data:* Already in klines (idx5 volume, idx9 taker-buy-base); no new REST/WS. Backtest threshold on real_orders BUY losers first.  
- *Integration:* new helper reading kline idx9/idx5 (bot/kline_processor.py or a filter module) feeding a veto into bot/recommendation_engine.py / bot/analyzer.py

**[E2] Immediate-reversal guard: require confirmation before entry to cut <1h stopouts**  
- *Category:* entry-timing | *Effort:* medium  
- *Rationale:* 0-1h holds are a distinct loss cohort: n=30, WR 13%, net -$128 (nearly a third of the total -$423). These are entries that reverse almost immediately — a hallmark of entering mid-candle at a poor location or chasing a break that fails. kline_processor.py already confirms swings with neighbours=2 lag, but entries fire without a same-direction close confirmation.  
- *Mechanism:* Require the signal candle (or next candle) to CLOSE in the trade direction / beyond the entry zone before placing, and/or reject entries whose price has already run > X% past the ideal entry. Filters the fast-reversal population that produces -$128 while retaining the slower trades that trail into profit.  
- *Data:* Already present: hold_h derivable from open/close_time in real_orders; candle closes in klines.  
- *Integration:* entry gating in bot/recommendation_engine.py + confirmation in bot/kline_processor.py

**[RF1] Precision score is non-predictive — replace top weighting with SL-width / direction penalty**  
- *Category:* indicator | *Effort:* medium  
- *Rationale:* The precision score does not separate winners from losers: 0.7-1.0 band = 44 trades WR 27% net -$218; 0.5-0.6 band = 17 trades WR 24% net -$159. Same win rate across the whole score range, so the current reliability*0.25 + alignment + entry_quality*0.40 blend (recommendation_engine.py:264-269) is not earning its ranking authority. Meanwhile SL-width shows a real gradient: 2-4% SL bucket = -$151 vs 0-2% = -$260 over 54 trades, and parent alignment (direction) is the one component with proven edge (R1).  
- *Mechanism:* Rather than a fragile ML re-fit on only ~64 recent trades, apply a monotonic penalty to wide-SL / opposing-parent candidates in _precision(), so tie-breaks in _selectBest (line 244-251) stop promoting high-nominal-score counter-trend losers. Improves which candidate actually gets placed.  
- *Data:* Already present: precision_score, sl, entry_price, side in real_orders. Low data volume — favor a rules-based penalty over ML.  
- *Integration:* bot/recommendation_engine.py _precision (264-269) and _selectBest (244-251)


---

## Appendix B — Panel chair synthesis (verbatim)

# FINAL CONSOLIDATED RECOMMENDATION — Panel Chair

## 1. Rejected (failed the ≥2/3 approve/refine bar, or unanimous reject)
- **Funding gate (F7/F1):** testnet funding is synthetic; no replayable historical series → poisons preset-efficiency sweep. Only 1/3 kept.
- **Open-interest (F2):** OI history capped ~30 days vs multi-month klines = hard parity gap; testnet OI unrepresentative. Unanimous reject.
- **Orderbook depth as engine gate (O8/X1):** no historical L2 → un-backtestable, corrupts preset selection; testnet books thin. Reject engine-gate framing (survives only as a hypothetical live-only executor check, out of scope).
- **Time-of-day / trade-count gate (Arch-T2):** 24/7 alt perps + 64 trades = overfit; causal fix is E2 instead. Only 1/3 kept.

## 2. Survivors ranked by (USDT impact ÷ effort)

Every data gate depends on **A1 (shared MarketContext)** — the enabler that makes gates parity-honest (this is the exact plumbing whose absence spawned the `ignore_parent_alignment` divergence). Build it once.

| # | Idea | Profit mechanism (real failure) | Integration point | Effort | Validation metric |
|---|------|-------------------------------|-------------------|--------|-------------------|
| 1 | **Blocklist -EV presets (P1)** | Stops selector re-picking l2_bos_entry (n11/9%/-$239), l2_regime_aware -$67, r5_sl_adjust -$74. ~$380 avoided | `risk_config.json` blocklist, hot-reload. No code, no parity risk | trivial | Sweep no longer selects blocked presets; net over next window ≥ 0 |
| 2 | **BUY-side parent-opposing hard gate (Trader-R1)** | Kills -$436/11%WR counter-trend longs; SELL (+$13) untouched → no drought | `recommendation_engine.py` gate ~210-216 using existing `_parent_is_opposing` (307); reject BUY when parent opposes regardless of `ignore_parent_alignment` | low, shared path | Backtest: BUY net -$436→~-$94, SELL unchanged, trade count SELL flat |
| 3 | **Trail-first exit tuning (E1)** | Structural TP hit 2× ever (+$33) vs 19 trail closes +$308/79%WR. Winners round-trip to SL before unreachable 20-35% TP | `config/presets.py` trail_activation/distance + de-emphasize max_profit_pct on r5_sl_filter, l2_bos_trend, trail_15_*; sim already models it | low, parity-safe | Per-preset sweep: trail-captured $ up, no clipping of strong-trend winners |
| 4 | **EMA50/200 regime gate (R1)** | 15/18 losing BUYs were below EMA200; swing-based `_get_regime` missed them. Orthogonal axis | `_score_and_filter` via A1 context; closes idx4 (preserved live). Preset flags | low (+A1) | Backtest join: BUY loss -$436→-$94; confirm SELL side not degraded |
| 5 | **Volume-confirmation gate (V10/A2)** | Low-vol BoS = false breaks → the 43-loss cluster | `_score_and_filter` via A1; idx5 already in WS+REST (no widening) | low (+A1) | rel_vol floor: loss-cluster trades filtered, WR up, count not gutted |
| 6 | **Taker-imbalance veto (T2/A3/T1)** | -$436 BUYs = buying while takers hit the bid; idx9 ~99% populated | `_score_and_filter` via A1 **+ widen 3 data_feed candle builders to 12-field** (192-200/262-265/346-349) + defensive short-row handling | medium | Rolling 3-candle ratio veto: BUY-loser subset blocked; verify live path non-null |
| 7 | **SL/TP level-sourcing fix (Arch-R1)** | Documented structural bug: SL/TP from wrong trend level → 6-15% SL / 20-35% TP; 1583 floor_sl_pct skips. CLAUDE.md ranks bug-fix above filters | `trend.py getRecommendation()` stop/target selection; simplifies floor/clip in engine. Fully shared, no parity risk | medium | floor_sl_pct/skip_max_profit_pct event counts drop sharply; RR realistic |
| 8 | **Precision SL-width penalty (RF1)** | Precision score non-predictive (0.7-1.0 band WR27%/-$218 ≈ 0.5-0.6 band). SL-width shows real gradient | `_precision` (264-269) + `_selectBest` (244-251): monotonic penalty on wide-SL candidates. Fold Arch-P1 volume term here | medium | Selected-candidate WR/net beats current selection on replay |
| 9 | **Per-symbol kill switch (R2)** | TIA 0%WR/-$158 stayed active; no circuit breaker | Risk layer reads `streak_state_test.json` + placement gate in `analyzer.py`/`risk_state.json`; needs re-enable path | medium, live-only | Simulate: TIA disabled after K losses saves ~$100 |
| 10 | **ATR SL-floor (A4, no TP cap)** | Complements #7 as defensive cap; already wired in backtester (369) | Enable `min_sl_atr_mult` on live presets + main.py flooring to match sim | low | After #7, absurd-SL trades bounded; drop TP cap (fights trail thesis) |

## 3. Grouping

**(a) Quick wins — deployable this session:** #1 blocklist (pure hot-reload, no deploy), #2 BUY parent gate (tiny code), #3 trail tuning (config + sweep). These three attack the confirmed -$436 long bleed and amplify the only proven profit source.

**(b) Medium structural — write a spec first (CLAUDE.md big-feature rule):** #4-#10, anchored on **A1 MarketContext** (build once, both analyzer + backtester loop). Sequence: #7 SL/TP sourcing → then #10 ATR floor and the blanket parent re-enable (Arch-R2); #6 requires the 3-site WS widening + mixed 7/12-field defensive handling or it silently no-ops live.

**(c) Research bets — validate before committing:** ATR-scaled trailing (A/B vs #3; risks widening trails in exactly the high-vol give-back regimes), immediate-reversal run-past-entry veto (E2 refined — the "wait for close" half adds 15m lag that can worsen winners; ship only the "price ran >X% past entry" half), CVD slope (only after #6 pays), ADX chop-floor (drop RSI/MACD divergence), MTF 1h (defer — redundant with #4), combined EMA+taker as a **tunable OR knob for the sweep, never a hard AND** (the AND leaves 15/64 trades = overfit + reintroduces droughts).

**Honest limits:** all entry-gate numbers are joins on ~64 post-Jul22 trades on testnet — directionally strong (the long-side bleed and trail-only edge are robust across the 260-trade history too) but per-preset n is small. Treat every gate as a swept knob, not a hard-coded constant.

## 4. Single highest-leverage next action
**Hot-reload the P1 preset blocklist now** (zero code, zero deploy, ~$380 of avoided loss, fully reversible) **and in the same session ship the Trader-R1 BUY-side parent-opposing hard gate** — the durable fix for the identical -$436/11%WR counter-trend-long leak, using the `_parent_is_opposing` helper that already exists. The blocklist buys time; R1 removes the leak mechanically without touching the roughly-flat, profitable short flow.
