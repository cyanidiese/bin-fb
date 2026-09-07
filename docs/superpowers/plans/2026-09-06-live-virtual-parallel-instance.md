# Live-Mode Virtual Instance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `main.py` in live mode with real orders switched off, alongside the testnet bot, so preset statistics accumulate on real charts in exactly the files a live-mode bot will later read.

**Architecture:** Same image, second compose service, `TRADING_MODE=live VIRTUAL_ONLY=1`, **no credentials**. Files stay in their normal locations with the existing `_live` suffix — no separate directories — because a future live-mode main bot reads `data/..._live.json` and must find this history waiting for it. The helper is not a separate system: **remove `VIRTUAL_ONLY`, add credentials, and it becomes the live bot**, already trained.

**Tech Stack:** Python 3.12, python-binance, pytest, Next.js 16 dashboard, Docker Compose.

**Spec:** `docs/specs/2026-09-06-live-virtual-parallel-instance.md`

## Global Constraints

- The testnet bot trades. Every change must leave `virtual_only=False` behaviour **byte-identical**.
- `bot/virtual_order_simulator.py`, `bot/virtual_tracker.py`, `bot/analyzer.py`, `bot/fake_order.py` **must not change** — the simulation has to be identical across modes or the comparison is meaningless.
- The live instance holds **no API credentials** and sends **no Telegram**. No task may add either.
- Files keep their normal paths. Category-B suffixes apply **only when `virtual_only`** — the
  primary always owns the unsuffixed name in either mode, so existing filenames and every
  dashboard reader are untouched. (Amended 2026-09-07: a mode-keyed rule would hand the
  secondary `risk_state.json` once the primary went live, blanking the trading bot's risk page.)
- The secondary always runs the **opposite** mode to `bot_mode.json`, resolved at startup and
  re-established by a clean exit + container restart when the file flips. See spec Amendment 2026-09-07.
- Baseline: **456 passing** (Tasks 1 and 1a already committed: `ffd965a`, `14db2f7`).
- Never deploy without explicit user confirmation.
- Resource headroom measured 2026-09-06: 1,998 MB RAM available, `bot` uses 138 MB at 0.44% CPU, 5.0 GB disk free with `data/` at 144 MB. A second instance is comfortable — but its logs must rotate (Task 6 Step 2b) or disk becomes the failure mode.

## The shared-state taxonomy

Every bug found in review was shared mutable state assumed to be per-instance. There are two kinds, handled two different ways.

**A. Single-owner control resources — the virtual instance does not participate at all.**
Suffixing these would break the handover, because the dashboard writes to the unsuffixed name.

| resource | why it must not be shared | after the switch to live |
|---|---|---|
| `data/bot_pid.json` | dashboard Stop button reads it (`api/bot/stop/route.ts:9`) | flag removed → written normally |
| `data/bot_command*.json` | the bot **deletes** the command after reading (`mode_manager.py:73`); a Stop consumed by the wrong instance leaves the trading bot running while the dashboard reports success | flag removed → polled normally |
| `dashboard/public/bot_state.json` | "is the bot alive" indicator | flag removed → written normally |
| Telegram `getUpdates` | delivered exactly once; `do_pause`/`do_resume`/`do_enable` mutate the shared registry | flag removed → menu starts |
| `risk_config.json`, `symbol_registry.json` | `weight_rebalancer` calls `save_risk_config()` every candle; `_auto_disable()` writes the registry | flag removed → writes normally |

**B. Per-instance data — mode-suffixed off test.**

| file | writer |
|---|---|
| `logs/bot.log` | compose redirect + `main.py:92` |
| `logs/trades.log` | `main.py:101` — two `RotatingFileHandler`s on one file fight during rollover |
| `logs/analysis.jsonl` | `main.py:306` |
| `data/system_log.json` | `main.py:126` |
| `dashboard/public/alert_state.json` | `main.py:127` |
| `dashboard/public/risk_state.json` | `risk_manager.py:35` — worst case: the virtual instance has balance 0, so it would zero the trading bot's risk page |
| `dashboard/public/results_{symbol}.json` | `exporter.py:96` — takes `mode`, ignores it |

`dashboard/public/symbols.json` stays shared: both read the same registry and write an identical list.

Everything else in `data/` is already `_test` suffixed — verified against all 1,562 files on the server. That is why no directory split is needed, and why the handover is free.

---

### Task 2: The `virtual_only` guards

The safety-critical task. The flag may only ever **skip** work; it must never change what a real order does. Most of these guards protect the **testnet bot**, not the live one.

**Files:**
- Modify: `main.py`
- Test: `tests/test_virtual_only_skips_real_orders.py`

**Interfaces:**
- Consumes: `Settings.virtual_only` (Task 1).
- Produces: a process that runs analyzers and the virtual simulator and touches nothing else.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_virtual_only_skips_real_orders.py
"""A virtual-only instance must not trade, must not write shared state, and must not
consume single-owner resources.

Each guard protects something specific; the docstrings say what, because a future
reader deleting one "harmless" guard is exactly how this breaks.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def _guarded(call: str) -> bool:
    """True if every occurrence of `call` sits under a virtual_only guard."""
    src = MAIN.read_text()
    for m in re.finditer(re.escape(call), src):
        window = src[max(0, m.start() - 900):m.start()]
        if 'virtual_only' not in window:
            return False
    return True


def test_leverage_brackets_are_guarded():
    """futures_leverage_bracket is private — 401 without credentials."""
    assert _guarded('fetch_leverage_brackets(')


def test_balance_fetch_is_guarded():
    """futures_account is private, and virtual sizing uses rank-pool balances."""
    assert _guarded('_get_fresh_balance()')


def test_placement_is_guarded():
    assert _guarded('_try_place_order(')


def test_weight_rebalancer_is_guarded():
    """It calls save_risk_config() every candle. Unguarded, the live instance would
    retune the TESTNET bot's real symbol allocation from live virtual results."""
    assert _guarded('weight_rebalancer.on_candle_close(')


def test_exchange_symbol_check_is_guarded():
    """_auto_disable() writes the shared symbol_registry.json, disabling a symbol for
    the testnet bot too."""
    assert _guarded('check_symbols_on_exchange(')


def test_reconcile_is_guarded():
    """Private endpoint at startup, and closing positions the bot does not know about
    is meaningless for an instance that opens none."""
    assert _guarded('reconcile_with_exchange(')


def test_telegram_menu_is_guarded():
    """Telegram delivers each update exactly once. Two pollers on one token means
    commands land on a coin flip — including do_pause/do_resume/do_enable, which
    mutate the shared symbol registry."""
    assert _guarded('telegram_menu.run()')


def test_flag_only_skips_never_alters():
    """Guards must be plain skips. A virtual_only branch that CHANGES an order's size,
    price, side or leverage would put the flag on the real-money path."""
    src = MAIN.read_text()
    for m in re.finditer(r'virtual_only', src):
        line_start = src.rfind('\n', 0, m.start()) + 1
        line = src[line_start:src.find('\n', m.start())]
        assert not re.search(r'(quantity|entry|tp|sl|leverage)\s*=', line), \
            f'virtual_only must not alter order parameters: {line.strip()}'
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_virtual_only_skips_real_orders.py -q`
Expected: FAIL on the guard tests — none exist yet.

- [ ] **Step 3: Locate every call site**

```bash
grep -n "fetch_leverage_brackets(\|_get_fresh_balance()\|_try_place_order(\|weight_rebalancer.on_candle_close(\|check_symbols_on_exchange(\|reconcile_with_exchange(\|telegram_menu.run()" main.py
```

Record the line numbers before editing — several appear more than once.

- [ ] **Step 4: Add the flag and the banner**

In `main.py`, after `first_settings` is defined:

```python
    _virtual_only = first_settings.virtual_only
    if _virtual_only:
        logger.warning(
            "VIRTUAL-ONLY instance: no real orders, no private endpoints, no Telegram, "
            "no shared-config writes. Collecting preset statistics only."
        )
```

- [ ] **Step 5: Guard every call site**

Each guard is a plain skip. Do not restructure the code inside.

```python
    if not _virtual_only:
        await order_executor.fetch_leverage_brackets(symbols)
```

```python
        balance = 0.0 if _virtual_only else await _get_fresh_balance()
        if balance > 0:
            risk_manager.update_balance(balance)
```

```python
        # Placement pass — indent the existing loop one level, otherwise unchanged.
        if not _virtual_only:
            ...
```

```python
        if not _virtual_only:
            weight_rebalancer.on_candle_close(candle_ts)
```

```python
    if not _virtual_only:
        await order_executor.check_symbols_on_exchange(symbols)
```

```python
    if not _virtual_only:
        await order_executor.reconcile_with_exchange()
```

```python
    _menu_task = None
    if not _virtual_only:
        _menu_task = asyncio.create_task(telegram_menu.run())
```

`_menu_task` is cancelled during shutdown — guard that too:

```python
    if _menu_task is not None:
        _menu_task.cancel()
```

`candle_ts` is assigned inside the placement block: **move its assignment above the
guard** so the rebalancer guard can still reference it.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_virtual_only_skips_real_orders.py -q`
Expected: 8 passed

- [ ] **Step 7: Prove the trading path is untouched**

Run: `python3 -m pytest tests/ -q`
Expected: 464 passed. **A pre-existing failure here is a stop signal** — it means a guard
altered the real path.

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_virtual_only_skips_real_orders.py
git commit -m "feat(main): guard real-order and shared-state paths behind virtual_only

Most guards protect the TESTNET bot: weight_rebalancer rewrites risk_config every
candle, _auto_disable writes the shared registry, and the Telegram queue is
consume-once so commands would land on a coin flip.

A test asserts no virtual_only branch alters order size, price, side or leverage,
so the flag can only skip work."
```

---

### Task 2b: Single-owner control resources

`bot_pid.json`, the command channel and `bot_state.json` have exactly one legitimate
owner. The virtual instance must not participate — suffixing them would break the
dashboard, which writes to the unsuffixed names.

**Files:**
- Modify: `main.py` — `_write_bot_pid()`, `_write_bot_state()`, command polling call sites
- Test: `tests/test_virtual_only_control_resources.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_virtual_only_control_resources.py
"""Single-owner resources must have exactly one owner: the trading bot.

bot_pid.json drives the dashboard's Stop button, and mode_manager DELETES a command
after reading it. If the virtual instance owns the PID or eats the command, pressing
Stop reports success while the trading bot keeps running. That is the failure this
prevents.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def _guarded(call: str) -> bool:
    src = MAIN.read_text()
    hits = list(re.finditer(re.escape(call), src))
    assert hits, f'{call} not found in main.py — has it been renamed?'
    for m in hits:
        window = src[max(0, m.start() - 900):m.start()]
        if 'virtual_only' not in window:
            return False
    return True


def test_pid_write_is_guarded():
    """Whoever owns bot_pid.json is who the Stop button kills."""
    assert _guarded('_write_bot_pid(')


def test_bot_state_write_is_guarded():
    """The 'is the bot alive' indicator must reflect the trading bot."""
    assert _guarded('_write_bot_state(')


def test_command_polling_is_guarded():
    """mode_manager deletes the command file after reading it — consume-once."""
    assert _guarded('poll_command(')
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_virtual_only_control_resources.py -q`
Expected: FAIL, or an assertion naming a helper that does not exist under that name —
in which case correct the test to the real name found in Step 3.

- [ ] **Step 3: Find the real names**

```bash
grep -n "bot_pid\|_BOT_STATE_PATH\|poll_command\|check_command" main.py bot/mode_manager.py | head
```

Use the actual function names in both the test and the guards. Do not invent names.

- [ ] **Step 4: Guard them**

```python
    # The dashboard Stop button kills whatever PID is in this file, and the command
    # channel is consume-once. A virtual-only instance must own neither, or a Stop
    # would report success while the trading bot kept running.
    if not _virtual_only:
        _write_bot_pid()
```

Apply the same guard to the bot-state write and to command polling.

- [ ] **Step 5: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_virtual_only_control_resources.py -q` → 3 passed
Run: `python3 -m pytest tests/ -q` → 467 passed

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_virtual_only_control_resources.py
git commit -m "feat(main): virtual-only instance owns no control resources

bot_pid.json, the file command channel and bot_state.json have one legitimate
owner. The command channel is consume-once, so a Stop taken by the wrong instance
would report success while the trading bot kept running."
```

---

### Task 1c: Mirror mode resolution, and one source of truth for mode

Amended in 2026-09-07. Two latent bugs and the mirror behaviour all live in the same
five lines of mode resolution, so they are one task.

Today `current_mode` comes from `bot_mode.json` (`main.py:158`) but `DataFeed` is built
from `settings.trading_mode` (env `TRADING_MODE`, `main.py:320`). They agree on the server
right now (`TRADING_MODE=test`, `bot_mode.json` absent) which is why nothing has broken —
but they are independent, so the first genuine mode change desynchronises the file suffix
from the endpoint being read.

**Files:**
- Modify: `bot/mode_manager.py` — add `opposite_mode()`, replace `forced_mode` with `mirror`
- Modify: `main.py:153-158` — resolve one mode and stamp it onto the settings the feed uses
- Modify: `main.py` shutdown region — add the mirror watcher task
- Modify: `dashboard/components/settings/TradingMode.tsx:62` — make the dialog truthful
- Test: `tests/test_mirror_mode.py`

**Interfaces:**
- Consumes: `Settings.virtual_only` (Task 1), `_VIRTUAL_ONLY` (Task 2b)
- Produces: `mode_manager.opposite_mode(m) -> str`; `ModeManager(mirror: bool)`;
  `main._resolve_mode(base_settings, mode_manager) -> str`; watcher coroutine
  `main._mirror_watch(mode_manager)`. Task 4 keys its suffix off `_VIRTUAL_ONLY`, not off
  this mode, so it does not depend on the value — only on the mode string being correct.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mirror_mode.py
import json
from bot.mode_manager import ModeManager, opposite_mode


def test_opposite_mode_flips_both_ways():
    assert opposite_mode('test') == 'live'
    assert opposite_mode('live') == 'test'


def test_opposite_mode_defaults_unknown_to_live():
    """An unreadable/garbage mode must not silently mirror the primary."""
    assert opposite_mode('') == 'live'
    assert opposite_mode('nonsense') == 'live'


def _mm(tmp_path, mode, mirror):
    mp = tmp_path / 'bot_mode.json'
    if mode is not None:
        mp.write_text(json.dumps({'mode': mode}))
    return ModeManager(mode_path=mp, command_path=tmp_path / 'c.json',
                       result_path=tmp_path / 'r.json', mirror=mirror)


def test_primary_follows_the_file(tmp_path):
    assert _mm(tmp_path, 'live', mirror=False).current_mode == 'live'


def test_mirror_takes_the_opposite(tmp_path):
    assert _mm(tmp_path, 'live', mirror=True).current_mode == 'test'
    assert _mm(tmp_path, 'test', mirror=True).current_mode == 'live'


def test_mirror_of_missing_file_is_live(tmp_path):
    """No file means the primary defaults to test, so the mirror must be live."""
    assert _mm(tmp_path, None, mirror=True).current_mode == 'live'


def test_mirror_detects_a_flip(tmp_path):
    mm = _mm(tmp_path, 'test', mirror=True)
    assert mm.mirror_target_changed() is False
    (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'live'}))
    assert mm.mirror_target_changed() is True


def test_primary_never_reports_a_flip(tmp_path):
    mm = _mm(tmp_path, 'test', mirror=False)
    (tmp_path / 'bot_mode.json').write_text(json.dumps({'mode': 'live'}))
    assert mm.mirror_target_changed() is False


def test_torn_or_garbage_file_is_not_a_flip(tmp_path):
    """A half-written file must not trigger a restart loop."""
    mm = _mm(tmp_path, 'test', mirror=True)
    (tmp_path / 'bot_mode.json').write_text('{not json')
    assert mm.mirror_target_changed() is False


def test_resolved_mode_is_stamped_onto_settings():
    """The feed must read the market whose name the files carry."""
    import re
    src = open('main.py').read()
    body = src[src.index('def _resolve_mode'):]
    body = body[:body.index('\nasync def', 1) if '\nasync def' in body else 2000]
    assert 'trading_mode' in body, 'resolved mode must be written back to settings'
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mirror_mode.py -v`
Expected: FAIL — `ImportError: cannot import name 'opposite_mode'`

- [ ] **Step 3: Implement in `bot/mode_manager.py`**

Add above the class:

```python
def opposite_mode(mode: str) -> str:
    """The mode a mirror instance runs, given the primary's mode.

    Anything other than an explicit 'live' mirrors to 'live'. Defaulting the unknown
    case to 'live' is deliberate: the primary defaults to 'test', so 'live' is the only
    answer that cannot leave both instances in the same mode writing the same files.
    """
    return 'test' if mode == 'live' else 'live'
```

Replace the `forced_mode` parameter with `mirror`:

```python
        mirror: bool = False,
```
```python
        # A mirror instance runs whatever mode the primary is not running, so the two
        # never share a file suffix. It resolves this once at startup and re-resolves by
        # exiting and letting the container restart — see mirror_target_changed().
        self._mirror = mirror
        self.current_mode: str = (
            opposite_mode(self._read_mode()) if mirror else self._read_mode()
        )
```

And add the watcher predicate:

```python
    def mirror_target_changed(self) -> bool:
        """True when a mirror instance is no longer the opposite of the primary.

        Returns False for a primary, and False when the file cannot be read — a torn or
        deleted file must never trigger a restart loop. _read_mode() already swallows
        decode errors and returns 'test', so an unreadable file looks like 'test'; guard
        on existence so a momentarily-absent file is not read as a real flip.
        """
        if not self._mirror or not self._mode_path.exists():
            return False
        try:
            raw = json.loads(self._mode_path.read_text()).get('mode')
        except (json.JSONDecodeError, ValueError, OSError):
            return False
        if raw not in ('test', 'live'):
            return False
        return opposite_mode(raw) != self.current_mode
```

- [ ] **Step 4: Implement one source of truth in `main.py`**

Replace the `mode_manager` construction at `main.py:153-158` with:

```python
    _base_settings = load_settings()
    mode_manager = ModeManager(
        notifier=notifier,
        mirror=_base_settings.virtual_only,
    )
    current_mode = _resolve_mode(_base_settings, mode_manager)
```

and add near the other module-level helpers:

```python
def _resolve_mode(base_settings: Settings, mode_manager: "ModeManager") -> str:
    """Return the one mode this process runs, and make the feed agree with it.

    current_mode names every data file; Settings.trading_mode picks the REST and
    WebSocket endpoints. They came from two independent sources — bot_mode.json and the
    TRADING_MODE env var — so a disagreement meant writing '_live' files while reading
    testnet prices. Stamping the resolved mode back onto Settings makes the market the
    bot reads and the market its filenames claim the same thing by construction.
    """
    resolved = mode_manager.current_mode
    if base_settings.trading_mode != resolved:
        logger.warning(
            f"Mode source disagreement: TRADING_MODE={base_settings.trading_mode!r}, "
            f"resolved={resolved!r} (mirror={base_settings.virtual_only}). "
            f"Using {resolved!r} for both filenames and endpoints."
        )
    object.__setattr__(base_settings, 'trading_mode', resolved)
    return resolved
```

Then, where per-symbol settings are built (`main.py:169`), stamp each one too, so
`DataFeed(first_settings, ...)` at line 320 cannot disagree:

```python
        s = load_settings(symbol)
        object.__setattr__(s, 'trading_mode', current_mode)
```

Note: `object.__setattr__` because `Settings` is a frozen dataclass. Verify with
`grep -n "frozen" config/settings.py` before writing this; if it is not frozen, use plain
assignment.

- [ ] **Step 5: Add the mirror watcher**

Alongside the other background tasks (near `main.py:1573`):

```python
    _mirror_task = None
    if _virtual_only:
        _mirror_task = asyncio.create_task(_mirror_watch(mode_manager))
```

and the coroutine:

```python
async def _mirror_watch(mode_manager) -> None:
    """Exit when the primary's mode changes, so the container restarts as its opposite.

    Restarting beats switching in place: on_switch_mode() closes orders, refetches
    balance and rebuilds every mode-scoped object, and a partial failure would leave
    this instance writing to a mix of both suffixes. A fresh process has no such state.

    Requires two consecutive confirmations 30s apart. bot_mode.json is written
    atomically by both writers, so a torn read is not possible — but the dashboard
    re-runs backtests right after writing it, and exiting mid-backtest for a value that
    is about to be corrected again would be a restart loop.
    """
    confirmations = 0
    while True:
        await asyncio.sleep(30.0)
        if mode_manager.mirror_target_changed():
            confirmations += 1
            if confirmations < 2:
                logger.info("Mirror: primary mode change seen, confirming in 30s")
                continue
            logger.warning(
                f"Mirror: primary mode changed — this instance ran "
                f"{mode_manager.current_mode!r}. Exiting so the container restarts "
                f"as the new opposite."
            )
            os._exit(0)
        else:
            confirmations = 0
```

`os._exit(0)` rather than `sys.exit` or raising: this runs in a background task where an
exception would be swallowed by the task, and there is nothing to flush — the mirror holds
no positions and no credentials. Add `import os` if absent. Add `_mirror_task` to the
shutdown filter built in Task 2b (`_tasks = [t for t in (...) if t is not None]`).

- [ ] **Step 6: Make the mode dialog truthful**

`TradingMode.tsx:57-65` promises order closing and immediate live trading; the route only
writes a file. Replace the two `botRunning` branches with one honest message:

```tsx
    const msg = botRunning
      ? `Save ${target.toUpperCase()} as the bot mode?

`
        + `The running bot is NOT switched by this — it keeps trading in `
        + `${mode.toUpperCase()} with its open positions until it is restarted.

`
        + `Backtests for all ${symbolCount} symbols will re-run now to load `
        + `${target}-mode klines.`
      : `Switch to ${target.toUpperCase()} mode?

The bot is not running — the mode `
        + `preference will be saved and used on next start.

Backtests for all `
        + `${symbolCount} symbols will re-run automatically to load ${target}-mode kline data.`
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5`
Expected: all pass, count = previous + 8. Any failure naming `forced_mode` is a
`tests/test_mode_manager_forced_mode.py` call site — update it to `mirror=`, keeping the
behavioural assertions.

- [ ] **Step 8: Typecheck the dashboard**

Run: `cd dashboard && npx tsc --noEmit`

- [ ] **Step 9: Commit**

```bash
git add bot/mode_manager.py main.py tests/test_mirror_mode.py \
        tests/test_mode_manager_forced_mode.py \
        dashboard/components/settings/TradingMode.tsx
git commit -m "feat(mirror): secondary runs the opposite mode; one source of truth for mode"
```

---

### Task 3: No Telegram from the virtual instance

`Notifier` already skips sending when the token is empty (`notifier.py:105`), so this
needs no new sending logic.

**Files:**
- Modify: `main.py` (Notifier construction)
- Test: `tests/test_virtual_only_no_telegram.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_virtual_only_no_telegram.py
"""A statistics-only instance sends no Telegram at all.

It has no trades to report, and duplicate ban alerts from two bots are worse than
none. Notifier guards sending on `if self._token and self._chat_id`, so an empty
token disables sending while local logging continues.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def _token_arg() -> str:
    m = re.search(r'telegram_token\s*=([^\n]+)', MAIN.read_text())
    assert m, 'telegram_token argument not found'
    return m.group(1)


def test_token_is_blanked_for_virtual_only():
    assert 'virtual_only' in _token_arg(), \
        'virtual_only must blank the token so no message is ever sent'


def test_the_real_token_still_reaches_the_trading_bot():
    """The guard must be conditional, not a blanket disable."""
    assert 'token' in _token_arg().replace('telegram_token', '')
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_virtual_only_no_telegram.py -q`
Expected: FAIL — the token is passed unconditionally.

- [ ] **Step 3: Implement**

`first_settings` is defined after the Notifier is built, so use `_base_settings` from
Task 1a (already loaded above the ModeManager):

```python
        # A statistics-only instance sends nothing: no trades to report, and two bots
        # alerting on one ban is worse than one. Notifier skips sending when the token
        # is empty; local logging is unaffected.
        telegram_token='' if _base_settings.virtual_only else _tg.get('token', ''),
```

Move the `_base_settings = load_settings()` line above the Notifier construction if it
is not already there.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `python3 -m pytest tests/test_virtual_only_no_telegram.py -q` → 2 passed
Run: `python3 -m pytest tests/ -q` → 469 passed

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_virtual_only_no_telegram.py
git commit -m "feat(notifier): no Telegram from a virtual-only instance"
```

---

### Task 4: Give the mirror its own per-instance files

Seven files carry per-instance data and would interleave. **Amended 2026-09-07:** the
suffix is keyed on **which instance** is writing (`virtual_only`), not on the mode. A
mode-keyed rule breaks the moment the primary runs live: the mirror would then be the
test-mode process and would claim `dashboard/public/risk_state.json` — which it writes
with balance 0, blanking the trading bot's risk page.

**The rule:** the primary always writes the historical unsuffixed name, in either mode.
The mirror always writes `<stem>_{its mode}.<ext>`.

Verified before choosing this: `results_{symbol}_{mode}.json` is read by **nothing** —
`telegram_menu.py:257`, `telegram_menu.py:324`, `exporter.py:96`, `page.tsx:62`,
`trades/page.tsx:204` and `api/symbols/[symbol]/route.ts:24` all read the unsuffixed name.
So the primary needs to write only that one name, exactly as today.

**Files:**
- Create: `bot/instance_paths.py` — holds `instance_path()`, because Task 4c needs it in
  `backtest.py`, `bot/risk_manager.py` and `bot/symbol_discovery.py`, none of which can
  import `main`. `main._instance_path` is a re-export.
- Modify: `main.py` (bot.log + trades.log handlers, analysis.jsonl, system_log, alert_state, RiskManager `state_path`)
- Modify: `bot/exporter.py:96`
- Modify: `bot/mode_manager.py` — expose `read_mode_file()` for use before `ModeManager` exists
- Test: `tests/test_per_instance_paths.py`

**Interfaces:**
- Consumes: `opposite_mode()` (Task 1c), `Settings.virtual_only` (Task 1)
- Produces: `_instance_path(base: Path, name: str, mode: str, mirror: bool) -> Path` in
  `main.py`; `_results_path(symbol: str, mode: str, mirror: bool) -> Path` in
  `bot/exporter.py`; `read_mode_file(path: Path) -> str` in `bot/mode_manager.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_per_instance_paths.py
"""Per-instance files must not interleave between the two bots.

The suffix is keyed on which instance writes, not on the mode: the primary owns the
unsuffixed name in either mode, so every existing reader is untouched whichever mode
the bot is switched to.
"""
from pathlib import Path

from bot.exporter import _results_path
from main import _instance_path

BASE = Path('/x')
NAMES = ('risk_state.json', 'alert_state.json', 'system_log.json',
         'analysis.jsonl', 'bot.log', 'trades.log')


def test_primary_keeps_the_historical_name_in_test_mode():
    for n in NAMES:
        assert _instance_path(BASE, n, 'test', mirror=False) == BASE / n, n


def test_primary_keeps_the_historical_name_in_live_mode():
    """The regression the amendment exists to prevent: going live must not hand the
    mirror the file the dashboard reads."""
    for n in NAMES:
        assert _instance_path(BASE, n, 'live', mirror=False) == BASE / n, n


def test_mirror_is_always_suffixed():
    assert _instance_path(BASE, 'risk_state.json', 'live', mirror=True) == BASE / 'risk_state_live.json'
    assert _instance_path(BASE, 'risk_state.json', 'test', mirror=True) == BASE / 'risk_state_test.json'
    assert _instance_path(BASE, 'analysis.jsonl', 'live', mirror=True) == BASE / 'analysis_live.jsonl'
    assert _instance_path(BASE, 'bot.log', 'test', mirror=True) == BASE / 'bot_test.log'


def test_mirror_suffix_tracks_its_own_market():
    """After a flip the mirror must not append live-market data to a test-market file."""
    a = _instance_path(BASE, 'analysis.jsonl', 'live', mirror=True)
    b = _instance_path(BASE, 'analysis.jsonl', 'test', mirror=True)
    assert a != b


def test_extensionless_name_still_gets_a_suffix():
    assert _instance_path(BASE, 'notes', 'live', mirror=True) == BASE / 'notes_live'


def test_results_primary_writes_only_the_name_everything_reads():
    assert _results_path('INJUSDT', 'test', mirror=False) == Path('dashboard/public/results_INJUSDT.json')
    assert _results_path('INJUSDT', 'live', mirror=False) == Path('dashboard/public/results_INJUSDT.json')


def test_results_mirror_never_writes_the_unsuffixed_name():
    for mode in ('test', 'live'):
        got = _results_path('INJUSDT', mode, mirror=True)
        assert got == Path(f'dashboard/public/results_INJUSDT_{mode}.json')
        assert got != Path('dashboard/public/results_INJUSDT.json')
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_per_instance_paths.py -q`
Expected: FAIL — `ImportError: cannot import name '_instance_path'`

- [ ] **Step 3: Implement the helper**

In `main.py`, beside the other path constants:

```python
def _instance_path(base: Path, name: str, mode: str, mirror: bool) -> Path:
    """Per-instance file path.

    Keyed on which instance is writing, not on the mode. The primary keeps the
    historical unsuffixed name in either mode, so the dashboard, logrotate and our own
    diagnostic commands are untouched by a mode switch. The mirror always gets its own
    file, suffixed with the market it is actually reading — so a flip starts a new file
    rather than appending a different market to the old one.

    Without this the mirror (balance 0) would overwrite the trading bot's
    risk_state.json with zeros.
    """
    if not mirror:
        return base / name
    stem, dot, ext = name.rpartition('.')
    if not dot:                       # no extension: 'notes' -> 'notes_live'
        return base / f'{name}_{mode}'
    return base / f'{stem}_{mode}.{ext}'
```

Apply it to `analysis.jsonl`, `system_log.json`, `alert_state.json` and `logs/trades.log`
(`main.py:101` — two `RotatingFileHandler`s on one file collide during rollover even
though the mirror logs no trades), and pass a `state_path` into `RiskManager` for
`risk_state.json`.

`bot.log` is set up before `current_mode` exists, so resolve it from the environment and
the mode file directly. Add to `bot/mode_manager.py`:

```python
def read_mode_file(path: Path = _DEFAULT_MODE_PATH) -> str:
    """The primary's mode as recorded on disk, defaulting to 'test'.

    Module-level so logging setup can resolve a filename before ModeManager exists.
    """
    try:
        m = json.loads(path.read_text()).get('mode')
        return m if m in ('test', 'live') else 'test'
    except (json.JSONDecodeError, ValueError, OSError):
        return 'test'
```

and in `main.py`'s logging setup:

```python
    # Runs before Settings/ModeManager exist, so read the two inputs directly. The
    # mirror's log name must track the market it reads, which is the opposite of the
    # primary's recorded mode — not the TRADING_MODE env var, which it ignores.
    _log_mirror = os.getenv('VIRTUAL_ONLY', 'false').lower() in ('1', 'true', 'yes')
    _log_mode = opposite_mode(read_mode_file()) if _log_mirror else 'test'
    general = logging.handlers.RotatingFileHandler(
        str(_instance_path(Path('logs'), 'bot.log', _log_mode, _log_mirror)),
        maxBytes=10 * 1024 * 1024, backupCount=5
    )
```

In `bot/exporter.py`, above `export()`:

```python
def _results_path(symbol: str, mode: str, mirror: bool) -> Path:
    """Where this symbol's chart data goes.

    The primary writes the unsuffixed name in either mode — it is the only name any
    reader looks for (telegram_menu.py:257/324, page.tsx:62, trades/page.tsx:204,
    api/symbols/[symbol]/route.ts:24). The mirror writes its own suffixed file, which
    the Trades-page data toggle fetches explicitly.
    """
    if mirror:
        return Path(f'dashboard/public/results_{symbol}_{mode}.json')
    return Path(f'dashboard/public/results_{symbol}.json')
```

`export()` needs `mirror` threaded in from `Settings.virtual_only` at its call site;
`mode` is already a parameter it currently ignores.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/python -m pytest tests/test_per_instance_paths.py -q`
Expected: 7 passed

- [ ] **Step 5: Confirm nothing changed for the primary, in either mode**

```bash
.venv/bin/python -c "
from pathlib import Path
from main import _instance_path
from bot.exporter import _results_path
for mode in ('test','live'):
    for n in ('risk_state.json','alert_state.json','system_log.json','analysis.jsonl','bot.log','trades.log'):
        assert _instance_path(Path('x'), n, mode, mirror=False) == Path('x')/n, (n, mode)
    assert _results_path('INJUSDT', mode, mirror=False) == Path('dashboard/public/results_INJUSDT.json')
print('every primary path is unchanged in both modes')
"
```

- [ ] **Step 6: Check the symbol-template glob still picks a sane file**

`api/symbols/route.ts:47` seeds a new symbol by copying any existing `results_*.json`.
That glob now also matches the mirror's `results_X_live.json`. Harmless — it is only a
render placeholder that the next candle overwrites — but confirm it is not treated as
authoritative:

```bash
grep -n "startsWith('results_')" -A4 dashboard/app/api/symbols/route.ts
```

- [ ] **Step 7: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests/ -q   # expect previous + 7
git add main.py bot/exporter.py bot/mode_manager.py tests/test_per_instance_paths.py
git commit -m "feat(paths): give the mirror instance its own per-instance files

Keyed on which instance writes, not on the mode: the primary keeps the unsuffixed
name in either mode, so switching to live cannot hand the mirror the trading bot's
risk_state.json and blank it with a zero balance."
```

---

### Task 4c: `backtest_results_{symbol}.json` must be per-instance

**Found 2026-09-07 while implementing Task 1c. This is the most damaging collision in the
design and the original spec's path table missed it entirely.**

The chain, every line verified:

1. The mirror runs the obligatory startup backtest — `main.py:378` is **not** gated by
   `virtual_only`, and it passes `--mode current_mode`, so the mirror backtests the live
   market. It runs again on `main.py:1583`.
2. `backtest.py:107` writes `dashboard/public/backtest_results_{symbol}.json` —
   **unsuffixed**. The mirror therefore overwrites the primary's testnet backtest with
   live-market results.
3. `bot/risk_manager.py:447` reads that exact path in `_compute_perf_score()` and returns
   `(intra_score, best_pf, raw_profit_pct)`.
4. `intra_score` sets **leverage** (`risk_manager.py:417`). `raw_profit_pct` is, per its
   own docstring, the **cross-symbol allocation weight** — "a symbol with +22 % profit
   gets proportionally more capital than one with +6 %".

So the mirror would silently reset the trading bot's leverage and capital allocation from
a different market's backtest, on real orders, at every startup. `_PERF_CACHE_TTL` delays
it; it does not prevent it.

Other readers of the same unsuffixed name: `bot/symbol_discovery.py:104` and `:125`,
`bot/telegram_menu.py:425` (mirror does not run the menu — no change needed),
`dashboard/app/backtest/page.tsx` and `dashboard/app/api/trades/route.ts:41` (both read
the primary's, which is correct).

**Files:**
- Modify: `backtest.py:107`
- Modify: `bot/risk_manager.py:53-59,447`
- Modify: `bot/symbol_discovery.py:104,125`
- Modify: `main.py:389,1583` (the two `seed_from_backtest` call sites)
- Test: `tests/test_backtest_results_per_instance.py`

**Interfaces:**
- Consumes: `instance_path()` from `bot/instance_paths.py` (Task 4)
- Produces: `RiskManager(..., mirror: bool = False, mode: str = 'test')`;
  `backtest_results_name(symbol, mode, mirror) -> str` in `bot/instance_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_results_per_instance.py
"""The mirror must never overwrite the primary's backtest results.

risk_manager._compute_perf_score() reads backtest_results_{symbol}.json and derives
leverage and cross-symbol capital allocation from it. A mirror running the other market
would rewrite that file at startup and change what the trading bot risks on real orders.
"""
from pathlib import Path

from bot.instance_paths import backtest_results_name
from bot.risk_manager import RiskManager


def test_primary_uses_the_historical_name_in_both_modes():
    for mode in ('test', 'live'):
        assert backtest_results_name('INJUSDT', mode, mirror=False) == \
            'backtest_results_INJUSDT.json'


def test_mirror_is_suffixed_with_its_own_market():
    assert backtest_results_name('INJUSDT', 'live', mirror=True) == \
        'backtest_results_INJUSDT_live.json'
    assert backtest_results_name('INJUSDT', 'test', mirror=True) == \
        'backtest_results_INJUSDT_test.json'


def test_risk_manager_reads_the_primary_file_by_default(tmp_path):
    """Default construction must be byte-identical to today."""
    rm = RiskManager(mode='test', backtest_results_dir=tmp_path)
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT.json'


def test_risk_manager_in_a_mirror_reads_its_own_file(tmp_path):
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path, mirror=True)
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT_live.json'


def test_a_live_primary_still_reads_the_unsuffixed_file(tmp_path):
    """The regression to avoid: going live must not make the trading bot read the
    mirror's file."""
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path)
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT.json'


def test_backtest_writes_the_mirror_name_when_virtual_only(monkeypatch):
    import importlib
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'live')
    import backtest
    importlib.reload(backtest)
    assert backtest._dashboard_path('INJUSDT').name == 'backtest_results_INJUSDT_live.json'


def test_backtest_writes_the_primary_name_otherwise(monkeypatch):
    import importlib
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    monkeypatch.setenv('TRADING_MODE', 'test')
    import backtest
    importlib.reload(backtest)
    assert backtest._dashboard_path('INJUSDT').name == 'backtest_results_INJUSDT.json'
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_backtest_results_per_instance.py -q`
Expected: FAIL — `ImportError: cannot import name 'backtest_results_name'`

- [ ] **Step 3: Add the name helper to `bot/instance_paths.py`**

```python
def backtest_results_name(symbol: str, mode: str, mirror: bool) -> str:
    """Filename for a symbol's backtest results.

    The primary keeps the historical name in either mode — risk_manager derives
    leverage and cross-symbol capital allocation from it, and the dashboard's backtest
    page reads it. The mirror gets its own file suffixed with the market it backtested,
    so it can never rewrite the numbers the trading bot sizes real orders from.
    """
    return instance_path(Path('.'), f'backtest_results_{symbol}.json', mode, mirror).name
```

- [ ] **Step 4: Route the writer**

In `backtest.py`, replace line 107's inline path:

```python
def _dashboard_path(symbol: str) -> Path:
    """Where this run's dashboard results go.

    Reads the environment rather than taking a parameter because main.py invokes this
    as a subprocess and the container's VIRTUAL_ONLY comes along for free. The
    dashboard's own /api/run-backtest runs without VIRTUAL_ONLY, so a user-triggered
    backtest correctly writes the primary's file.
    """
    mirror = os.getenv('VIRTUAL_ONLY', 'false').lower() in ('1', 'true', 'yes')
    mode = 'test' if os.getenv('TRADING_MODE', 'test') in ('test', 'testnet') else 'live'
    return Path('dashboard') / 'public' / backtest_results_name(symbol, mode, mirror)
```

`--mode` already sets `os.environ['TRADING_MODE']` at `backtest.py:201`, before settings
load, so this reads the effective mode and not a stale env value. Verify that ordering
holds after editing.

- [ ] **Step 5: Route the readers**

`bot/risk_manager.py` — accept the flags and add one accessor, so there is a single
place to test:

```python
        mirror: bool = False,
```
```python
        self._mirror = mirror

    def _backtest_path(self, symbol: str) -> Path:
        return self._results_dir / backtest_results_name(symbol, self._mode, self._mirror)
```

Replace the inline path at `risk_manager.py:447` with `self._backtest_path(symbol)`.
Check what `self._mode` is actually called in that class before writing this — it may be
`self.mode`.

`bot/symbol_discovery.py:104,125` — same substitution. It already computes a mode suffix
at line 154 for a different purpose; do not reuse that variable, it is not mirror-aware.

`main.py:389` and `main.py:1583` — the two `seed_from_backtest` call sites:

```python
        bt_path = _PROJECT_ROOT / "dashboard" / "public" / backtest_results_name(
            sym, current_mode, _virtual_only)
```

At line 1583 use `mode_manager.current_mode` and `_virtual_only`.

Pass `mirror=_base_settings.virtual_only` where `RiskManager` is constructed
(`main.py:~190`) and wherever `SymbolDiscovery` is constructed.

- [ ] **Step 6: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_backtest_results_per_instance.py -q` → 7 passed
Run: `.venv/bin/python -m pytest tests/ -q` → previous + 7

- [ ] **Step 7: Prove the primary's numbers are untouched**

The point of the task is that real-order sizing does not change. Confirm the default
construction resolves to the same file it always did:

```bash
.venv/bin/python -c "
from pathlib import Path
from bot.risk_manager import RiskManager
for mode in ('test','live'):
    rm = RiskManager(mode=mode, backtest_results_dir=Path('dashboard/public'))
    p = rm._backtest_path('INJUSDT')
    assert p == Path('dashboard/public/backtest_results_INJUSDT.json'), (mode, p)
print('primary leverage/allocation inputs unchanged in both modes')
"
```

- [ ] **Step 8: Commit**

```bash
git add backtest.py bot/risk_manager.py bot/symbol_discovery.py bot/instance_paths.py \
        main.py tests/test_backtest_results_per_instance.py
git commit -m "fix(mirror): backtest_results must be per-instance

The mirror's startup backtest wrote the unsuffixed backtest_results_{symbol}.json,
which risk_manager reads to derive leverage and cross-symbol capital allocation.
A live-market backtest would silently resize the testnet bot's real orders."
```

---

### Task 4b: Make the registry write atomic and survive a read-only mount

`SymbolRegistry._persist()` uses `write_text()` directly (`symbol_registry.py:235`),
unlike `risk_config._atomic_write()` which does tmp+replace. With two processes reading
the file, a reader can catch it mid-write and get truncated JSON. `_load()` then falls
through its `except` to `_persist()` — a **write** — which on the `:ro` mount raises
`PermissionError` and kills the instance at startup.

So the `:ro` safety measure turns a transient read glitch into a hard crash. Fixing the
write atomically removes the trigger; tolerating a failed persist removes the crash.
Both improve the existing bot too.

**Files:**
- Modify: `bot/symbol_registry.py:235` (`_persist`)
- Test: `tests/test_symbol_registry_atomic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_symbol_registry_atomic.py
"""The registry must be written atomically and must not die on a read-only mount.

Two processes now read this file. A non-atomic write lets a reader see truncated
JSON; _load() then falls through to _persist(), which on a read-only mount raises
and takes the instance down at startup.
"""
import json
from pathlib import Path

import pytest

from bot.symbol_registry import SymbolRegistry


def test_persist_is_atomic(tmp_path, monkeypatch):
    """A reader must never observe a partially written file."""
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], path=path)
    seen = {}

    real_replace = Path.replace

    def spy(self, target):
        # At the moment of replace the destination is either absent or complete —
        # never half-written.
        if target.exists():
            seen['valid_before_swap'] = json.loads(target.read_text()) is not None
        return real_replace(self, target)

    monkeypatch.setattr(Path, 'replace', spy)
    r.pause_symbol('INJUSDT')
    assert json.loads(path.read_text())['paused']


