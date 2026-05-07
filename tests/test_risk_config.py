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
