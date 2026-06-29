# Learning Mode — Design Spec

**Date:** 2026-06-29
**Status:** Approved — ready for implementation planning

---

## What it does and why it's needed

Learning Mode is an overlay on the Strategy dashboard page that lets the user navigate historical candles one-at-a-time, place manual paper orders or accept/reject bot signals, and record notes at each step. At the end of a session the user dumps the event log to Claude Code for analysis. Claude reads the events, identifies patterns in what the user found intuitive vs. wrong, and writes concrete improvement hypotheses to a persistent markdown file.

The feature exists because the bot's profitability is limited by hypotheses that have never been tested. Paper backtesting tells us which presets scored highest on historical data; it does not tell us whether a human looking at the same chart would have placed the order differently. Learning Mode creates a feedback loop: the user's intuition becomes structured data, Claude converts that data into testable hypotheses, and the best hypotheses become code changes.

This is not a training pipeline. It is a hypothesis generator. The output is always a human-readable list of candidate improvements, never automatic weight updates.

---

## Architecture overview

### State model

Learning Mode adds a boolean `learningMode` flag to the Strategy page's state. When active it also holds:

```ts
interface LearningSession {
  id: string;                   // uuid, set at session start
  symbol: string;
  startCandleIndex: number;
  currentCandleIndex: number;
  events: LearningEvent[];
  startedAt: string;            // ISO timestamp
  // endedAt is added only at serialization time (not in live state)
}

interface LearningOrder {
  id: string;                   // uuid
  placedAtCandleIndex: number;
  side: "BUY" | "SELL";
  entryPrice: number;
  tpPrice: number;
  slPrice: number;
  closedAtCandleIndex?: number;
  closePrice?: number;
}
```

The session lives in React state only (never persisted to the server during the session). On "Stop & Save" the full session JSON is written to the browser's Downloads folder via a standard anchor-click download. On "Discard" the session is dropped with a confirmation prompt.

### Activation flow

1. User clicks "Learning Mode" button (top-right of Strategy page, next to Settings).
2. Modal: choose symbol, enter a starting candle index (default: current scrubber position). "Start" creates a new `LearningSession` with a fresh UUID.
3. Any existing in-progress session shows a resume prompt: "Resume [symbol] from candle [N]?" with a separate "Discard & Start New" option.
4. Learning Mode overlay activates — the scrubber ▶ arrows are disabled, the slider is hidden. Navigation becomes "Next Candle ▶" only (no backwards). The page header shows "LEARNING MODE — [symbol]" in amber.

---

## Controls and chart overlay

### Navigation

- **Next Candle ▶** button advances `currentCandleIndex` by 1, fires the replay API with the new index, and appends a `candle_advanced` event to the session.
- The scrubber's ▶▶ fast-forward button is removed in learning mode. The ◀ back button is disabled. The slider is hidden.
- `TimeScrubber` receives `learningMode?: boolean` prop: when true, renders only the Next Candle button and a candle counter label ("Candle 243 / 1200").

### Recommendation panel

Below the chart, a "Bot Recommendation" card appears. It shows the current replay signal (BEST/CANDIDATE/NONE) with preset name, side (BUY/SELL), entry, TP, SL, and RR. Two buttons: **Accept** and **Reject**. A third button: **Place Custom Order** opens an inline form.

Accept fires a `signal_accepted` event. Reject opens a small inline form with an optional free-text reason field and fires `signal_rejected`. "No reason" is valid — the reason field is never required. The user is encouraged to fill it when meaningful, especially when rejecting.

If the replay returns NONE, the panel shows "No signal this candle" with only the **Place Custom Order** button.

### Custom order form

Fields: side (BUY/SELL toggle), entry price (pre-filled with last close), TP price, SL price. On submit, fires `custom_order_placed`. The order is immediately rendered on the chart.

### Chart overlays

Learning Mode orders are drawn on `SwingPointsChart` using the same visual style as `TradesChart.tradeRects`:
- Green rectangle from entry to TP (TP zone)
- Red rectangle from entry to SL (SL zone)
- Horizontal entry line
- All three rendered only between the candle the order was placed and the candle it closes (hit TP or SL)

A new optional prop `learningOrders?: LearningOrder[]` is added to `SwingPointsChart`. The chart re-evaluates open orders on each candle advance: if the current candle's high/low crosses an order's SL or TP, the order is marked closed and a `order_closed` event is appended. `market_outcome` is computed at close time: `"tp_hit" | "sl_hit"`.

Closed orders remain visible on the chart with a faded fill (0.15 alpha vs 0.3 for open).

### Notes

A sticky "Add Note" button (bottom-right) is always visible throughout the entire Learning Mode session — it is never hidden or disabled regardless of what other UI is open (recommendation panel, custom order form, candle advance in progress). Clicking it opens a floating textarea overlay. Submitting fires a `note_added` event. Notes are free-text with no mandatory structure. The user can note anything at any point: "Price is in compression, signal feels premature", "Wish there was a volume filter here", or observations about a candle they just advanced past.

---

## Session data format

### Event log structure

