"""The preset Profit% store covers every date shortcut the Trades page offers.

The store derives its windows from RANGE_PRESETS itself, so a new shortcut is covered
automatically — this guards that nobody reintroduces a hand-kept list that can drift
(the recalc script used to hold one), and that "today"/"all" stay special-cased by
their `days` value rather than by name.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = (ROOT / 'dashboard/app/api/trades/_preset-profit-store.ts').read_text()
SCRIPT = (ROOT / 'scripts/recalc_symbol_scores.sh').read_text()
PAGE_KEYS = re.findall(r"\{ key: '([^']+)'",
                       (ROOT / 'dashboard/lib/tradesDateRange.ts').read_text())


def test_store_windows_come_from_range_presets():
    assert 'for (const p of RANGE_PRESETS)' in STORE
    assert 'p.days === null ? null : p.days === 0 ? midnightS' in STORE


def test_no_hand_kept_shortcut_list_in_store_or_script():
    for key in PAGE_KEYS:
        if key in ('today', 'all'):
            continue
        assert f"'{key}'" not in STORE and f'"{key}"' not in SCRIPT, key


def test_page_still_offers_two_weeks():
    assert '14d' in PAGE_KEYS