def test_read_only_mount_does_not_crash(tmp_path):
    """A failed persist must be logged, not fatal."""
    path = tmp_path / 'symbol_registry.json'
    path.write_text('{ this is not valid json')
    path.chmod(0o444)
    try:
        r = SymbolRegistry(seed_symbols=['INJUSDT'], path=path)
        assert r.get_symbols() == ['INJUSDT'], 'must fall back to the seed, not die'
    finally:
        path.chmod(0o644)


def test_corrupt_file_falls_back_to_seed(tmp_path):
    path = tmp_path / 'symbol_registry.json'
    path.write_text('{ truncated')
    r = SymbolRegistry(seed_symbols=['INJUSDT', 'TIAUSDT'], path=path)
    assert r.get_symbols() == ['INJUSDT', 'TIAUSDT']
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_symbol_registry_atomic.py -q`
Expected: FAIL — `test_read_only_mount_does_not_crash` raises `PermissionError`. Check
`SymbolRegistry.__init__`'s real parameter name for the path first and match the test to it.

- [ ] **Step 3: Implement**

```python
    def _persist(self) -> None:
        data = {
            'symbols': self._symbols,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'status': self._status,
            'weights': self._weights,
            'disabled': self._disabled,
            'disabled_ranks': self._disabled_ranks,
            'paused': self._paused,
            'leverage_overrides': self._leverage_overrides,
        }
        # Atomic: a second process now reads this file, and a direct write_text lets a
        # reader observe truncated JSON. Matches config/risk_config.py::_atomic_write.
        try:
            tmp = self._path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self._path)
        except OSError as exc:
            # A read-only mount is a deliberate configuration for the virtual-only
            # instance, not a fault. Losing an in-memory change is acceptable there;
            # crashing at startup is not.
            logger.warning(f"SymbolRegistry: cannot persist to {self._path}: {exc}")
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_symbol_registry_atomic.py -q` → 3 passed
Run: `python3 -m pytest tests/ -q` → 477 passed

- [ ] **Step 5: Commit**

```bash
git add bot/symbol_registry.py tests/test_symbol_registry_atomic.py
git commit -m "fix(registry): atomic persist, and survive a read-only mount

