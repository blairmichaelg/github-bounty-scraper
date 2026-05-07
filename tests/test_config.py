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


def test_scraper_config_repr():
    cfg = ScraperConfig(github_token="secret")
    r = repr(cfg)
    assert "ghp_***REDACTED***" in r
    assert "secret" not in r


def test_scraper_config_invalid_weights():
    import pytest

    with pytest.raises(ValueError, match="must sum to a positive number"):
        ScraperConfig(
            weight_amount=0,
            weight_recency=0,
            weight_activity=0,
            weight_escrow_strength=0,
            w_repo_reputation=0,
            weight_vibe=0,
        )


def test_load_signals_missing_file():
    from github_bounty_scraper.config import load_signals

    # Should not crash, returns defaults
    sigs = load_signals("nonexistent.json")
    assert sigs["positive_escrow"] == []


def test_load_config_file_missing():
    from github_bounty_scraper.config import load_config_file

    assert load_config_file("nonexistent.json") == {}


def test_build_config_unrecognized_keys(monkeypatch):
    import pytest

    monkeypatch.setenv("GITHUB_TOKEN", "test_token")
    with pytest.warns(UserWarning, match="unrecognized keys"):
        cfg = build_config({"unknown_key": 123})
    assert cfg.github_token == "test_token"


def test_resolve_github_token_gh_cli(monkeypatch):
    from unittest.mock import MagicMock, patch

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="gh_cli_token\n", check=True)
        assert resolve_github_token() == "gh_cli_token"
