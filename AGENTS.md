# Project Agent Instructions

## Review cadence

**One lightweight review at the end of a complete feature — not per task.**

When using `superpowers:subagent-driven-development`, skip ALL per-task loops:
- No spec-compliance review per task
- No code-quality review per task

After all tasks are done, run a single lightweight final check:
- Use `sonnet` (not `opus`) for the reviewer subagent
- Keep the review focused: confirm tests pass, no obvious bugs, nothing missing from spec
- Do NOT produce exhaustive audits, edge-case inventories, or long recommendation lists
- A short pass/fail with a few lines of notes is the target output

Why: per-task reviews and deep final audits burn tokens without meaningfully improving quality on a focused implementation plan. Ship it clean, not perfect.
