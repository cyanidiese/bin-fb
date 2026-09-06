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
- Files keep their normal paths. Suffixes apply **only off test mode**, so existing filenames and every dashboard reader are untouched.
- Baseline: **456 passing** (Tasks 1 and 1a already committed: `ffd965a`, `14db2f7`).
- Never deploy without explicit user confirmation.

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

### Task 4: Mode-suffix the per-instance files

Six files carry per-instance data and would interleave. Suffix **only off test mode**.

**Files:**
- Modify: `main.py` (bot.log handler, analysis.jsonl, system_log, alert_state, RiskManager `state_path`)
- Modify: `bot/exporter.py:96`
- Test: `tests/test_per_instance_paths.py`

**Interfaces:**
- Produces: `_mode_path(base: Path, name: str, mode: str) -> Path` in `main.py`;
  `_results_path(symbol: str, mode: str) -> list[Path]` in `bot/exporter.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_per_instance_paths.py
"""Per-instance files must not interleave between the two bots.

Suffixes apply only off test mode, so today's filenames — and every dashboard reader
expecting them — are untouched.
"""
from pathlib import Path

from bot.exporter import _results_path
from main import _mode_path

BASE = Path('/x')


def test_test_mode_keeps_the_historical_name():
    assert _mode_path(BASE, 'risk_state.json', 'test') == BASE / 'risk_state.json'
    assert _mode_path(BASE, 'analysis.jsonl', 'test') == BASE / 'analysis.jsonl'


def test_live_mode_is_suffixed():
    assert _mode_path(BASE, 'risk_state.json', 'live') == BASE / 'risk_state_live.json'
    assert _mode_path(BASE, 'analysis.jsonl', 'live') == BASE / 'analysis_live.jsonl'


def test_unknown_mode_is_suffixed_too():
    """Fail closed: never write the file the dashboard reads unless mode is test."""
    assert _mode_path(BASE, 'risk_state.json', 'weird') == BASE / 'risk_state_weird.json'


def test_results_test_mode_writes_both_names():
    """The dashboard reads the unsuffixed name today; keep writing it."""
    assert _results_path('INJUSDT', 'test') == [
        Path('dashboard/public/results_INJUSDT_test.json'),
        Path('dashboard/public/results_INJUSDT.json'),
    ]


def test_results_live_mode_never_writes_the_unsuffixed_name():
    assert _results_path('INJUSDT', 'live') == [
        Path('dashboard/public/results_INJUSDT_live.json'),
    ]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_per_instance_paths.py -q`
Expected: FAIL — `ImportError: cannot import name '_mode_path'`

- [ ] **Step 3: Implement the helper**

In `main.py`, beside the other path constants:

```python
def _mode_path(base: Path, name: str, mode: str) -> Path:
    """Per-instance file path.

    Test mode keeps the historical unsuffixed name so every existing reader — the
    dashboard, logrotate, our own diagnostic commands — is untouched. Any other mode
    gets its own file. Without this a virtual-only instance (balance 0) would
    overwrite the trading bot's risk_state.json with zeros.
    """
    if mode == 'test':
        return base / name
    stem, _, ext = name.rpartition('.')
    return base / f'{stem}_{mode}.{ext}'
```

Apply it to `analysis.jsonl`, `system_log.json` and `alert_state.json`, and pass a
`state_path` into `RiskManager` for `risk_state.json`. For `bot.log`, the handler runs
before `current_mode` exists — read the env there:

```python
    _log_mode = os.getenv('TRADING_MODE', 'test')
    _log_mode = 'test' if _log_mode in ('test', 'testnet') else _log_mode
    general = logging.handlers.RotatingFileHandler(
        str(_mode_path(Path('logs'), 'bot.log', _log_mode)),
        maxBytes=10 * 1024 * 1024, backupCount=5
```

In `bot/exporter.py`, above `export()`:

