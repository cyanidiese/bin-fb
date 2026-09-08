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
- **Every bug that was found and fixed**: what the bug was, what caused it, and how it was fixed. This gives future sessions full context on what has already been diagnosed and resolved, preventing the same issues from being re-investigated or reintroduced.

Also maintain a `TODO.md` with a prioritized task list. Mark items as [ ] pending, [~] in progress, [x] done.

Also maintain a `FEATURES.md` in the project root documenting every implemented feature. After completing any feature, update this file immediately — do not batch it. Each entry must describe: what the feature does, which files implement it, and any key configuration or behaviour details. This file is the authoritative reference for what the project can do, and must be kept accurate so future sessions do not re-implement existing functionality or miss available knobs.

The Librarian agent (`/librarian`) is responsible for writing and updating `FEATURES.md`, `CLAUDE_NOTES.md`, and `TODO.md`. Use it at the end of every session and after any significant implementation.

**Any change to existing functionality must be reflected in `FEATURES.md` immediately** — if a feature is modified, extended, removed, or its behaviour/config changes, update the relevant entry before closing the task. Outdated documentation is worse than no documentation.

---

## Proactive improvement mandate

When asked to "act like the wise owner" (or any equivalent), pull the latest data, identify the single highest-leverage improvement backed by actual numbers, propose it, and implement only after explicit approval.

Default priority: fix a loss-producing bug → tighten a filter with data → adjust a risk param with data → add a signal filter → add a new feature.

**Never block real orders without data.** Always show win rate, trade count, and USDT impact before applying any block.

Use the **`/bfb-analyze`** skill for the full step-by-step procedure.

---

## Big-feature spec rule

**If a feature discussion has consumed significant context (multiple back-and-forth exchanges, deep analysis, or design decisions), write a spec doc BEFORE writing any code.**

Save to: `docs/specs/YYYY-MM-DD-<topic>.md`

The spec must cover: what it does, why it's needed, the chosen approach and why, what was rejected and why, exact file/function touch points, and any risk flags. Do not start coding until the spec is committed to git. This prevents losing design decisions when context is compacted or the session ends.

---

## How to work with me

- **Read all files before suggesting anything.** Understand the full picture first.
- **Ask before rewriting.** If any strategy logic, order sizing, entry/exit conditions, indicator parameters, or config structure is unclear — ask me. Do not guess my intent.
- **One topic at a time.** Don't present 10 changes at once. Propose, get my approval, then implement.
- **Show diffs or clearly marked sections**, not full file rewrites unless truly necessary.
- **Explain every change** — not just what, but why. If there's a tradeoff, name it.
- **Flag anything risky** explicitly before touching it. Especially anything related to order execution, position sizing, or live mode.
- **Never switch to live mode** or remove testnet guards without my explicit instruction.
- **Never implement based on an assumption you have not verified.** If you are not certain how an external system behaves (API, library, protocol), say so explicitly and verify first — run a test, check the docs, or ask me. Do not write or revert code based on guesses.
- **Make decisions only after deep and thorough analysis.** Before assigning any configuration value, parameter, or setting — read the actual data (CSV, logs, backtest results). Do not rely on summaries alone. A metric that looks optimal on the surface (e.g. "highest profit") may be optimal for the wrong reason (e.g. "nearly zero trades = nearly zero losses"). Always ask: *why* is this value winning? Is it because entries are better, or because trading is suppressed? The latter is not an improvement.

---

## Code review

When reading code, assess and report on:

1. Bugs and logic errors
2. Silent failure points (uncaught exceptions, missing API error handling)
3. Strategy logic — flag anything ambiguous or potentially unintended
4. Order flow — entry, exit, stop loss, take profit completeness
5. State management — is position/order state tracked reliably?
6. WebSocket stability — reconnection, heartbeat, error recovery
7. Rate limiting — are API call limits respected?
8. Code clarity — naming, structure, separation of concerns

Present findings as: **critical** / **important** / **minor** — in that priority order.
Use the **`/code-review`** skill for structured review execution.

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

## Deployment

Deploy on a Linux VPS (Ubuntu) via Docker. Use the **`/bfb-deploy`** skill for the exact deploy procedure (graceful stop, Docker rebuild, feature branch handling).

Key invariants:
- Python source is baked into the Docker image — `git pull` alone does NOT update the running bot, always rebuild with `--build`
- `risk_config.json` is gitignored — update via SSH, never committed
- Always stop the bot gracefully (SIGTERM → wait → stop container) before deploying
- Never deploy without explicit user confirmation

Before recommending live mode, complete this go-live checklist:
- [ ] Testnet ran stably for at least 30 days
- [ ] All critical log events reviewed
- [ ] Risk parameters reviewed and confirmed
- [ ] Live API keys created with futures-only, no-withdrawal permissions
- [ ] Position size set conservatively for first live run
- [ ] Monitoring/alerting in place
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