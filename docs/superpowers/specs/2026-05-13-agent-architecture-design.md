# Agent Architecture Design
**Date:** 2026-05-13  
**Status:** Approved  
**Location:** `.claude/agents/` (project-local)

---

## Goals

Build a suite of 8 specialized Claude Code agents that assist in developing, maintaining, and improving the Binance Futures trading bot. Two primary objectives drive every design decision:

1. **System stability** — no broken modules, no regressions, full test coverage over time
2. **Maximum USDT profit** — every feature and strategy change is evaluated against real gain, not abstract metrics

---

## Invocation Model

**Hybrid — mostly auto, explicit naming also works.**

- Agents are auto-triggered by me (main Claude) based on context and task size
- User can explicitly invoke any agent by name: *"As Architect, check this solution"*
- Agents never run autonomously — every pipeline ends with a proposal or a completed task that the user has approved

---

## Flow Model

### Large Feature Pipeline (full pipeline, option A)

Triggered when: new functionality, multi-module changes, anything touching order execution or real-money paths.

```
User → Receptionist → Architect ──► Trader (standing advisor on all bot/ runtime changes)
                           │              ◄──► Analyst (if strategy/data questions exist)
                           └──────────────────────────────────────────► Coder → Tester → Librarian
```

Trader is a **standing advisor** for Architect: any change touching `bot/` runtime modules goes through a Trader constraint check before Architect finalises scope. Trader either clears it ("no exchange constraints") or flags issues. Analyst joins when the change also has strategy or data implications.

### Small Fix Flow (on-demand, option C)

Triggered when: bugfixes, isolated UI changes, single-module tweaks, config updates.

```
User → Me (triage) → Coder → Tester? → Librarian (session end)
```

Two escalation gates prevent a "small" fix from being mishandled:

- **Gate 1 — Triage**: Before dispatching Coder, I check escalation criteria. Any hit → reroute to full pipeline.
- **Gate 2 — Coder escalates**: If Coder discovers complexity mid-task, it stops and reports back. No half-written implementation left behind.

**Escalation criteria (either gate):**
- Change touches 3+ modules
- Involves `OrderExecutor`, `RiskManager`, `LeverageTracker`, or any real-money path
- Changes data file formats shared with the dashboard (`results_*.json`, `risk_state.json`, etc.)
- Breaks existing test contracts
- Requires a new dependency

### Proactive Improvement Flow

Triggered when: user asks "what should we do next?" or "what can we improve?"

```
User asks → Planner reads project state
               │
               ├──► Analyst (reads backtest results, efficiency scores, decision log)
               │       │
               └──► Trader (validates against Binance constraints)
                         │
                         ▼
                    Proposal to user (STOP — no implementation without approval)
                         │
                    User approves → triggers large feature or small fix flow
```

---

## Agents

### 1. Receptionist

| Field | Value |
|---|---|
| **Model** | Haiku 4.5 |
| **Triggers** | Start of large feature pipeline |
| **Outputs to** | Architect |
| **Tools** | `Read`, `Bash` |
| **Skills** | None |

Transforms a raw feature idea into a structured brief. Before asking anything, reads `CLAUDE_NOTES.md`, `TODO.md`, and the relevant modules to ask *smart, codebase-aware* questions — not generic "what's the acceptance criteria" but "does this interact with `LeverageTracker`? does it change the decision log format?". Resolves ambiguity one question at a time. Outputs a brief Architect can act on immediately.

Haiku is sufficient — this is structured Q&A, not deep reasoning.

---

### 2. Architect

| Field | Value |
|---|---|
| **Model** | Sonnet 4.6 |
| **Triggers** | After Receptionist on large features; explicit "check this solution / review this architecture" |
| **Outputs to** | Domain cluster (if needed) → Coder |
| **Tools** | `Read`, `Bash`, `Glob`, `Grep` |
| **Skills** | `project-knowledge`, `superpowers:systematic-debugging` |

The codebase oracle. For every incoming feature: checks if it already exists (this project has a history of re-implementing things), identifies which modules are touched, detects conflict risks, produces the minimal change surface. For any change touching `bot/` runtime modules, always consults Trader before finalising scope — Trader either clears it or flags exchange-side constraints Architect may not have seen. When investigating bugs, uses `superpowers:systematic-debugging` to reproduce → hypothesise → identify root cause before handing a diagnosis to Coder. Maintains a living module map via `project-knowledge` so it doesn't re-explore from scratch each session.

---

### 3. Trader

| Field | Value |
|---|---|
| **Model** | Sonnet 4.6 |
| **Triggers** | Standing advisor: all changes touching `bot/` runtime modules; jointly with Analyst on strategy/profit changes; explicit trading domain questions |
| **Outputs to** | Architect (constraint report — clears or flags); Analyst (joint evaluation) |
| **Tools** | `Read`, `WebFetch`, `WebSearch`, `Bash` |
| **Skills** | None |

Binance Futures domain expert and standing advisor to Architect. Answers: *"Can we do this on Binance?"* Knows exchange constraints — lot sizes, min notionals, leverage tiers, rate limits, order types, WebSocket behaviour, testnet quirks. Advises on order placement timing, stop logic, and margin calculations. Proactively flags exchange-side implications in Architect's proposed scope that Architect may not have recognised — not just answering when asked, but catching what wasn't asked. Can fetch current Binance API docs when constraints need verification. Keeps the bot safe from exchange-side failures.

---

### 4. Analyst

| Field | Value |
|---|---|
| **Model** | Sonnet 4.6 |
| **Triggers** | Strategy improvement questions, preset tuning, "which symbols are underperforming?"; jointly with Trader on profit-related features |
| **Outputs to** | Planner (improvement proposals); Architect (data-backed feature decisions) |
| **Tools** | `Read`, `Bash` |
| **Skills** | None |

