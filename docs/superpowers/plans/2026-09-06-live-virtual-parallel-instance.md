# Live-Mode Virtual Instance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a second bot container against the live Binance market in virtual-only mode, holding no API credentials, so preset statistics can be gathered on real charts before any real money is committed.

**Architecture:** Same Docker image, second compose service, different environment (`TRADING_MODE=live`, `VIRTUAL_ONLY=1`, no credentials). A keyless `python-binance` client serves the public endpoints the virtual path needs and refuses private ones outright, so the instance is structurally incapable of trading. Six shared file paths gain a mode suffix so the two instances cannot corrupt each other.

**Tech Stack:** Python 3.12, python-binance, pytest, Next.js 16 dashboard, Docker Compose.

**Spec:** `docs/specs/2026-09-06-live-virtual-parallel-instance.md`

## Global Constraints

- The bot trades real (testnet) money. Every change must leave `virtual_only=False` behaviour **byte-identical**. Each task carries a test asserting this.
- `bot/virtual_order_simulator.py`, `bot/virtual_tracker.py`, `bot/analyzer.py`, `bot/fake_order.py` are **not to be modified**. The simulation must stay identical across modes or the comparison is meaningless.
- The live instance must never hold API credentials. No task may add them.
- The dashboard is used daily. `mode` defaults to `test` everywhere; omitting it preserves today's behaviour.
- `Settings` is a plain dataclass with no field defaults. New fields go at the end of the field list and get a matching entry in `load_settings()`.
- Run the full suite (`python3 -m pytest tests/ -q`) before every commit. Baseline is **448 passing**.
- Never deploy without explicit user confirmation (CLAUDE.md).

---

### Task 1: `virtual_only` setting and per-mode log paths

Adds the flag and stops the two instances writing to the same four files.

**Files:**
- Modify: `config/settings.py` (field list end; `load_settings()`)
- Modify: `main.py:92` (bot.log handler), `main.py:306` (analysis.jsonl), `main.py:126-127` (system_log, alert_state)
- Test: `tests/test_virtual_only_setting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.virtual_only: bool` (env `VIRTUAL_ONLY`, default `False`); log/state paths suffixed with the trading mode.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_virtual_only_setting.py
"""virtual_only marks an instance that must never place a real order.

The live-market instance runs with no API credentials, so it cannot trade even
if asked. This flag is the in-process expression of that, and it must default
to False so the existing testnet bot is unaffected.
"""
import dataclasses
import os

from config.settings import Settings, load_settings


def test_defaults_to_false():
    """The trading bot must be unaffected by the flag's introduction."""
    assert load_settings().virtual_only is False


def test_is_a_real_settings_field():
    assert 'virtual_only' in {f.name for f in dataclasses.fields(Settings)}


def test_env_enables_it(monkeypatch):
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    assert load_settings().virtual_only is True


def test_env_accepts_the_usual_truthy_spellings(monkeypatch):
    for val in ('1', 'true', 'TRUE', 'yes'):
        monkeypatch.setenv('VIRTUAL_ONLY', val)
        assert load_settings().virtual_only is True, val
    for val in ('0', 'false', 'no', ''):
        monkeypatch.setenv('VIRTUAL_ONLY', val)
        assert load_settings().virtual_only is False, val
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_virtual_only_setting.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'virtual_only'`

- [ ] **Step 3: Add the field**

In `config/settings.py`, after the `live_klines: bool` field:

```python
    # Marks an instance that gathers statistics only and must never place a real
    # order. The live-market instance also runs without credentials, so this is a
    # second, in-process expression of the same guarantee rather than the only one.
    virtual_only: bool
