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
    return SymbolRegistry(seed_symbols=symbols, registry_path=path)


def test_pause_marks_symbol(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.pause_symbol("BTCUSDT")
    assert reg.is_symbol_paused("BTCUSDT")
    assert not reg.is_symbol_paused("ETHUSDT")


def test_resume_unmarks_symbol(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.pause_symbol("BTCUSDT")
    reg.resume_symbol("BTCUSDT")
    assert not reg.is_symbol_paused("BTCUSDT")


def test_get_paused_symbols_returns_dict(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.pause_symbol("BTCUSDT")
    paused = reg.get_paused_symbols()
    assert "BTCUSDT" in paused
    assert "paused_at" in paused["BTCUSDT"]


def test_pause_persists_across_reload(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "symbols": ["BTCUSDT"],
        "weights": {"BTCUSDT": 1.0},
        "disabled": {},
        "status": {"BTCUSDT": {"backtest": "none", "pid": None}},
    }))
    reg = SymbolRegistry(seed_symbols=["BTCUSDT"], registry_path=path)
    reg.pause_symbol("BTCUSDT")

    reg2 = SymbolRegistry(seed_symbols=["BTCUSDT"], registry_path=path)
    assert reg2.is_symbol_paused("BTCUSDT")


def test_pause_does_not_affect_weight(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    w_before = reg.get_weight("BTCUSDT")
    reg.pause_symbol("BTCUSDT")
    assert reg.get_weight("BTCUSDT") == w_before