```json
{
  "session_id": "uuid",
  "symbol": "TIAUSDT",
  "start_candle_index": 200,
  "started_at": "2026-06-29T10:00:00Z",
  "ended_at": "2026-06-29T10:42:00Z",
  "events": [...]
}
```

### Event types

```ts
type LearningEvent =
  | { type: "candle_advanced"; candle_index: number; timestamp: string; trend_state: TrendStateSnapshot }
  | { type: "signal_accepted"; candle_index: number; signal: ReplaySignal; timestamp: string }
  | { type: "signal_rejected"; candle_index: number; signal: ReplaySignal; reason?: string; timestamp: string }
  | { type: "custom_order_placed"; candle_index: number; order: LearningOrder; timestamp: string }
  | { type: "order_closed"; order_id: string; candle_index: number; market_outcome: "tp_hit" | "sl_hit"; pnl_pct: number; timestamp: string }
  | { type: "note_added"; candle_index: number; text: string; timestamp: string }
```

`ReplaySignal` is the existing signal shape already returned by `/api/replay` — no new fields needed.

`TrendStateSnapshot` is captured from the replay response at each `candle_advanced` event:

```ts
interface TrendStateSnapshot {
  trend_levels: TrendLevel[];    // full trend state at this candle
  all_points: SwingPoint[];      // swing points visible up to this candle
}
```

This captures the full market geometry at each step. Claude can read the trend state at the candle a signal was rejected and understand exactly what the chart looked like, not just the raw OHLCV.

`pnl_pct` on `order_closed` is computed as `(exit_price - entry_price) / entry_price * 100 * side_sign`. It does not account for leverage or fees — it is a directional signal only.

### Download

"Stop & Save" triggers:
1. Confirmation modal: "Save session? [N events recorded]"
2. `JSON.stringify(session, null, 2)` → Blob → anchor download as `learning-[symbol]-[date]-[session_id_prefix].json`
3. Session state cleared, Learning Mode deactivated.

---

## Hypotheses storage

After the user drops a session JSON to Claude Code for analysis, Claude writes findings to:

```
docs/learning/hypotheses.md
```

This file is committed to git and persists across sessions. It is the authoritative list of improvement hypotheses derived from learning sessions. Format per entry:

```markdown
## [YYYY-MM-DD] — [Symbol] — [short title]

**Session:** learning-TIAUSDT-2026-06-29-abc123.json
**Status:** pending | validated | rejected | implemented

### Hypotheses
1. [hypothesis text] — evidence: [event reference]
2. ...

### Notes
[optional additional context]

---
```

Statuses are updated manually by Claude or the user as hypotheses are tested:
- `pending` — recorded, not yet tested against live data
- `validated` — supported by live trade data or backtest
- `rejected` — contradicted by data
- `implemented` — turned into a code or config change

The file grows append-only. Old entries are never deleted — rejected hypotheses are still valuable context.

---

## Implementation scope

### Files touched

**Dashboard — new:**
- `dashboard/lib/useLearningSession.ts` — custom hook, owns all session state and event appending logic
- `dashboard/lib/learningTypes.ts` — TypeScript types for `LearningSession`, `LearningEvent`, `LearningOrder`, `TrendStateSnapshot`
- `dashboard/components/LearningRecommendationPanel.tsx` — bot recommendation card with Accept/Reject/Custom
- `dashboard/components/LearningOrderForm.tsx` — inline custom order entry form
- `docs/learning/hypotheses.md` — created as empty stub with header

**Dashboard — modified:**
- `dashboard/app/page.tsx` — add Learning Mode button, wire `useLearningSession`, pass `learningOrders` to chart, pass `learningMode` to `TimeScrubber`
- `dashboard/components/SwingPointsChart.tsx` — add `learningOrders?: LearningOrder[]` prop, draw tradeRects for open and closed learning orders, evaluate TP/SL crossings on each render
- `dashboard/components/TimeScrubber.tsx` — add `learningMode?: boolean` prop, hide slider and ◀ when true, show candle counter label

**Bot — no changes.** Learning Mode is entirely frontend. It calls the existing `/api/replay` endpoint exactly as the time-travel scrubber does. No new API routes are needed.

### What is explicitly out of scope

- No server-side session persistence (sessions exist only in browser RAM until downloaded)
- No automatic hypothesis extraction (Claude reads the JSON manually in a Code session)
- No connection to order_executor.py or any live/testnet order placement
- No leverage or fee simulation in pnl_pct
- No authentication changes

---

## Risk flags

- **Replay API performance:** Each "Next Candle" press fires one POST to `/api/replay`. At 300ms debounce the existing scrubber is fine; in Learning Mode single-press latency is the concern. If replay takes >1s the UX feels sluggish. The existing replay timeout (subprocess) should handle this — no new risk.
- **Session loss on tab close:** Session is in React state only. A browser crash or accidental tab close loses the session. Acceptable for v1 — the user controls the pace and can save frequently. Future improvement: `localStorage` checkpoint.
- **Large session files:** At ~1KB per event and sessions up to 500 candles, a session file is ~0.5MB max. Not a concern.
