# Session 60 State / Handoff — 2026-08-13

Checkpoint of everything that must survive context compaction. Covers the deep
signal/exit analysis, what's live, what's staged-but-undeployed, and what's next.

---

## 1. LIVE NOW on the server (hot-reload, reversible, already applied)

Server runs branch `feature/backtest-live-parity` @ `c77dc4a`. `/opt/bot/risk_config.json`:
- **Active weights:** TIAUSDT 10, EIGENUSDT 8, INJUSDT 7, MEMEUSDT 2. (DOGE→0, everything else 0.)
- **preset_blocklist** includes `l2_bos_entry` (−$100.91 all-time / 17% WR; by-design counter-trend).
- Earlier hot-reloads still in place: `max_sl_pct=8.0` per-symbol on the 5 majors.

Applied this session, in order: l2_bos_entry blocklisted → DOGE 1→0 → TIA 15→10.
Balance at last check ~$3,228 (peak $3,724 on Jul 22; ~−13% drawdown).

## 2. STAGED — committed to `main`, NOT deployed

The server branch does NOT have these yet — they live on `main`. To deploy, cherry-pick/merge
onto `feature/backtest-live-parity`, push, then rebuild (graceful stop → wait for "Bot stopped." →
`docker compose up -d --build`). **Nothing below is live until deployed.**

| commit | what | status |
|---|---|---|
| `2d00a62` | Telegram close message: **Fee line** (`Fee: X.XXXX USDT`) + PnL labeled "(net of fee)"; fee also persisted on real_order records | ready |
| `94ab82b` | docs: backtest-live parity resolved | doc |
| `14b014b` | **Widen trail on 5 l2 trend presets** (activation 2.0→2.5, trailing 0.15→0.30, tmin 1.0 kept) — the payoff lever | ready, validated |
| `a7d8372` | Parent-alignment hard-gate mechanism (Settings `enforce_parent_alignment_hard` + global `global_enforce_parent_alignment`) — **DORMANT**, enabled nowhere, backtest showed it net-negative | keep off |
| `d4c1442`, `9d96b8a` | specs (confirmation gates; trend-structure fixes) | doc |

**Deploy note:** the min() trail-arming fix (`c77dc4a`) IS already on the server. The trail *widening*
(`14b014b`) is a separate, newer change still on main only.

## 3. Core finding (the load-bearing conclusion)

**This strategy has no positive edge in the current regime.** Baseline: 311 real trades, ~28% WR,
−$785 net. Every rigorously tested lever reduces losses toward breakeven or improves payoff
*structure* — none manufactures profitability. Trailing exits are the ONLY profit source
(+$1,456 hist / 70% WR; structural TP hit ~twice ever). The user's directive: **accept losses,
make wins bigger** (raise payoff ratio, not win rate). Current payoff ~1.4; need >1.9 for positive
expectancy at this WR.

## 4. What was TRIED and REJECTED (do not re-try without new evidence)

- **Entry filters (EMA/BTC-regime/taker/volume):** the strong-looking taker/EMA edges were mostly
  **one-candle lookahead**; no-lookahead they only cut losses toward breakeven, don't create edge.
- **Parent-alignment hard gate (Gate 1):** implemented + backtest-validated (flag on vs off, same
  candles) → **net-negative** (l2_bos_entry −11%→−47%, l2_regime_aware −14%→−47%). Mechanism kept
  dormant. The l2_bos_trend(+$138)-vs-l2_bos_entry(−$101) "natural experiment" was **confounded**.
- **Funding / open-interest / orderbook signals:** un-backtestable on testnet (no replayable history)
  → would corrupt preset-efficiency selection. Off the table until live.
- **BTC market-regime gate, rel-volume gate:** weak on the full 311-trade sample.
- **Re-enabling any disabled symbol:** none has a credible profitability case (all net-negative;
  AVAX's high payoff is a 6%-WR fluke and stale since May 28). Watch them virtually instead.

## 5. Validation methodology (critical — prevents repeating mistakes)

- **Backtest-live parity is RESOLVED, not broken.** Post-fix + fees, the FakeOrder model matches
  live within **−$0.40/trade**. Earlier distrust was (a) the now-fixed dead-trail bug and (b) my
  `/tmp` resim harness omitting fees (the real backtester models them at `_TAKER_FEE_RATE=0.0004`).
- **Trust the backtester for:** exit mechanics, and same-regime A/B (flag on/off on identical candles).
- **Discount the backtester for:** cross-regime absolute preset ranking (e.g. MEMEUSDT l2_bos_entry
  +44% backtest vs live loss) — that's the irreducible regime/selection gap, NOT a bug.
- **Any new gate/knob MUST be backtest-validated (same-regime A/B) before enabling.** No-lookahead:
  build features from the last CLOSED candle, never the entry candle.
- Trail widening (14b014b) was validated fee-inclusive on the l2-family real trades: avg win
  $29→$36, payoff 0.97→1.25, avg loss flat, no symbol degrades.

## 6. Open items / next steps

1. **Deploy decision (user-gated):** the staged main commits (esp. trail widening `14b014b` and
   Telegram fee `2d00a62`) need a deploy to take effect. User said "deploy all together later."
2. **After deploy: run 2–4 weeks, then re-read live payoff/net** under all fixes (dead-trail,
   blocklist, weights, wider trail) to decide preserve-vs-pause-vs-new-edge.
3. **Deploy-free next lever (in progress, interrupted):** extend the validated wider-trail change to
   the OTHER trend/trail preset families — evaluate each preset's own real trades via the
   fee-inclusive resim, require per-symbol robustness, apply only where robustly positive.
4. **Highest-ceiling new-edge idea:** a mean-reversion overlay for the chop regime (the trend engine
   structurally loses in ranges). Real build, needs its own spec.
5. Standing infra TODOs: harden deploy graceful-stop (verify "Bot stopped."); investigate REST
   -1003 rate-limit bans (improving, 160→23/week); the 2026-07-16 structural spec (same-level
   stops, soft BoS pruning) remains unbuilt.

## 7. Data + tooling locations

- Local pulled data: `/tmp/s60/` — `real_orders/*.json` (12 symbols, full history), `fullklines/*.json`
  (8 majors + BTC, ~8100 candles each incl. taker fields), `risk_config.json`, `decision_log_test.json`,
  `balance_history_test.json`.
- Analysis scripts (reusable): `/tmp/s60/validate_nolook.py` (entry-filter counterfactuals),
  `/tmp/s60/payoff.py` (payoff-ratio by trail config), `/tmp/s60/exit_replay.py` (fee-inclusive
  FakeOrder replay of real trades).
- Docs: `docs/2026-08-11-signal-precision-analysis.md` (analysis + 3-agent panel + parity addendum),
  `docs/specs/2026-08-12-confirmation-gates.md` (gates spec incl. Gate-1 rejection record).
- Server: `185.237.14.105`, SSH `~/.ssh/id_ed25519` as root; bot at `/opt/bot`, docker container `bot`.