Reads the actual data — `backtest_results_*.json`, `preset_efficiency_*.json`, `data/decision_log_*.json`, `data/balance_history_*.json` — and extracts what is working and what isn't. Calculates real USDT gain potential per proposed change. Answers are always in USDT, never abstract points or percentages without context. Works with Trader to validate that strategy improvements are both data-backed and exchange-feasible.

---

### 5. Coder

| Field | Value |
|---|---|
| **Model** | Sonnet 4.6 |
| **Triggers** | After Architect approves scope (large features); directly on small fixes; explicit "implement this" |
| **Outputs to** | Tester |
| **Tools** | `Read`, `Edit`, `Write`, `Bash` |
| **Skills** | `superpowers:verification-before-completion` |

Primary implementation agent. Writes Python, TypeScript, config files. Enforces code quality: well-named identifiers, comments only where the *why* is non-obvious, no over-engineering, no half-finished implementations. Owns refactoring when Architect identifies structural problems. Fixes bugs jointly with Architect when root cause analysis is needed.

**Escalation gate built in:** If during analysis Coder finds any escalation criterion (see Flow Model), it stops immediately, reports what it found, and hands back to main Claude to restart as a large feature.

---

### 6. Tester

| Field | Value |
|---|---|
| **Model** | Sonnet 4.6 |
| **Triggers** | Automatically after every Coder session (large features); on-demand for risky small fixes; explicit "check/update tests" |
| **Outputs to** | Librarian (verified); back to Coder if tests fail |
| **Tools** | `Read`, `Edit`, `Write`, `Bash` |
| **Skills** | `superpowers:test-driven-development` |

Owns the test suite. Creates new tests for every new module, updates existing tests when implementation changes, checks whether tests remain valid after refactors. Runs the full suite before signalling done. Current coverage: 31 tests across 3 modules — systematic expansion is an ongoing responsibility, not a one-time task.

---

### 7. Librarian

| Field | Value |
|---|---|
| **Model** | Haiku 4.5 |
| **Triggers** | Automatically at end of every session; after any resolved design decision; explicit "update notes / remember this" |
| **Outputs to** | Nothing — terminal node |
| **Tools** | `Read`, `Edit`, `Write` |
| **Skills** | `project-knowledge` |

Keeps `CLAUDE_NOTES.md`, `TODO.md`, and spec documents current. Saves decisions immediately, not at session end when context may be lost. Prunes stale entries, marks completed items, records new decisions with rationale. Writes focused summaries so future agents receive only the context they need — not the entire 1000-line history.

Haiku is intentional: documentation writing does not require deep reasoning, and Librarian runs on every session end. Using Haiku here saves meaningful tokens across the lifetime of the project.

---

### 8. Planner

| Field | Value |
|---|---|
| **Model** | Sonnet 4.6 |
| **Triggers** | Explicit "what should we do next?", "what can we improve?", "what's the plan?"; when Analyst surfaces a significant data insight |
| **Outputs to** | User (proposal only) → if approved, triggers appropriate flow |
| **Tools** | `Read`, `Bash` |
| **Skills** | `superpowers:writing-plans` |

Proposes what to build next with a focus on two goals: system stability and maximum USDT profit. Reads the full project state, pulls data from Analyst, validates constraints with Trader, then produces a prioritised list of improvements with clear reasoning. Always ends at a proposal to the user — **never triggers implementation**. User approval is required before anything enters the pipeline.

Sonnet over Opus: the domain knowledge lives in the agent prompts and the project data files, not in model reasoning depth. Opus would cost 5× more with no meaningful gain here.

---

## Token Cost Summary

| Agent | Model | Frequency | Relative cost |
|---|---|---|---|
| Receptionist | Haiku 4.5 | Large features only | Very low |
| Architect | Sonnet 4.6 | Large features + explicit | Medium |
| Trader | Sonnet 4.6 | All bot/ runtime changes (standing) + domain questions | Medium |
| Analyst | Sonnet 4.6 | Data questions + Planner | Low–medium |
| Coder | Sonnet 4.6 | Every implementation | High (unavoidable) |
| Tester | Sonnet 4.6 | After implementation | Medium |
| Librarian | Haiku 4.5 | Every session end | Very low |
| Planner | Sonnet 4.6 | Explicit only | Low |

Key token-saving decisions:
- Haiku on the two highest-frequency lightweight agents (Receptionist, Librarian)
- Sonnet everywhere else — Opus nowhere
- Small-fix flow skips Receptionist, Architect, and sometimes Tester entirely
- Planner only activates on explicit user request, never on a schedule

---

## Full Connection Map

```
                            USER
                             │
          ┌──────────────────┼──────────────────────┐
          │ large feature    │ small fix             │ "what's next?"
          ▼                  │                       ▼
    Receptionist             │                    Planner
          │                  │                    ╱     ╲
          ▼                  │              Analyst ◄──► Trader
      Architect ◄────────────┘
          │
    Trader (always, for bot/ changes) ◄──► Analyst (if strategy/data involved)
          │ constraint report back to Architect
          ▼
        Coder ──► [escalation?] ──► back to full pipeline
          │
          ▼
        Tester ──► [fail?] ──► Coder
          │
          ▼
       Librarian  ◄──────────────── also runs every session end
```

---

## Implementation Notes

- All agents live in `.claude/agents/` (project-local)
- File format: `{agent-name}.md` with YAML frontmatter (`name`, `description`, `model`, `tools`, `color`)
- No inter-agent direct calls — main Claude always orchestrates
- Agents do not run autonomously; every flow ends at user approval or a completed, user-initiated task
- Plugin packaging (`~/.claude/plugins/`) deferred until design is stable in practice