```python
def _results_path(symbol: str, mode: str) -> list[Path]:
    """Where this symbol's chart data goes.

    Test mode also writes the historical unsuffixed name so the dashboard keeps
    working. Any other mode writes only its own file — never the unsuffixed one,
    which belongs to the test bot.
    """
    paths = [Path(f'dashboard/public/results_{symbol}_{mode}.json')]
    if mode == 'test':
        paths.append(Path(f'dashboard/public/results_{symbol}.json'))
    return paths
```

Replace line 96 and its write with a loop over these paths, keeping the existing
try/except per path.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_per_instance_paths.py -q`
Expected: 5 passed

- [ ] **Step 5: Confirm nothing changed for test mode**

```bash
python3 -c "
from pathlib import Path
from main import _mode_path
from bot.exporter import _results_path
for n in ('risk_state.json','alert_state.json','system_log.json','analysis.jsonl','bot.log'):
    assert _mode_path(Path('x'), n, 'test') == Path('x')/n, n
assert Path('dashboard/public/results_INJUSDT.json') in _results_path('INJUSDT','test')
print('every test-mode path is unchanged')
"
```

- [ ] **Step 6: Run the full suite and commit**

```bash
python3 -m pytest tests/ -q   # expect 474
git add main.py bot/exporter.py tests/test_per_instance_paths.py
git commit -m "feat(paths): mode-suffix per-instance files off test mode

risk_state.json matters most: a virtual-only instance has balance 0 and would
otherwise zero the trading bot's risk page. Test-mode paths are unchanged."
```

---

### Task 5: Dashboard test/live toggle

The trades API already accepts `?mode=` (`route.ts:37`) and files stay in `data/`, so no
directory resolution is needed.

**Files:**
- Create: `dashboard/components/ModeToggle.tsx`
- Modify: `dashboard/app/page.tsx:62`
- Modify: `dashboard/app/api/risk/route.ts:7` (accept `?mode=`, default test)

- [ ] **Step 1: Create the component**

```tsx
// dashboard/components/ModeToggle.tsx
'use client'

/** Chooses which instance's data the page shows.
 *
 *  'test' is the testnet bot that places real (testnet) orders. 'live' is the
 *  virtual-only instance on the real market — no credentials, never trades.
 *  Defaults to 'test' so the page behaves as before.
 */
export type DataMode = 'test' | 'live'

