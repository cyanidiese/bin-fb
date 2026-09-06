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