```

And in `load_settings()`, after the `live_klines=` entry:

```python
        virtual_only=os.getenv('VIRTUAL_ONLY', 'false').lower() in ('1', 'true', 'yes'),
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_virtual_only_setting.py -q`
Expected: 4 passed

- [ ] **Step 5: Suffix the four shared paths**

`main.py:92` — the log handler. Read the mode before logging is configured; `TRADING_MODE` is the env var:

```python
    _log_mode = 'test' if os.getenv('TRADING_MODE', 'test') == 'test' else 'live'
    general = logging.handlers.RotatingFileHandler(
        f'logs/bot_{_log_mode}.log', maxBytes=10 * 1024 * 1024, backupCount=5
```

`main.py:306` — analysis log:

```python
        _PROJECT_ROOT / 'logs' / f'analysis_{current_mode}.jsonl',
```

`main.py:126-127` — notifier paths:

```python
        log_path=_PROJECT_ROOT / "data" / f"system_log_{current_mode}.json",
        alert_path=_PROJECT_ROOT / "dashboard" / "public" / f"alert_state_{current_mode}.json",
```

- [ ] **Step 6: Point the dashboard at the renamed files**

`system_log.json` and `alert_state.json` are read by the dashboard. Update the readers to default to the test-mode name:

```bash
grep -rn "system_log.json\|alert_state.json" dashboard/app dashboard/lib
```

For each hit, change the literal to `system_log_test.json` / `alert_state_test.json`.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: 452 passed (448 baseline + 4 new)

- [ ] **Step 8: Migrate the existing files on the server**

The renames orphan the current files. Preserve history rather than losing it:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cd /opt/bot && \
   [ -f data/system_log.json ] && cp data/system_log.json data/system_log_test.json; \
   [ -f dashboard/public/alert_state.json ] && cp dashboard/public/alert_state.json dashboard/public/alert_state_test.json; \
   [ -f logs/analysis.jsonl ] && cp logs/analysis.jsonl logs/analysis_test.jsonl; \
   ls -la data/system_log_test.json logs/analysis_test.jsonl 2>/dev/null"
```

Copy rather than move, so a rollback still finds the originals.

- [ ] **Step 9: Commit**

```bash
git add config/settings.py main.py tests/test_virtual_only_setting.py dashboard/
git commit -m "feat(config): add virtual_only flag and per-mode log paths

Two instances writing one bot.log, analysis.jsonl, system_log.json and
alert_state.json would interleave into unreadable files. Suffixes them with the
trading mode. virtual_only defaults to False so the testnet bot is unchanged."
```

---

### Task 2: Skip the real-order path when `virtual_only`

The safety-critical task. The flag may only ever *skip* work; it must never change what a real order does.

**Files:**
- Modify: `main.py` — `fetch_leverage_brackets()` calls (lines ~293, ~1039, ~1442), `_get_fresh_balance()` call (~1091), the placement loop
- Test: `tests/test_virtual_only_skips_real_orders.py`

**Interfaces:**
- Consumes: `Settings.virtual_only` from Task 1.
- Produces: a bot process that runs analyzers and the virtual simulator but never calls `_try_place_order`, `fetch_account_balance` or `fetch_leverage_brackets`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_virtual_only_skips_real_orders.py
"""A virtual_only instance must never touch a private endpoint or place an order.

It runs against the live market with no credentials. python-binance refuses
private calls outright, so an attempt would raise rather than trade — but it
would also spam errors every candle and could mask a real fault. The flag skips
those code paths entirely.
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
    """futures_leverage_bracket is a private endpoint — 401 without credentials."""
    assert _guarded('fetch_leverage_brackets(')


def test_balance_fetch_is_guarded():
    """futures_account is private and pointless without real orders."""
    assert _guarded('_get_fresh_balance()')


def test_placement_is_guarded():
    assert _guarded('_try_place_order(')


def test_flag_only_skips_never_alters():
    """Guards must be plain skips. A virtual_only branch that CHANGES an order's
    size, price or side would put the flag on the real-money path."""
    src = MAIN.read_text()
    for m in re.finditer(r'virtual_only', src):
        line_start = src.rfind('\n', 0, m.start()) + 1
        line = src[line_start:src.find('\n', m.start())]
        assert not re.search(r'(quantity|entry|tp|sl|leverage)\s*=', line), \
            f'virtual_only must not alter order parameters: {line.strip()}'
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_virtual_only_skips_real_orders.py -q`
Expected: FAIL on the first three tests — no guards exist yet.

- [ ] **Step 3: Add the guards**

In `main.py`, capture the flag once near the other startup values:

```python
    _virtual_only = first_settings.virtual_only
    if _virtual_only:
        logger.warning(
            "VIRTUAL-ONLY instance: no real orders, no balance reads, no private "
            "endpoints. Collecting preset statistics only."
        )
```

Guard each of the three `fetch_leverage_brackets(...)` calls:

```python
    if not _virtual_only:
        await order_executor.fetch_leverage_brackets(symbols)
```

Guard the balance fetch (`main.py` ~1091):

```python
        # A virtual-only instance has no account to read and no credentials to read
        # it with. Virtual sizing uses the rank-pool balances, not this one.
        balance = 0.0 if _virtual_only else await _get_fresh_balance()
        if balance > 0:
            risk_manager.update_balance(balance)
```

Guard the placement loop by skipping the whole candidate pass:

```python
        # Efficiency-ranked cross-symbol placement loop
        if not _virtual_only:
            candle_ts = int(kline[0]) if kline else 0
            ...existing loop unchanged, indented one level...
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_virtual_only_skips_real_orders.py -q`
Expected: 4 passed

- [ ] **Step 5: Prove the trading path is untouched**

Run: `python3 -m pytest tests/ -q`
Expected: 456 passed (452 + 4). **Any pre-existing test that changes behaviour here is a stop signal** — it means the guard altered the real path. Investigate before continuing.

- [ ] **Step 6: Verify it starts keyless**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
TRADING_MODE=live VIRTUAL_ONLY=1 BINANCE_API_KEY= BINANCE_API_SECRET= \
  timeout 20 python3 -c "
import os, main
from config.settings import load_settings
s = load_settings()
print('virtual_only:', s.virtual_only, '| mode:', s.trading_mode)
assert s.virtual_only is True
print('imports and settings OK')
"
```

Expected: `virtual_only: True | mode: live` and no exception.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_virtual_only_skips_real_orders.py
git commit -m "feat(main): skip real-order paths when virtual_only

Guards leverage brackets, balance reads and the placement loop. The flag only
ever skips work — a test asserts no virtual_only branch alters order size,
price, side or leverage, so it cannot reach the real-money path."
```

---

### Task 3: Mode-suffixed exporter, dual-writing for safety

`exporter.py:96` takes `mode` and ignores it, so two instances would overwrite each other's chart data. Dual-write in test mode so the dashboard keeps working untouched.

**Files:**
- Modify: `bot/exporter.py:96`
- Test: `tests/test_exporter_mode_paths.py`

**Interfaces:**
- Consumes: `export(symbol, timeframe, mode, ...)` — `mode` is already a parameter.
- Produces: `dashboard/public/results_{symbol}_{mode}.json` always; `results_{symbol}.json` additionally when `mode == 'test'`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exporter_mode_paths.py
"""Two instances must not overwrite each other's chart data.

export() has always taken `mode` and ignored it in the path. Test mode keeps
writing the unsuffixed name as well, so the dashboard that reads it today does
not break; only live mode is suffixed-only.
"""
from pathlib import Path

import pytest

from bot.exporter import _results_path


def test_test_mode_writes_both_names():
    assert _results_path('INJUSDT', 'test') == [
        Path('dashboard/public/results_INJUSDT_test.json'),
        Path('dashboard/public/results_INJUSDT.json'),
    ]


def test_live_mode_writes_only_the_suffixed_name():
    """Live must never write the unsuffixed file — that is the test bot's."""
    assert _results_path('INJUSDT', 'live') == [
        Path('dashboard/public/results_INJUSDT_live.json'),
    ]


def test_unknown_mode_is_treated_as_live():
    """Fail closed: never clobber the file the dashboard reads."""
    assert Path('dashboard/public/results_INJUSDT.json') not in _results_path('INJUSDT', 'weird')
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_exporter_mode_paths.py -q`
Expected: FAIL — `ImportError: cannot import name '_results_path'`

- [ ] **Step 3: Implement**

In `bot/exporter.py`, above `export()`:

```python
def _results_path(symbol: str, mode: str) -> list[Path]:
    """Where this symbol's chart data goes.

    Test mode also writes the historical unsuffixed name so the dashboard keeps
    working while the mode toggle is added. Any other mode writes only its own
    suffixed file — never the unsuffixed one, which belongs to the test bot.
    """
    paths = [Path(f'dashboard/public/results_{symbol}_{mode}.json')]
    if mode == 'test':
        paths.append(Path(f'dashboard/public/results_{symbol}.json'))
    return paths
```

Replace line 96 and its write with a loop over `_results_path(symbol, mode)`, keeping the existing try/except per path.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_exporter_mode_paths.py -q`
Expected: 3 passed

- [ ] **Step 5: Confirm `symbols.json` needs no change**

The spec listed `dashboard/public/symbols.json` (`exporter.py:125`) as a collision.
It is benign and stays as-is: both instances read the same `symbol_registry.json`, so
both write an identical list and last-writer-wins is a no-op. Verify rather than assume:

```bash
grep -n "def write_symbols_json" -A 6 bot/exporter.py
```

Expected: the content comes solely from the passed symbol list, with no mode-specific
data. If that is ever not true, this file needs a suffix too.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: 459 passed

- [ ] **Step 7: Commit**

```bash
git add bot/exporter.py tests/test_exporter_mode_paths.py
git commit -m "feat(exporter): mode-suffixed results files, dual-written in test

export() took mode and ignored it, so a second instance would overwrite the
first's chart data. Test mode keeps writing the unsuffixed name so the dashboard
is unaffected; live writes only its own file."
```

---

### Task 4: Instance label on notifications

Two bots in one Telegram chat must be distinguishable.

**Files:**
- Modify: `bot/notifier.py` (constructor, `notify()`, `notify_trade_close()`)
- Modify: `main.py:125` (pass the label)
- Test: `tests/test_notifier_instance_label.py`

**Interfaces:**
- Consumes: `Settings.virtual_only`.
- Produces: `Notifier(..., instance_label: str = '')`; when set, every title is prefixed `[<label>] `.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notifier_instance_label.py
"""Two bots share one Telegram chat; you must be able to tell them apart."""
from pathlib import Path
from unittest.mock import patch

from bot.notifier import Notifier


def _n(label: str, tmp_path: Path) -> Notifier:
    return Notifier(
        log_path=tmp_path / 'log.json',
        alert_path=tmp_path / 'alert.json',
        telegram_token='t', telegram_chat_id='c',
        instance_label=label,
    )


def test_label_prefixes_the_title(tmp_path):
    n = _n('LIVE-VIRTUAL', tmp_path)
    with patch.object(Notifier, '_send_telegram') as send:
        n.notify('info', 'API ban ended', 'body', 'src')
    assert '[LIVE-VIRTUAL] API ban ended' in send.call_args[0][0]


def test_no_label_leaves_the_title_alone(tmp_path):
    """The existing bot's messages must not change."""
    n = _n('', tmp_path)
    with patch.object(Notifier, '_send_telegram') as send:
        n.notify('info', 'API ban ended', 'body', 'src')
    text = send.call_args[0][0]
    assert 'API ban ended' in text and '[' not in text.split('API')[0].replace('ℹ️', '').strip()


def test_label_contains_no_html(tmp_path):
    """notify() html-escapes the body but not the title — a tag here would render."""
    n = _n('LIVE-VIRTUAL', tmp_path)
    with patch.object(Notifier, '_send_telegram') as send:
        n.notify('info', 'title', 'body', 'src')
    assert '<b>[' not in send.call_args[0][0].replace('<b>', '', 1)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_notifier_instance_label.py -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'instance_label'`

- [ ] **Step 3: Implement**

Add to `Notifier.__init__` after `warning_repeat_interval_s`:

```python
        instance_label: str = '',
```

```python
        # Distinguishes bots sharing one Telegram chat. Empty for the primary bot so
        # its messages are unchanged.
        self._instance_label = instance_label
```

Add a helper and use it in both `notify()` and `notify_trade_close()` wherever the title is built:

```python
    def _titled(self, title: str) -> str:
        return f"[{self._instance_label}] {title}" if self._instance_label else title
```

In `main.py:125`, pass:

```python
        instance_label='LIVE-VIRTUAL' if first_settings.virtual_only else '',
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_notifier_instance_label.py -q`
Expected: 3 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: 462 passed

- [ ] **Step 6: Commit**

```bash
git add bot/notifier.py main.py tests/test_notifier_instance_label.py
git commit -m "feat(notifier): optional instance label for Telegram titles

Two bots share one chat. The live-virtual instance prefixes [LIVE-VIRTUAL];
the primary bot passes an empty label and its messages are unchanged."
```

---

### Task 5: Dashboard test/live toggle

The trades API already accepts `?mode=` (`route.ts:37`) and the Strategy page loads through a generic `public-file` route, so this is smaller than it looks.

**Files:**
- Create: `dashboard/components/ModeToggle.tsx`
- Modify: `dashboard/app/page.tsx:62` (results fetch)
- Test: `dashboard/_modetoggle.test.mts` (throwaway; delete after running)

**Interfaces:**
- Consumes: `results_{symbol}_{mode}.json` from Task 3.
- Produces: `<ModeToggle value={mode} onChange={setMode} />`; `mode: 'test' | 'live'` persisted in `localStorage` under `bfb-data-mode`.

- [ ] **Step 1: Write the component**

```tsx
// dashboard/components/ModeToggle.tsx
'use client'

/** Chooses which instance's data the page shows.
 *
 *  'test' is the testnet bot that places real (testnet) orders. 'live' is the
 *  virtual-only instance running against the real market — it holds no
 *  credentials and never trades. Defaults to 'test' so the page behaves as before.
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

In `dashboard/app/page.tsx`, add the state and use it in the fetch at line 62:

```tsx
const [dataMode, setDataMode] = useState<DataMode>('test')

useEffect(() => {
  try {
    const saved = localStorage.getItem('bfb-data-mode')
    if (saved === 'live' || saved === 'test') setDataMode(saved)
  } catch { /* private window — keep the default */ }
}, [])
```

```tsx
fetch(`/api/public-file?f=results_${symbol}_${dataMode}.json`)
```

Add `dataMode` to that effect's dependency array, render `<ModeToggle>` in the header, and persist on change:

```tsx
onChange={(m) => {
  setDataMode(m)
  try { localStorage.setItem('bfb-data-mode', m) } catch { /* ignore */ }
}}
```

- [ ] **Step 3: Typecheck and build**

```bash
cd dashboard && npx tsc --noEmit && npm run build
```

Expected: no errors; `✓ Compiled successfully`.

- [ ] **Step 4: Verify the default is unchanged**

```bash
cd dashboard && grep -n "dataMode" app/page.tsx | head
```

Expected: `useState<DataMode>('test')` — the page must load testnet data with no stored preference.

- [ ] **Step 5: Commit**

```bash
git add dashboard/components/ModeToggle.tsx dashboard/app/page.tsx
git commit -m "feat(dashboard): testnet/live data toggle on the Strategy page

