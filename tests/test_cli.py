"""Tests for cli.py argument parsing and ScraperConfig wiring."""

from __future__ import annotations

import pytest

from github_bounty_scraper.cli import parse_args
from github_bounty_scraper.config import ScraperConfig


def test_defaults_produce_valid_config():
    """parse_args with no arguments should return a valid ScraperConfig with defaults."""
    config = parse_args([])
    assert isinstance(config, ScraperConfig)
    assert config.max_issues_per_run >= 100


def test_max_issues_flag():
    config = parse_args(["--max-issues", "50"])
    assert config.max_issues_per_run == 50


def test_min_amount_flag():
    config = parse_args(["--min-amount", "250"])
    assert config.min_amount == 250.0
    assert config.min_bounty_amount == 250.0


def test_output_flag():
    config = parse_args(["--output", "myresults.json"])
    assert config.output_file == "myresults.json"


def test_db_path_flag():
    config = parse_args(["--db", "custom.db"])
    assert config.db_path == "custom.db"
    assert config.db_file == "custom.db"


def test_top_n_flag():
    config = parse_args(["--top", "25"])
    assert config.top_n == 25


def test_dry_run_flag():
    config = parse_args(["--dry-run"])
    assert config.dry_run is True


def test_dry_run_default_is_false():
    config = parse_args([])
    assert config.dry_run is False


def test_no_vibe_flag():
    config = parse_args(["--no-vibe"])
    assert config.enable_vibe is False


def test_vibe_enabled_by_default():
    config = parse_args([])
    assert config.enable_vibe is True


def test_min_stars_flag():
    config = parse_args(["--min-stars", "10"])
    assert config.min_stars == 10
    assert config.min_repo_stars == 10


def test_scrape_accepts_run_options_after_subcommand():
    config = parse_args(
        [
            "scrape",
            "--max-issues",
            "50",
            "--dry-run",
            "--output-file",
            "results",
            "--min-amount",
            "250",
            "--db-path",
            "custom.db",
            "--min-stars",
            "12",
        ]
    )
    assert config.max_issues_per_run == 50
    assert config.dry_run is True
    assert config.output_file == "results"
    assert config.min_bounty_amount == 250.0
    assert config.db_file == "custom.db"
    assert config.min_repo_stars == 12


def test_invalid_max_issues_raises():
    with pytest.raises(SystemExit):
        parse_args(["--max-issues", "-1"])


def test_weight_flags_normalize_to_one():
    """If weights are passed that don't sum to 1.0, ScraperConfig should normalize them."""
    config = parse_args([])
    total = (
        config.weight_amount
        + config.weight_recency
        + config.weight_activity
        + config.weight_escrow_strength
        + config.w_repo_reputation
        + config.weight_vibe
    )
    assert abs(total - 1.0) < 0.001
