import json
from pathlib import Path
from bot.symbol_registry import SymbolRegistry


def _make_registry(tmp_path, symbols):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "symbols": symbols,
        "weights": {s: 1.0 / len(symbols) for s in symbols},
        "disabled": {},
        "status": {s: {"backtest": "none", "pid": None} for s in symbols},
    }))
    # seed_symbols is ignored when the file already exists
    return SymbolRegistry(seed_symbols=symbols, registry_path=path)


def test_disable_marks_symbol(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    reg.disable("BTCUSDT", reason="not tradeable")
    assert reg.is_disabled("BTCUSDT")
    assert not reg.is_disabled("ETHUSDT")


def test_disable_redistributes_weight(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    reg.disable("BTCUSDT", reason="test")
    assert abs(reg.get_weight("ETHUSDT") + reg.get_weight("SOLUSDT") - 1.0) < 0.001
    assert reg.get_weight("BTCUSDT") == 0.0


def test_reenable_restores_equal_split(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.disable("BTCUSDT", reason="test")
    reg.reenable("BTCUSDT")
    assert not reg.is_disabled("BTCUSDT")
    assert abs(reg.get_weight("BTCUSDT") - 0.5) < 0.001


def test_all_disabled_returns_true(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.disable("BTCUSDT", reason="a")
    reg.disable("ETHUSDT", reason="b")
    assert reg.all_disabled()