Reads results_{symbol}_{mode}.json. Defaults to test and persists the choice,
so the page is unchanged for anyone who does not touch it."
```

---

### Task 6: Compose service and first run

**Files:**
- Modify: `docker-compose.yml`
- Modify: `FEATURES.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a `bot_live` container.

- [ ] **Step 1: Add the service**

```yaml
  # Live-market data, virtual orders only. Deliberately has NO credentials:
  # python-binance refuses private endpoints without a secret, so this container
  # is structurally incapable of placing an order rather than merely configured
  # not to. Shares risk_config.json and symbol_registry.json with `bot` so the
  # only difference between the two is the market data.
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
      - ./risk_config.json:/app/risk_config.json
      - ./symbol_registry.json:/app/symbol_registry.json
    command: >
      sh -c 'cd /app && exec .venv/bin/python3 main.py >> /app/logs/bot_live.log 2>&1'
    restart: unless-stopped
    stop_grace_period: 60s
```

Note: `environment:` rather than `env_file: .env` — that is what keeps the credentials out.

- [ ] **Step 2: Verify no credentials leak in**

```bash
python3 -c "
import yaml
c = yaml.safe_load(open('docker-compose.yml'))
env = c['services']['bot_live'].get('environment', {})
assert 'env_file' not in c['services']['bot_live'], 'env_file would inject real keys'
assert env.get('BINANCE_API_KEY') == '', env
assert env.get('VIRTUAL_ONLY') == '1'
assert env.get('TRADING_MODE') == 'live'
print('bot_live carries no credentials')
"
```