Two processes now read symbol_registry.json. write_text() is not atomic, so a
reader could see truncated JSON; _load() then falls through to _persist(), which
on the virtual instance's read-only mount would raise and kill it at startup.
Also fixes a latent race for the existing bot."
```

---

### Task 5: Trades-page instance toggle

**Amended 2026-09-07.** This is a **data-view** control, not a mode control: it changes
which instance's files the page reads and takes effect **instantly**, client-side. It
never writes `bot_mode.json` or `bot_command.json`, so it cannot alter what the bot
trades. The bot-mode control stays where it is, in Settings, and still takes effect only
on restart.

Placed on the Trades page — the mirror produces trade and preset data and nothing else,
so no other page has anything to show for it.

Labels are "Primary" and "Shadow" rather than "Test" and "Live", because which market
each one reads depends on the bot mode. The shadow's market is shown next to the label,
derived from `/api/mode` as its opposite, so the reader always knows what they are
looking at.

**Files:**
- Create: `dashboard/components/InstanceToggle.tsx`
- Modify: `dashboard/app/trades/page.tsx:204` (the `results_${symbol}.json` fetch)
- Modify: `dashboard/app/api/trades/route.ts` (accept `?instance=`)

- [ ] **Step 1: Create the component**

```tsx
// dashboard/components/InstanceToggle.tsx
'use client'

