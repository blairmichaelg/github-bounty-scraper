import pytest
from github_bounty_scraper.config import build_config, ScraperConfig

def test_defaults():
    # Pass a non-existent config file to ensure we only get defaults
    cfg = build_config({"config_file": "non_existent_config.json"})
    # Check some defaults
    assert cfg.min_stars == 5
    assert cfg.weight_amount == 0.3

def test_override():
    cfg = build_config({"min_stars": 10, "config_file": "non_existent_config.json"})
    assert cfg.min_stars == 10
    assert cfg.weight_amount == 0.3 # stays default

def test_unknown_key_warning():
    with pytest.warns(UserWarning, match="unrecognized keys"):
        build_config({"bad_key": "value", "config_file": "non_existent_config.json"})

def test_mode_strict():
    cfg = build_config({"mode": "strict", "config_file": "non_existent_config.json"})
    assert cfg.mode == "strict"
    assert cfg.min_bounty_amount >= 25.0

def test_mode_opportunistic():
    cfg = build_config({"mode": "opportunistic", "config_file": "non_existent_config.json"})
    assert cfg.mode == "opportunistic"
    assert cfg.opportunistic_allow_dead_repos is True