- [ ] **Step 3: Update FEATURES.md**

Add a section describing the instance, the keyless guarantee, the mode-suffixed paths, and the dashboard toggle. Reference the spec path.

- [ ] **Step 4: Run the full suite and commit**

```bash
python3 -m pytest tests/ -q
git add docker-compose.yml FEATURES.md
git commit -m "feat(deploy): bot_live service — live market, virtual only, no keys"
```

- [ ] **Step 5: STOP — get explicit deploy approval**

Do not deploy. Report to the user: what will be started, that the existing bot is unchanged, and the file migrations from Task 1 Step 8. Wait for confirmation.

- [ ] **Step 6: Deploy the new service only**

The existing `bot` container must not be restarted by this step beyond the image rebuild it needs for Tasks 1-4:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cd /opt/bot && git pull origin feature/mean-reversion-overlay && \
   docker compose up -d --build 2>&1 | tail -10"
```

- [ ] **Step 7: Verify the guarantee holds in production**

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "echo '--- no credentials? ---'; docker exec bot_live printenv | grep -c 'BINANCE_API_KEY=$'; \
   echo '--- virtual-only banner ---'; docker exec bot_live grep -c 'VIRTUAL-ONLY instance' /app/logs/bot_live.log; \
   echo '--- any real order file? (must be 0) ---'; ls /opt/bot/data/real_orders_*_live.json 2>/dev/null | wc -l; \
   echo '--- live virtual orders appearing? ---'; ls /opt/bot/data/virtual_orders_rank2_*_live.json 2>/dev/null | wc -l; \
   echo '--- test bot still healthy ---'; docker exec bot tail -2 /app/logs/bot_test.log"
```

Expected: banner present, **zero** `real_orders_*_live.json`, live virtual files appearing, test bot logging normally.

---

## After the plan

Leave running for a week, then compare per preset:

```
preset            test net    live net
l2_bos_trend        +5.36       ?
r5_sl_filter        -1.20       ?
```

If the rankings disagree, the current preset selection should not be trusted when going live — which is the finding this was built to produce.