/** Chooses which instance's data this page shows. Pure data view — it never changes
 *  what the bot trades.
 *
 *  'primary' is the bot that places real orders, in whatever mode it was started in.
 *  'shadow' is the virtual-only mirror running the opposite market: no credentials,
 *  structurally unable to trade. Defaults to 'primary' so the page behaves as before.
 */
export type Instance = 'primary' | 'shadow'

export default function InstanceToggle({
  value, onChange, botMode,
}: {
  value: Instance
  onChange: (i: Instance) => void
  botMode: 'test' | 'live'
}) {
  const shadowMode = botMode === 'live' ? 'test' : 'live'
  const label: Record<Instance, string> = {
    primary: `Primary (${botMode})`,
    shadow: `Shadow (${shadowMode}, virtual)`,
  }
  const title: Record<Instance, string> = {
    primary: `The trading bot — places real orders on the ${botMode} market`,
    shadow: `Virtual-only mirror on the ${shadowMode} market — no credentials, never trades`,
  }
  return (
    <div className="inline-flex rounded border border-gray-700 overflow-hidden text-xs">
      {(['primary', 'shadow'] as Instance[]).map(i => (
        <button
          key={i}
          onClick={() => onChange(i)}
          title={title[i]}
          className={`px-3 py-1 transition-colors ${
            value === i ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
          }`}
        >
          {label[i]}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Wire it into the Trades page**

Derive during render — do not seed state in an effect, which trips
`react-hooks/set-state-in-effect` (the pattern already used for the date range in this
file):

```tsx
const [instance, setInstance] = useState<Instance>('primary')
const [botMode, setBotMode] = useState<'test' | 'live'>('test')

useEffect(() => {
  fetch('/api/mode').then(r => r.json())
    .then(d => setBotMode(d.mode === 'live' ? 'live' : 'test'))
    .catch(() => {})
}, [])

// Which file this page reads. The shadow's suffix is its own market — the opposite of
// the bot mode — matching _results_path() in bot/exporter.py.
const shadowMode = botMode === 'live' ? 'test' : 'live'
const resultsFile = instance === 'primary'
  ? `results_${symbol}.json`
  : `results_${symbol}_${shadowMode}.json`
```

Change the fetch at line 204 to use `resultsFile`, add `resultsFile` to that effect's
dependency array, and render the toggle in the header beside the date pickers.

Persist the choice, tolerating a private window:

```tsx
function persistInstance(i: Instance) {
  setInstance(i)
  try { localStorage.setItem('bfb-instance', i) } catch { /* private window */ }
}
```

- [ ] **Step 3: Handle the empty shadow gracefully**

Until the mirror has run a candle, its file does not exist and the fetch 404s. Show that
as a state, not an error — otherwise the first click looks like a bug:

```tsx
{instance === 'shadow' && error && (
  <div className="text-xs text-gray-500">
    No shadow data yet — the mirror instance writes its first file after one candle close.
  </div>
)}
```

- [ ] **Step 4: Typecheck and build**

```bash
cd dashboard && npx tsc --noEmit && npm run build
```

- [ ] **Step 5: Verify the default is unchanged and nothing writes bot state**

```bash
cd dashboard && grep -n "useState<Instance>" app/trades/page.tsx
grep -rn "bot_command\|target_mode" components/InstanceToggle.tsx app/trades/page.tsx || echo "clean: view-only toggle"
```

Expected: `useState<Instance>('primary')`, and `clean: view-only toggle`.

- [ ] **Step 6: Commit**

```bash
git add dashboard/components/InstanceToggle.tsx dashboard/app/trades/page.tsx \
        dashboard/app/api/trades/route.ts
git commit -m "feat(dashboard): Trades-page primary/shadow data toggle, defaulting to primary"
```

---

### Task 6: Compose service and first run

**Amended 2026-09-07.** The service is named `bot_mirror`, not `bot_live`: its mode is no
longer fixed, and a name that claims otherwise would be wrong half the time.

**Files:**
- Modify: `docker-compose.yml`, `FEATURES.md`

- [ ] **Step 1: Add the service**

```yaml
  # The mirror. Runs whichever mode the primary is not running, with virtual orders only.
  #
  # Deliberately has NO credentials: python-binance refuses private endpoints without a
  # secret, so this container cannot place an order even if asked. TRADING_MODE is
  # intentionally absent — a mirror derives its mode as the opposite of bot_mode.json
  # (see _resolve_mode in main.py), and an env value here would only be misleading.
  #
  # It writes data/*_{its mode}.json, exactly where a same-mode main bot reads, so going
  # live later means deleting VIRTUAL_ONLY and adding keys with the statistics already
  # gathered.
  #
  # restart: unless-stopped is load-bearing, not just resilience: on a bot-mode change
  # the mirror exits 0 and this policy brings it back up as the new opposite.
  bot_mirror:
    build: .
    container_name: bot_mirror
    environment:
      VIRTUAL_ONLY: "1"
      BINANCE_API_KEY: ""
      BINANCE_API_SECRET: ""
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./dashboard/public:/app/dashboard/public
      # Read-only: shared config is input only. Task 2 skips the two writers;
      # :ro is the backstop if one is ever missed.
      - ./risk_config.json:/app/risk_config.json:ro
      - ./symbol_registry.json:/app/symbol_registry.json:ro
    command: >
      sh -c 'cd /app && exec .venv/bin/python3 main.py >> /app/logs/bot_mirror_stdout.log 2>&1'
    restart: unless-stopped
    stop_grace_period: 60s
```

`environment:` rather than `env_file: .env` is what keeps credentials out.

The stdout redirect goes to a fixed `bot_mirror_stdout.log` because compose cannot know
the resolved mode. It catches only crashes before logging initialises; Python's own
handler writes `logs/bot_{mode}.log`. Both need rotation (Step 2b).

- [ ] **Step 2: Verify no credentials leak in and the restart policy is right**

```bash
.venv/bin/python -c "
import yaml
c = yaml.safe_load(open('docker-compose.yml'))['services']['bot_mirror']
assert 'env_file' not in c, 'env_file would inject the real keys'
assert c['environment']['BINANCE_API_KEY'] == ''
assert c['environment']['BINANCE_API_SECRET'] == ''
assert c['environment']['VIRTUAL_ONLY'] == '1'
assert 'TRADING_MODE' not in c['environment'], 'the mirror derives its mode, not env'
assert c['restart'] == 'unless-stopped', 'the mirror restart depends on this policy'
assert sum(1 for v in c['volumes'] if v.endswith(':ro')) == 2, 'config must be read-only'
print('bot_mirror: no credentials, config read-only, restart policy correct')
"
```

- [ ] **Step 2b: Extend logrotate — with globs, not fixed paths**

logrotate lists **explicit paths** (`/opt/bot/logs/bot.log /opt/bot/logs/trades.log`), so
new files would never rotate. The mirror's log name changes when the mode flips, so
enumerate with globs rather than naming each one. The compose stdout redirect appends
outside Python's `RotatingFileHandler` cap and grows without limit — and 5 GB of free
disk is what both bots live on.

Find the real filename first:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 "ls /etc/logrotate.d/"
```

Then widen it to globs covering `bot.log`, `bot_test.log`, `bot_live.log`,
`bot_mirror_stdout.log`, `trades.log`, `trades_test.log`, `trades_live.log`:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cp /etc/logrotate.d/bot /etc/logrotate.d/bot.bak && \
   sed -i '1s#.*#/opt/bot/logs/bot*.log /opt/bot/logs/trades*.log {#' /etc/logrotate.d/bot && \
   head -1 /etc/logrotate.d/bot && logrotate -d /etc/logrotate.d/bot 2>&1 | grep -c 'considering log'"
```

Verify the first line is the glob form and the dry run considers every existing log. Keep
`.bak` until the next rotation has been observed.

- [ ] **Step 3: Update FEATURES.md**

Document the mirror, the keyless guarantee, the shared-state taxonomy, the two separate
controls (bot mode = restart, data view = instant), the corrected suffix rule, and the
handover: remove `VIRTUAL_ONLY`, add credentials, and it becomes the live bot.

- [ ] **Step 4: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests/ -q
git add docker-compose.yml FEATURES.md
git commit -m "feat(deploy): bot_mirror — opposite mode, virtual only, no keys"
```

- [ ] **Step 5: STOP. Ask for explicit deploy approval.**

Do not deploy. Report what is ready and wait. CLAUDE.md: never deploy without explicit
user confirmation.

- [ ] **Step 6: After approval — deploy and verify both instances**

Follow `/bfb-deploy`, then confirm the two instances resolved **different** modes:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'bot|dashboard'; \
   echo '--- resolved modes ---'; \
   grep -h 'Bot starting' /opt/bot/logs/bot.log /opt/bot/logs/bot_live.log 2>/dev/null | tail -2; \
   echo '--- mirror must hold no keys ---'; \
   docker exec bot_mirror printenv BINANCE_API_KEY | wc -c"
```

Expected: both containers `Up`, the two `Bot starting` lines show **different** modes, and
the key length is 1 (a bare newline).

- [ ] **Step 7: Verify the mirror actually places no real order**

After one full candle, the mirror must have virtual activity and zero real orders:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "grep -ciE 'placing real|order placed|futures_create_order' /opt/bot/logs/bot_live.log; \
   grep -c 'VIRTUAL-ONLY instance' /opt/bot/logs/bot_live.log; \
   ls -la /opt/bot/data/preset_efficiency_live.json"
```

Expected: `0` real-order lines, `1` virtual-only banner, and the live efficiency file
present and growing.

- [ ] **Step 8: Verify the mirror flips on a bot-mode change**

This is the amendment's core behaviour and the one thing no unit test can prove. Do it
**only** while the primary is in test mode and has no open positions, and revert
immediately — flipping `bot_mode.json` also changes what the primary would do on its next
restart.

```bash
ssh ... "python3 -c \"
import json,pathlib
p=pathlib.Path('/opt/bot/data/bot_mode.json')
p.write_text(json.dumps({'mode':'live'}))
print('set live')\""
# wait ~90s (two 30s confirmations plus restart), then:
ssh ... "docker ps --format '{{.Names}}\t{{.Status}}' | grep bot_mirror; \
         grep 'Bot starting' /opt/bot/logs/bot_test.log | tail -1"
# revert:
ssh ... "python3 -c \"
import json,pathlib
pathlib.Path('/opt/bot/data/bot_mode.json').write_text(json.dumps({'mode':'test'}))
print('reverted')\""
```

Expected: `bot_mirror` shows a fresh `Up` (seconds, not hours), and `bot_test.log`
carries a `Bot starting | mode=test` line. After reverting, it flips back to live.
Confirm the **primary** never restarted and never changed mode: its uptime in
`docker ps` must be unbroken.

---

## The handover, later

When you are ready for real money: delete `VIRTUAL_ONLY` and the empty credential lines
from the `bot_live` service, add live keys, and stop the testnet `bot` service. Every
guard lifts at once, the instance starts placing real orders — and it already holds
months of `data/*_live.json` preset statistics gathered on real charts.

No migration, no second instance, nothing to copy. That is the whole point of keeping the
files in their normal places.
