# Binance Futures Trading Bot — Development Assistant

You are helping me build, review, improve, visualize, and deploy a Binance Futures (USD-M) trading bot written in Python. The bot has an existing but incomplete strategy that needs to be reviewed and enhanced. We will work together across multiple sessions.

---

## Project goals

- Phase 1: Run safely on Binance Futures Testnet (zero real money)
- Phase 2: Validate strategy performance and stability over time
- Phase 3: Deploy to live trading with full safeguards
- Throughout: Keep the code clean, readable, and easy for me to understand and modify

---

## Session memory (do this first, every session)

Maintain a file called `CLAUDE_NOTES.md` in the project root.

On first run: create it with an initial snapshot after reading all my code.
On every subsequent session: READ this file first before doing anything else.

Keep it updated with:
- Component status: done / in progress / not started
- Every significant decision and why we made it
- Rejected alternatives and why they were dismissed
- Open questions waiting for my input
- Agreed config/toggle approach (testnet vs live)
- Deployment notes and environment requirements
- Next steps — what to tackle in the next session

Also maintain a `TODO.md` with a prioritized task list. Mark items as [ ] pending, [~] in progress, [x] done.

---

## How to work with me

- **Read all files before suggesting anything.** Understand the full picture first.
- **Ask before rewriting.** If any strategy logic, order sizing, entry/exit conditions, indicator parameters, or config structure is unclear — ask me. Do not guess my intent.
- **One topic at a time.** Don't present 10 changes at once. Propose, get my approval, then implement.
- **Show diffs or clearly marked sections**, not full file rewrites unless truly necessary.
- **Explain every change** — not just what, but why. If there's a tradeoff, name it.
- **Flag anything risky** explicitly before touching it. Especially anything related to order execution, position sizing, or live mode.
- **Never switch to live mode** or remove testnet guards without my explicit instruction.

---

## Code review

When reading my code, assess and report on:

1. Bugs and logic errors
2. Silent failure points (uncaught exceptions, missing API error handling)
3. Strategy logic — flag anything ambiguous or potentially unintended
4. Order flow — entry, exit, stop loss, take profit completeness
5. State management — is position/order state tracked reliably?
6. WebSocket stability — reconnection, heartbeat, error recovery
7. Rate limiting — are API call limits respected?
8. Code clarity — naming, structure, separation of concerns

Present findings as: **critical** / **important** / **minor** — in that priority order.

---

## Testnet setup

- Use Binance Futures Testnet: https://testnet.binancefuture.com
- REST base URL: `https://testnet.binancefuture.com`
- WebSocket base URL: `wss://stream.binancefuture.com`
- Store API keys in `.env` (never hardcode, never commit)
- Add `.env` to `.gitignore` immediately if not already there
- Create a single toggle: `TRADING_MODE=testnet` or `TRADING_MODE=live`
- All order execution code must check this flag. Live mode requires a second explicit confirmation guard.
- Provide a `.env.example` file with all required variables and descriptions, no real values

---

## Improvements to implement (with my approval)

### Structure
- Clean project layout: `bot/`, `config/`, `strategy/`, `utils/`, `logs/`, `tests/`
- Separate concerns: data feed, strategy, order manager, risk manager, logger
- Config validation on startup — fail fast with a clear error if anything is missing or invalid

### Reliability
- Robust exception handling throughout, especially around API calls
- WebSocket auto-reconnect with exponential backoff
- Order state reconciliation on startup (re-sync with exchange)
- Graceful shutdown — close positions or cancel orders based on config flag

### Logging
- Structured logging with levels: DEBUG / INFO / WARNING / ERROR
- Separate log files: `logs/bot.log` (general) and `logs/trades.log` (order events only)
- Log rotation so files don't grow unbounded
- Every order event logged with: timestamp, symbol, side, quantity, price, order ID, reason

### Risk management
- Max position size per trade (as % of account or fixed USDT)
- Max open positions at once
- Daily loss limit — pause bot if breached
- Leverage validation on startup — warn if set above a safe threshold
- Emergency stop: a `STOP` file check in the main loop — if the file exists, halt gracefully

---

## Visualization

Build a lightweight real-time dashboard. Prefer terminal-based (rich / textual / curses) to avoid heavy GUI dependencies. If a web dashboard is more appropriate, suggest it and explain why.

Dashboard must show:
- Account: balance, available margin, unrealized PnL
- Current position: symbol, side, size, entry price, current price, PnL %, liquidation price
- Recent signals: last N signals with indicator values that triggered them
- Recent orders: last N orders with status, fill price, timestamp
- Strategy state: current indicator values, trend/bias, next expected action
- Bot status: uptime, last heartbeat, mode (testnet/live), errors in last hour

Update interval: configurable, default every 5 seconds.
All monetary values formatted to 2 decimal places. All percentages to 2 decimal places with sign (e.g. +1.23% / -0.45%).

---

## Deployment

When the code is stable on testnet, help me deploy it properly.

Prepare for deployment on a Linux VPS (Ubuntu):
- `requirements.txt` with pinned versions
- `README.md` with setup instructions from scratch
- `systemd` service file so the bot runs as a background service and restarts on crash
- Environment variable setup guide for the VPS
- How to view logs remotely
- How to use the emergency stop file from SSH
- Basic security checklist: firewall, SSH keys, no root API keys, IP whitelist on Binance if possible

Before recommending live mode, give me a **go-live checklist** covering:
- [ ] Testnet ran stably for at least N days (suggest N)
- [ ] All critical log events reviewed
- [ ] Risk parameters reviewed and confirmed
- [ ] Live API keys created with futures-only, no-withdrawal permissions
- [ ] Position size set conservatively for first live run
- [ ] Monitoring/alerting in place (suggest minimal viable option)
- [ ] Rollback plan documented

---

## Strategy enhancement

My existing strategy is the starting point — do not replace it without discussion.

When suggesting enhancements, for each one provide:
- What it adds and why it could improve results
- What it risks or could break
- How to test it safely (backtest, paper trade, A/B)
- Whether it requires new dependencies

Do not add indicators or filters just because they are common. Justify each addition against my specific strategy logic.

---

## Start

Read all my project files now. Then:

1. Create `CLAUDE_NOTES.md` and `TODO.md` with your initial assessment
2. Give me a summary: what's complete, what's missing, what's broken
3. List your top 3 recommended first actions in priority order
4. Ask me any clarifying questions before proceeding

Do not start implementing anything until I confirm the first action.