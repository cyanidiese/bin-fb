"""A virtual-only instance must not trade and must not write shared state.

Each guard protects something specific; the docstrings say what, because a future
reader deleting one "harmless" guard is exactly how this breaks. Most of these
protect the TESTNET bot, not the live one.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def _guarded(call: str) -> bool:
    """True if every CALL of `call` sits under a virtual_only guard.

    Definitions are skipped — `async def _get_fresh_balance()` contains the same text
    as a call to it, and guarding a definition is not a thing.
    """
    src = MAIN.read_text()
    hits = list(re.finditer(re.escape(call), src))
    assert hits, f'{call} not found in main.py — has it been renamed?'
    for m in hits:
        line_start = src.rfind('\n', 0, m.start()) + 1
        if src[line_start:m.start()].lstrip().startswith(('def ', 'async def ')):
            continue
        window = src[max(0, m.start() - 700):m.start()]
        if 'virtual_only' not in window:
            return False
    return True


def test_leverage_brackets_are_guarded():
    """futures_leverage_bracket is private — 401 without credentials."""
    assert _guarded('fetch_leverage_brackets(')


def test_balance_fetch_is_guarded():
    """futures_account is private, and virtual sizing uses rank-pool balances.

    Guarded inside the function rather than at each call site, so callers added later
    are covered automatically.
    """
    src = MAIN.read_text()
    body = src[src.index('async def _get_fresh_balance'):][:900]
    assert 'virtual_only' in body, 'the balance fetch must short-circuit for virtual_only'
    assert 'return 0.0' in body


def test_placement_is_guarded():
    """The placement pass is skipped by emptying its candidate source, rather than by
    re-indenting 200 lines of allocation logic on the real-money path."""
    src = MAIN.read_text()
    m = re.search(r'_placement_symbols\s*=([^\n]+)', src)
    assert m, 'the placement loop must draw from a gated symbol list'
    assert 'virtual_only' in m.group(1)
    assert 'for sym in _placement_symbols:' in src


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
