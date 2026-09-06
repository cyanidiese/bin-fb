# Live-Mode Virtual Instance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a second bot container against the live Binance market in virtual-only mode, holding no API credentials, so preset statistics can be gathered on real charts before any real money is committed.

**Architecture:** Same Docker image, second compose service, different environment (`TRADING_MODE=live`, `VIRTUAL_ONLY=1`, no credentials). A keyless `python-binance` client serves the public endpoints the virtual path needs and refuses private ones outright, so the instance is structurally incapable of trading.

Separation is by **separate host directories**, not by filename: the live container mounts `./data_live` and `./logs_live` at the same in-container paths, so a missed suffix cannot cause a collision. Only `dashboard/public/` stays shared — the dashboard has to read both — and the files written there gain a mode suffix. Shared config is mounted read-only, and the two code paths that write it are skipped.

**Tech Stack:** Python 3.12, python-binance, pytest, Next.js 16 dashboard, Docker Compose.

**Spec:** `docs/specs/2026-09-06-live-virtual-parallel-instance.md`

## Global Constraints

- The bot trades real (testnet) money. Every change must leave `virtual_only=False` behaviour **byte-identical**. Each task carries a test asserting this.
- `bot/virtual_order_simulator.py`, `bot/virtual_tracker.py`, `bot/analyzer.py`, `bot/fake_order.py` are **not to be modified**. The simulation must stay identical across modes or the comparison is meaningless.
- The live instance must never hold API credentials. No task may add them.
- **The live instance must never write shared state.** `weight_rebalancer` calls `save_risk_config()` (`weight_rebalancer.py:217`) every candle, and `_auto_disable()` writes `symbol_registry.json`. Either would silently retune the testnet bot's real trading from live-market virtual results. Both are skipped under `virtual_only`, and both files are mounted `:ro` as a backstop.
- The dashboard is used daily. `mode` defaults to `test` everywhere; omitting it preserves today's behaviour.
- `Settings` is a plain dataclass with no field defaults. New fields go at the end of the field list and get a matching entry in `load_settings()`.
- Run the full suite (`python3 -m pytest tests/ -q`) before every commit. Baseline is **448 passing**.
- Never deploy without explicit user confirmation (CLAUDE.md).

---

### Task 1: `virtual_only` setting

Adds the flag only. **No file renames**: separation comes from mounting separate host
directories (Task 6), so `logs/bot.log`, `logs/analysis.jsonl` and `data/system_log.json`
keep their names inside each container and land in different places on the host. That
avoids a server-side migration, dashboard reader changes and a logrotate update, and
leaves every existing diagnostic command working.

