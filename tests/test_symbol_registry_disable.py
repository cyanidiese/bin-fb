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


def test_disable_rank_and_enable(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT"])
    assert not reg.is_rank_disabled("BTCUSDT", 3)
    reg.disable_rank("BTCUSDT", 3)
    assert reg.is_rank_disabled("BTCUSDT", 3)
    assert not reg.is_rank_disabled("BTCUSDT", 2)
    reg.enable_rank("BTCUSDT", 3)
    assert not reg.is_rank_disabled("BTCUSDT", 3)


def test_disable_rank_persists(tmp_path):
    path = tmp_path / "registry.json"
    symbols = ["BTCUSDT"]
    path.write_text(json.dumps({
        "symbols": symbols,
        "weights": {"BTCUSDT": 1.0},
        "disabled": {},
        "status": {"BTCUSDT": {"backtest": "none", "pid": None}},
    }))
    reg = SymbolRegistry(seed_symbols=symbols, registry_path=path)
    reg.disable_rank("BTCUSDT", 4)

    # Reload from disk
    reg2 = SymbolRegistry(seed_symbols=symbols, registry_path=path)
    assert reg2.is_rank_disabled("BTCUSDT", 4)
    assert not reg2.is_rank_disabled("BTCUSDT", 2)


def test_enable_rank_cleans_up_empty_entry(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT"])
    reg.disable_rank("BTCUSDT", 2)
    reg.enable_rank("BTCUSDT", 2)
    # Enabling the only disabled rank should remove the symbol key entirely
    data = json.loads((tmp_path / "registry.json").read_text())
    assert "BTCUSDT" not in data.get("disabled_ranks", {})
