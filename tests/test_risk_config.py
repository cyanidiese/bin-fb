# tests/test_risk_config.py
import json
import pytest
from pathlib import Path
from config.risk_config import load_risk_config, save_risk_config, DEFAULT_CONFIG


def test_load_creates_file_when_missing(tmp_path):
    p = tmp_path / "risk_config.json"
    cfg = load_risk_config(p)
    assert p.exists()
    assert cfg["base_leverage"] == 2
    assert cfg["min_profit_factor"] == 1.2
    assert len(cfg["balance_tiers"]) == 3


def test_load_merges_missing_keys(tmp_path):
    p = tmp_path / "risk_config.json"
    p.write_text(json.dumps({"base_leverage": 5}))
    cfg = load_risk_config(p)
    # New key from defaults appears
    assert "min_profit_factor" in cfg
    # Existing key preserved
    assert cfg["base_leverage"] == 5


def test_save_and_reload(tmp_path):
    p = tmp_path / "risk_config.json"
    cfg = load_risk_config(p)
    cfg["base_leverage"] = 7
    save_risk_config(cfg, p)
    cfg2 = load_risk_config(p)
    assert cfg2["base_leverage"] == 7


def test_corrupt_file_returns_defaults(tmp_path):
    p = tmp_path / "risk_config.json"
    p.write_text("not json{{{")
    cfg = load_risk_config(p)
    assert cfg["base_leverage"] == 2


def test_new_defaults_present(tmp_path):
    path = tmp_path / "risk_config.json"
    cfg = load_risk_config(path)
    assert "telegram" in cfg
    assert cfg["telegram"] == {"token": "", "chat_id": ""}
    assert cfg["min_balance_pct"] == 15.0
    assert cfg["consecutive_failure_threshold"] == 3
    assert cfg["test_starting_balance_usdt"] == 10000.0
    assert cfg["max_leverage"] == 20
    assert cfg["price_stale_threshold_s"] == 15


def test_existing_file_missing_new_keys_gets_defaults(tmp_path):
    path = tmp_path / "risk_config.json"
    # Write file without new keys
    path.write_text('{"drawdown_warning_pct": 10.0}')
    cfg = load_risk_config(path)
    assert cfg["drawdown_warning_pct"] == 10.0
    assert cfg["telegram"] == {"token": "", "chat_id": ""}
    assert cfg["consecutive_failure_threshold"] == 3
