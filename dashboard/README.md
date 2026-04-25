# Bot Dashboard

A Next.js web UI for viewing Binance Futures bot results.

## Start

```bash
cd dashboard
npm install
npm run dev
```

Open http://localhost:3000

## How it works

The Python bot writes `dashboard/public/results.json` on startup and after each candle close.
The dashboard fetches that file on page load and renders charts and tables.
Refresh the page to see the latest data.

## JSON Schema

`public/results.json` must match this shape:

```typescript
{
  symbol: string            // e.g. "BTCUSDT"
  timeframe: string         // e.g. "15m"
  mode: "testnet" | "live"
  generated_at: string      // ISO 8601 UTC
  current_price: number
  trend_levels: Array<{
    level: number           // 1, 2, 3
    direction: "ASC" | "DESC" | "NONE"
    bos: number | null      // Break of Structure price
    bos_since: string | null
    last_high: { price: number; time: string } | null
    last_low:  { price: number; time: string } | null
  }>
  all_points: Array<{
    time: string            // ISO 8601 UTC
    level: number
    type: "high" | "low"
    price: number
  }>
  klines: Array<{
    time: number            // Unix timestamp (seconds)
    open: number
    high: number
    low: number
    close: number
  }>
  signals: Array<{
    level: number
    side: "BUY" | "SELL"
    signal_type: string
    target: number
    stop: number | null
  }>
}
```

## Python integration

`bot/exporter.py` is called automatically by the bot after each candle close.
It writes the latest 200 klines and all swing points to `dashboard/public/results.json`.
No manual steps needed — just run the bot and refresh the dashboard.
