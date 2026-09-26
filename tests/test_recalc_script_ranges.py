"""scripts/recalc_symbol_scores.sh fills the picker sort cache for every date shortcut.

It is plain JS inside a docker exec, so it cannot import the page's RANGE_PRESETS and
keeps its own list. A shortcut added to the page but not the script would sort that
shortcut by whatever few symbols were clicked — silently. Keep the three lists in step.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_KEYS = re.findall(r"\{ key: '([^']+)'",
                       (ROOT / 'dashboard/lib/tradesDateRange.ts').read_text())
SCRIPT = (ROOT / 'scripts/recalc_symbol_scores.sh').read_text()
ROUTE = (ROOT / 'dashboard/app/api/trades/symbol-scores/route.ts').read_text()


def test_script_covers_every_shortcut():
    listed = re.search(r'for \(const range of \[([^\]]+)\]', SCRIPT).group(1)
    assert re.findall(r'"([^"]+)"', listed) == PAGE_KEYS


def test_script_knows_every_sliding_window_length():
    days = re.search(r'const days = \{([^}]+)\}', SCRIPT).group(1)
    sliding = [k for k in PAGE_KEYS if k not in ('today', 'all')]
    assert sorted(re.findall(r'"([^"]+)":', days)) == sorted(sliding)


def test_route_has_a_ttl_for_every_shortcut():
    ttl = re.search(r'const TTL_MS[^{]+\{([^}]+)\}', ROUTE).group(1)
    assert sorted(re.findall(r"'?([\w]+)'?:", ttl)) == sorted(PAGE_KEYS)
