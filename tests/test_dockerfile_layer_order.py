"""A bot-only change must not rebuild the Next.js app.

`COPY . .` sat above `RUN npm run build`, so editing main.py invalidated the build layer
and every bot-only deploy paid ~40s rebuilding a dashboard the `bot` service does not
serve. Docker caches layers in file order, so the fix is ordering: dashboard sources and
their build first, Python source last.

Comments are stripped before matching — an earlier version of this test found these
strings inside the explanatory comments and reported the opposite of the truth.
"""
from pathlib import Path

RAW = (Path(__file__).resolve().parents[1] / 'Dockerfile').read_text()
INSTRUCTIONS = [
    ln.strip() for ln in RAW.splitlines()
    if ln.strip() and not ln.strip().startswith('#')
]


def _line_of(fragment: str) -> int:
    for i, ln in enumerate(INSTRUCTIONS):
        if fragment in ln:
            return i
    raise AssertionError(f'no Dockerfile instruction contains {fragment!r}')


def test_the_next_build_comes_before_the_python_source_copy():
    assert _line_of('npm run build') < _line_of('COPY . .'), \
        'COPY . . above npm run build makes every Python change rebuild the dashboard'


def test_the_dashboard_source_is_copied_before_its_build():
    assert _line_of('COPY dashboard/ ') < _line_of('npm run build'), \
        'the build needs its sources'


def test_npm_ci_still_precedes_the_build():
    """Dependency install stays in its own earlier layer, or it reinstalls too."""
    assert _line_of('npm ci') < _line_of('npm run build')


def test_the_python_venv_layer_is_still_cached_separately():
    """requirements.txt must be copied before the source, or a code change reinstalls
    every Python dependency."""
    assert _line_of('COPY requirements.txt') < _line_of('COPY . .')


def test_the_final_copy_still_brings_the_python_source():
    """Reordering must not drop the Python source — the bot would ship without main.py."""
    assert any(ln.startswith('COPY . .') for ln in INSTRUCTIONS)


def test_the_dashboard_is_copied_before_the_broad_copy_so_its_layer_is_reusable():
    """Both COPY steps bring dashboard/ in, which is harmless — what matters is that
    the narrow one comes first so the build layer keys only on dashboard sources."""
    assert _line_of('COPY dashboard/ ') < _line_of('COPY . .')
