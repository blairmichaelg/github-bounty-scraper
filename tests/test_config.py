"""Tests for config."""

from github_bounty_scraper.config import ScraperConfig, build_config, resolve_github_token


def test_scraper_config_post_init():
    cfg = ScraperConfig(
        weight_amount=0.5,
        weight_recency=0.5,
        weight_activity=0,
        weight_escrow_strength=0,
        w_repo_reputation=0,
        weight_vibe=0,
    )
    assert cfg.weight_amount == 0.5

    # Test normalization
    cfg = ScraperConfig(
        weight_amount=10.0,
        weight_recency=10.0,
        weight_activity=0,
        weight_escrow_strength=0,
        w_repo_reputation=0,
        weight_vibe=0,
    )
    assert cfg.weight_amount == 0.5
    assert cfg.weight_recency == 0.5


def test_resolve_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test_token")
    assert resolve_github_token() == "test_token"


def test_build_config(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test_token")
    cfg = build_config({"min_stars": 100})
    assert cfg.min_stars == 100
    assert cfg.github_token == "test_token"