**Files:**
- Modify: `config/settings.py` (field list end; `load_settings()`)
- Test: `tests/test_virtual_only_setting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.virtual_only: bool` (env `VIRTUAL_ONLY`, default `False`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_virtual_only_setting.py
"""virtual_only marks an instance that gathers statistics only.

No real orders, no private endpoints, no writes to shared config. The live-market
instance also runs with no credentials, so this flag is a second expression of the
same guarantee rather than the only one. It must default to False so the existing
testnet bot is unaffected.
"""
import dataclasses

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
    # Marks an instance that gathers statistics only: no real orders, no private
    # endpoints, and no writes to shared config.
    virtual_only: bool
```

And in `load_settings()`, after the `live_klines=` entry:

```python
        virtual_only=os.getenv('VIRTUAL_ONLY', 'false').lower() in ('1', 'true', 'yes'),
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_virtual_only_setting.py -q`
Expected: 4 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: 452 passed (448 baseline + 4 new)

- [ ] **Step 6: Commit**

```bash
git add config/settings.py tests/test_virtual_only_setting.py
git commit -m "feat(config): add virtual_only flag

Marks a statistics-only instance: no real orders, no private endpoints, no
writes to shared config. Defaults to False so the testnet bot is unchanged."
```

---

### Task 1a: Derive the mode from settings, not the shared mode file

**Without this the whole design fails silently.** Data file paths use
`mode_manager.current_mode`, which reads `data/bot_mode.json` and falls back to `"test"`
(`mode_manager.py:43-49`) — it never consults `TRADING_MODE`. The live instance would
connect to live endpoints (from env) while naming every file `..._test`. Task 6's
separate directories stop that corrupting the testnet bot, but the live container's own
files would be misleadingly named `_test`, and the dashboard toggle (Task 5) would never
find them. It is masked today only because `bot_mode.json` is absent, so the fallback and
the env happen to agree.

**Files:**
- Modify: `bot/mode_manager.py` (`__init__`)
- Modify: `main.py` (ModeManager construction)
- Test: `tests/test_mode_manager_forced_mode.py`

**Interfaces:**
- Consumes: `Settings.virtual_only`, `Settings.trading_mode` (Task 1).
- Produces: `ModeManager(..., forced_mode: str | None = None)`; when set, `current_mode`
  is that value and the shared mode file is never read.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mode_manager_forced_mode.py
"""A virtual-only instance is not mode-switchable — it IS its configured mode.

current_mode drives every data file path. It normally reads data/bot_mode.json, which
the dashboard can rewrite; without an override the live instance would name its files
after whatever that file says instead of its own configured mode.
"""
import json

from bot.mode_manager import ModeManager


def test_forced_mode_ignores_the_mode_file(tmp_path):
    mode_file = tmp_path / 'bot_mode.json'
    mode_file.write_text(json.dumps({'mode': 'test'}))
    m = ModeManager(mode_path=mode_file, forced_mode='live')
    assert m.current_mode == 'live', 'the mode file must not retarget this instance'


def test_forced_mode_works_when_the_file_is_absent(tmp_path):
    m = ModeManager(mode_path=tmp_path / 'missing.json', forced_mode='live')
    assert m.current_mode == 'live'


def test_without_forced_mode_behaviour_is_unchanged(tmp_path):
    """The testnet bot must keep reading the file exactly as before."""
    mode_file = tmp_path / 'bot_mode.json'
    mode_file.write_text(json.dumps({'mode': 'test'}))
    assert ModeManager(mode_path=mode_file).current_mode == 'test'


def test_absent_file_still_falls_back_to_test(tmp_path):
    assert ModeManager(mode_path=tmp_path / 'missing.json').current_mode == 'test'
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_mode_manager_forced_mode.py -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'forced_mode'`

- [ ] **Step 3: Implement**

In `bot/mode_manager.py`, add the parameter to `__init__` after `notifier`:

```python
        forced_mode: str | None = None,
```

and replace the `current_mode` assignment:

```python
        # A virtual-only instance is pinned to its configured mode. bot_mode.json is
        # written by the dashboard, so honouring it here would let a mode switch
        # retarget this instance's data files.
        self._forced_mode = forced_mode
        self.current_mode: str = forced_mode or self._read_mode()
```

In `main.py`, where ModeManager is constructed, pass:

```python
        forced_mode=first_settings.trading_mode if first_settings.virtual_only else None,
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_mode_manager_forced_mode.py -q`
Expected: 4 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: 456 passed

- [ ] **Step 6: Commit**

```bash
git add bot/mode_manager.py main.py tests/test_mode_manager_forced_mode.py
git commit -m "fix(mode): pin a virtual-only instance to its configured mode

current_mode drives every data file path but read only data/bot_mode.json,
never TRADING_MODE. The live instance would have connected to live endpoints
while naming its files _test."
```

---

### Task 2: Skip the real-order path and all shared-config writes

The safety-critical task. The flag may only ever *skip* work; it must never change what
a real order does.

Two of these guards protect the **testnet bot**, not the live one:
`weight_rebalancer.on_candle_close()` calls `save_risk_config()` every candle, and
`check_symbols_on_exchange()` can `_auto_disable()` a symbol into `symbol_registry.json`.
Unguarded, the live instance would retune the testnet bot's real allocation and disable
its symbols based on live-market virtual results.

**Files:**
- Modify: `main.py` — `fetch_leverage_brackets()` calls (~293, ~1039, ~1442), `_get_fresh_balance()` (~1091), the placement loop, `check_symbols_on_exchange()` (~288, ~814), `weight_rebalancer.on_candle_close()` (~1352)
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


def test_weight_rebalancer_is_guarded():
    """It calls save_risk_config() every candle. Unguarded, the live instance would
    retune the TESTNET bot's real symbol allocation from live virtual results."""
    assert _guarded('weight_rebalancer.on_candle_close(')


def test_exchange_symbol_check_is_guarded():
    """It can _auto_disable() a symbol into the shared symbol_registry.json,
    disabling it for the testnet bot too."""
    assert _guarded('check_symbols_on_exchange(')


def test_reconcile_with_exchange_is_guarded():
    """Startup call to futures_position_information — a private endpoint. Keyless it
    logs an error every boot, and its whole purpose (closing positions the bot does
    not know about) is meaningless for an instance that never opens any."""
    assert _guarded('reconcile_with_exchange(')


def test_telegram_menu_is_guarded():
    """Both instances would poll getUpdates with the SAME bot token. Telegram
    delivers each update exactly once, so commands would go to whichever instance
    grabbed them first — including do_pause / do_resume / do_enable, which mutate the
    shared symbol registry, and the allow/deny/revoke auth flow."""
    assert _guarded('telegram_menu.run()')


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
Expected: FAIL on the first seven tests — no guards exist yet.

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

Guard the two shared-config writers. Both protect the *testnet* bot:

```python
        # save_risk_config() every candle — a virtual-only instance must never
        # retune the trading bot's allocation from its own virtual results.
        if not _virtual_only:
            weight_rebalancer.on_candle_close(candle_ts)
```

```python
        # _auto_disable() writes the shared symbol_registry.json.
        if not _virtual_only:
            await order_executor.check_symbols_on_exchange(symbols)
```

```python
        # Private endpoint, and pointless for an instance that opens no positions.
        if not _virtual_only:
            await order_executor.reconcile_with_exchange()
```

```python
        # Telegram delivers each update exactly once. Two pollers on one token means
        # your commands land on a coin flip — and do_pause/do_resume/do_enable mutate
        # the shared symbol registry.
        if not _virtual_only:
            _menu_task = asyncio.create_task(telegram_menu.run())
```

Note `_menu_task` is referenced during shutdown; initialise it to `None` before the
guard and skip cancelling it when it is `None`.

Note `candle_ts` is assigned inside the placement guard above; move its assignment
above both guards so the rebalancer guard can still reference it.

- [ ] **Step 3b: Prove the shared config is untouched**

```bash
md5_before=$(md5 -q risk_config.json 2>/dev/null || md5sum risk_config.json | cut -d' ' -f1)
echo "risk_config.json before: $md5_before"
```

Record it; Task 6 Step 7 re-checks it after the live instance has run.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_virtual_only_skips_real_orders.py -q`
Expected: 8 passed

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

### Task 3: Mode-suffix everything written to the shared public dir

`dashboard/public/` is the **one shared mount** — the dashboard has to read both
instances — so every bot-written file in it needs a mode suffix. Three do:

| file | writer | today |
|---|---|---|
| `results_{symbol}.json` | `exporter.py:96` | takes `mode`, ignores it |
| `alert_state.json` | `main.py:127` (Notifier) | not suffixed |
| `risk_state.json` | `risk_manager.py:35` | not suffixed |

`risk_state.json` is the nastiest: a virtual-only instance has **balance 0** (Task 2
skips the balance read), so it would overwrite the trading bot's real risk state with
zeros — and that is what the dashboard's risk page displays.

`symbols.json` (`exporter.py:125`) stays shared: both read the same registry, so both
write an identical list and last-writer-wins is a no-op.

Every suffix is applied **only for non-test modes**, so today's filenames — and the
dashboard readers that expect them — are untouched.

**Files:**
- Modify: `bot/exporter.py:96`
- Modify: `main.py:127` (alert_path), `bot/risk_manager.py:35` + its construction in `main.py`
- Modify: `dashboard/app/api/risk/route.ts:7` (accept `?mode=`, default test)
- Test: `tests/test_shared_public_paths.py`

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


def test_alert_and_risk_state_are_suffixed_only_off_test():
    """Both live in the shared public mount. risk_state matters most: a virtual-only
    instance has balance 0, so an unsuffixed write would show the trading bot's risk
    page as zeroed."""
    from main import _public_state_path          # helper added in this task
    assert _public_state_path('risk_state.json', 'test').name == 'risk_state.json'
    assert _public_state_path('risk_state.json', 'live').name == 'risk_state_live.json'
    assert _public_state_path('alert_state.json', 'test').name == 'alert_state.json'
    assert _public_state_path('alert_state.json', 'live').name == 'alert_state_live.json'
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_exporter_mode_paths.py -q`
Expected: FAIL — `ImportError: cannot import name '_results_path'`

- [ ] **Step 3: Implement**

In `main.py`, add the shared-public-dir helper next to the other path builders:

```python
def _public_state_path(name: str, mode: str) -> Path:
    """Path for a bot-written file in the SHARED dashboard/public mount.

    Test mode keeps the historical unsuffixed name so every existing dashboard reader
    is untouched; any other mode gets its own file. Without this, a virtual-only
    instance (balance 0) would overwrite the trading bot's risk_state.json with zeros.
    """
    if mode == 'test':
        return _PROJECT_ROOT / 'dashboard' / 'public' / name
    stem, _, ext = name.rpartition('.')
    return _PROJECT_ROOT / 'dashboard' / 'public' / f'{stem}_{mode}.{ext}'
```

Use it for the notifier's `alert_path` and pass a `state_path` into `RiskManager`:

```python
        alert_path=_public_state_path('alert_state.json', current_mode),
```

```python
    risk_manager = RiskManager(
        mode=current_mode,
        notifier=notifier,
        state_path=_public_state_path('risk_state.json', current_mode),
    )
```

`current_mode` is not yet assigned where the notifier is built — move the
`_public_state_path` call for `alert_path` to after `current_mode` is set, or pass
`_base_settings.trading_mode` if `virtual_only` else `'test'`.

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

The live instance writes to a **separate host directory** (`data_live/`), and the
dashboard hardcodes `path.join(BOT_ROOT, 'data', ...)` (`_utils.ts:3`) while mounting
only `./data`. Without a directory-aware resolver the toggle would find nothing — the
suffixed filenames it looks for live in a directory it cannot see.

**Files:**
- Create: `dashboard/components/ModeToggle.tsx`
- Modify: `dashboard/app/api/_utils.ts` (add `dataDir(mode)`)
- Modify: `dashboard/app/api/trades/route.ts` (use `dataDir(mode)` in place of `'data'`)
- Modify: `dashboard/app/page.tsx:62` (results fetch)
- Test: `dashboard/_modetoggle.test.mts` (throwaway; delete after running)

**Interfaces:**
- Consumes: `results_{symbol}_{mode}.json` from Task 3.
- Produces: `<ModeToggle value={mode} onChange={setMode} />`; `mode: 'test' | 'live'` persisted in `localStorage` under `bfb-data-mode`.

- [ ] **Step 1: Make the API directory-aware**

In `dashboard/app/api/_utils.ts`:

```ts
/** Where a given instance's data lives.
 *
 *  The live virtual instance runs in its own container with ./data_live mounted at
 *  /app/data, so on the host its files sit in a different directory — not merely
 *  under a different filename. Test keeps the original path so nothing changes.
 */
export function dataDir(mode: string): string {
  return path.join(BOT_ROOT, mode === 'live' ? 'data_live' : 'data')
}
```

In `dashboard/app/api/trades/route.ts`, replace each `path.join(BOT_ROOT, 'data', X)`
with `path.join(dataDir(mode), X)`. Note `bot_mode.json` at line 16 must keep reading
the **test** directory — it is the testnet bot's mode file, not the live instance's.

- [ ] **Step 2: Write the component**

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

- [ ] **Step 3: Wire it into the Strategy page**

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

- [ ] **Step 4: Typecheck and build**

```bash
cd dashboard && npx tsc --noEmit && npm run build
```

Expected: no errors; `✓ Compiled successfully`.

- [ ] **Step 5: Verify the default is unchanged**

```bash
cd dashboard && grep -n "dataMode" app/page.tsx | head
```

Expected: `useState<DataMode>('test')` — the page must load testnet data with no stored preference.

- [ ] **Step 6: Commit**

```bash
git add dashboard/components/ModeToggle.tsx dashboard/app/page.tsx dashboard/app/api/
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

- [ ] **Step 1: Create the host directories**

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "mkdir -p /opt/bot/data_live /opt/bot/logs_live && ls -ld /opt/bot/data_live /opt/bot/logs_live"
```

- [ ] **Step 2: Add the service**

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
      # SEPARATE host directories, mounted at the same in-container paths. This is the
      # primary isolation: even a path that forgets its mode suffix cannot reach the
      # testnet bot's data. dashboard/public is the one shared mount, because the
      # dashboard must read both instances — the files written there are suffixed.
      - ./data_live:/app/data
      - ./logs_live:/app/logs
      - ./dashboard/public:/app/dashboard/public
      # Read-only: shared config is an input, never an output, for this instance.
      # Task 2 skips the two writers; :ro is the backstop if one is ever missed.
      - ./risk_config.json:/app/risk_config.json:ro
      - ./symbol_registry.json:/app/symbol_registry.json:ro
    command: >
      sh -c 'cd /app && exec .venv/bin/python3 main.py >> /app/logs/bot.log 2>&1'
    restart: unless-stopped
    stop_grace_period: 60s
```

Note: `environment:` rather than `env_file: .env` — that is what keeps the credentials out.

- [ ] **Step 2b: Mount the live data into the dashboard**

The dashboard needs to *read* `data_live/` for the Task 5 toggle. Add to the existing
`dashboard` service volumes (read-only — the dashboard must never write live data):

```yaml
      - ./data_live:/app/data_live:ro
```

- [ ] **Step 3: Verify no credentials leak in**

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

- [ ] **Step 4: Update FEATURES.md**

Add a section describing the instance, the keyless guarantee, the mode-suffixed paths, and the dashboard toggle. Reference the spec path.

- [ ] **Step 5: Run the full suite and commit**

```bash
python3 -m pytest tests/ -q
git add docker-compose.yml FEATURES.md
git commit -m "feat(deploy): bot_live service — live market, virtual only, no keys"
```

- [ ] **Step 6: STOP — get explicit deploy approval**

Do not deploy. Report to the user: what will be started, that the existing bot is unchanged, and the file migrations from Task 1 Step 8. Wait for confirmation.

- [ ] **Step 7: Deploy the new service only**

The existing `bot` container must not be restarted by this step beyond the image rebuild it needs for Tasks 1-4:

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cd /opt/bot && git pull origin feature/mean-reversion-overlay && \
   docker compose up -d --build 2>&1 | tail -10"
```

- [ ] **Step 8: Verify the guarantees hold in production**

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cd /opt/bot
   echo '--- credentials absent? (expect 1) ---'; docker exec bot_live printenv | grep -c 'BINANCE_API_KEY=$'
   echo '--- virtual-only banner ---';           docker exec bot_live grep -c 'VIRTUAL-ONLY instance' /app/logs/bot.log
   echo '--- real order files in live data? (MUST be 0) ---'; ls data_live/real_orders_*.json 2>/dev/null | wc -l
   echo '--- live virtual orders appearing? ---'; ls data_live/virtual_orders_rank2_*_live.json 2>/dev/null | wc -l
   echo '--- files named _test in live dir? (MUST be 0 — Task 1a) ---'; ls data_live/*_test.json 2>/dev/null | wc -l
   echo '--- shared config untouched? ---';      md5sum risk_config.json symbol_registry.json
   echo '--- testnet bot still healthy ---';     docker exec bot tail -2 /app/logs/bot.log"
```

Expected: credentials absent, banner present, **zero** `real_orders_*` in `data_live/`,
live virtual files appearing, **zero** `_test` files in `data_live/`, config checksums
matching the values recorded in Task 2 Step 3b, and the testnet bot logging normally.

## After the plan

Leave running for a week, then compare per preset:

```
preset            test net    live net
l2_bos_trend        +5.36       ?
r5_sl_filter        -1.20       ?
```

If the rankings disagree, the current preset selection should not be trusted when going live — which is the finding this was built to produce.