export default function ModeToggle(
  { value, onChange }: { value: DataMode; onChange: (m: DataMode) => void },
) {
  return (
    <div className="inline-flex rounded border border-gray-700 overflow-hidden text-xs">
      {(['test', 'live'] as DataMode[]).map(m => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={`px-3 py-1 transition-colors ${
            value === m ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
          }`}
          title={m === 'live'
            ? 'Virtual-only instance on the real market — no credentials, never trades'
            : 'Testnet bot — places real testnet orders'}
        >
          {m === 'test' ? 'Testnet' : 'Live (virtual)'}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Wire it into the Strategy page**

```tsx
const [dataMode, setDataMode] = useState<DataMode>('test')

useEffect(() => {
  try {
    const saved = localStorage.getItem('bfb-data-mode')
    if (saved === 'live' || saved === 'test') setDataMode(saved)
  } catch { /* private window — keep the default */ }
}, [])
```

Change the fetch at line 62 to `results_${symbol}_${dataMode}.json`, add `dataMode` to
that effect's dependency array, render the toggle in the header, and persist on change.

- [ ] **Step 3: Typecheck and build**

```bash
cd dashboard && npx tsc --noEmit && npm run build
```

- [ ] **Step 4: Verify the default is unchanged**

```bash
cd dashboard && grep -n "useState<DataMode>" app/page.tsx
```

Expected: `useState<DataMode>('test')`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/components/ModeToggle.tsx dashboard/app/page.tsx dashboard/app/api/risk/route.ts
git commit -m "feat(dashboard): testnet/live data toggle, defaulting to test"
```

---

### Task 6: Compose service and first run

**Files:**
- Modify: `docker-compose.yml`, `FEATURES.md`

- [ ] **Step 1: Add the service**

```yaml
  # Live market, virtual orders only. Deliberately has NO credentials: python-binance
  # refuses private endpoints without a secret, so this container cannot place an order
  # even if asked. Writes data/*_live.json — exactly where a live-mode main bot reads —
  # so going live later means deleting VIRTUAL_ONLY and adding keys, with the statistics
  # already gathered.
  bot_live:
    build: .
    container_name: bot_live
    environment:
      TRADING_MODE: live
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
      sh -c 'cd /app && exec .venv/bin/python3 main.py >> /app/logs/bot_live.log 2>&1'
    restart: unless-stopped
    stop_grace_period: 60s
```

`environment:` rather than `env_file: .env` is what keeps credentials out.

- [ ] **Step 2: Verify no credentials leak in**

```bash
python3 -c "
import yaml
c = yaml.safe_load(open('docker-compose.yml'))['services']['bot_live']
assert 'env_file' not in c, 'env_file would inject the real keys'
assert c['environment']['BINANCE_API_KEY'] == ''
assert c['environment']['VIRTUAL_ONLY'] == '1'
assert c['environment']['TRADING_MODE'] == 'live'
assert sum(1 for v in c['volumes'] if v.endswith(':ro')) == 2, 'config must be read-only'
print('bot_live: no credentials, config read-only')
"
```

- [ ] **Step 3: Update FEATURES.md**

Document the instance, the keyless guarantee, the shared-state taxonomy, the toggle, and
the handover: remove `VIRTUAL_ONLY`, add credentials, and it becomes the live bot.

- [ ] **Step 4: Run the full suite and commit**

```bash
python3 -m pytest tests/ -q
git add docker-compose.yml FEATURES.md
git commit -m "feat(deploy): bot_live — live market, virtual only, no keys"
```

- [ ] **Step 5: STOP — get explicit deploy approval**

Report what will start, that the testnet bot is unchanged, and wait.

- [ ] **Step 6: Deploy**

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cd /opt/bot && git pull origin feature/mean-reversion-overlay && \
   docker compose up -d --build 2>&1 | tail -10"
```

- [ ] **Step 7: Verify every guarantee in production**

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cd /opt/bot
   echo '--- credentials absent (expect 1) ---';        docker exec bot_live printenv | grep -c 'BINANCE_API_KEY=$'
   echo '--- virtual-only banner ---';                  docker exec bot_live grep -c 'VIRTUAL-ONLY instance' /app/logs/bot_live.log
   echo '--- real orders in live mode (MUST be 0) ---'; ls data/real_orders_*_live.json 2>/dev/null | wc -l
   echo '--- live virtual orders appearing ---';        ls data/virtual_orders_rank2_*_live.json 2>/dev/null | wc -l
   echo '--- PID belongs to the trading bot ---';       cat data/bot_pid.json
   echo '--- shared config untouched ---';              md5sum risk_config.json symbol_registry.json
   echo '--- risk_state not zeroed ---';                head -c 200 dashboard/public/risk_state.json
   echo '--- testnet bot healthy ---';                  docker exec bot tail -2 /app/logs/bot.log"
```

Expected: credentials absent, banner present, **zero** `real_orders_*_live.json`, live
virtual files appearing, `bot_pid.json` holding the **trading** bot's PID, config
checksums unchanged, `risk_state.json` showing real balances, testnet bot logging.

Record the config checksums before deploying so the comparison is meaningful.

---

## The handover, later

When you are ready for real money: delete `VIRTUAL_ONLY` and the empty credential lines
from the `bot_live` service, add live keys, and stop the testnet `bot` service. Every
guard lifts at once, the instance starts placing real orders — and it already holds
months of `data/*_live.json` preset statistics gathered on real charts.

No migration, no second instance, nothing to copy. That is the whole point of keeping the
files in their normal places.
