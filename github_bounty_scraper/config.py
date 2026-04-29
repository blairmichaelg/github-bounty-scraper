"""
Configuration management — loads settings from JSON config, CLI args, and
hard-coded defaults.  CLI flags always override the config file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from .log import get_logger

# ─── Defaults ────────────────────────────────────────────────────────
DEFAULT_CONFIG_FILE = "scraper_config.json"
DEFAULT_SIGNALS_FILE = "signals_config.json"

CRYPTO_KEYWORDS = [
    "USDC", "ETH", "SOL", "OP", "ARB", "MATIC", "DAI", "WETH", "STRK", "ROXN",
]

# Stablecoins treated as 1:1 USD
STABLECOIN_SYMBOLS = {"USDC", "USDT", "DAI", "BUSD"}


@dataclass
class ScraperConfig:
    """Runtime configuration assembled from defaults → config file → CLI."""

    # ── Authentication ──
    github_token: str = ""

    # ── Search / discovery ──
    languages: list[str] = field(default_factory=list)
    min_stars: int = 10
    since: str = ""  # YYYY-MM-DD
    max_issues: int = 0  # 0 = unlimited
    max_pages_per_query: int = 5
    sort_by: str = "updated"

    # ── Thresholds ──
    min_bounty_amount: float = 10.0
    max_sane_amount: float = 1e7
    new_repo_grace_days: int = 90

    # ── Caching ──
    cache_ttl_dead: int = 259200  # 3 days  (merges=0)
    cache_ttl_low: int = 43200    # 12 hours (merges 1-2)
    cache_ttl_active: int = 7200  # 2 hours  (merges >= 3)
    no_cache: bool = False

    # ── Concurrency ──
    semaphore_limit: int = 15
    token_bucket_capacity: int = 500
    token_bucket_fill_rate: float = 10.0
    batch_commit_size: int = 25

    # ── Scoring weights ──
    weight_amount: float = 0.35
    weight_recency: float = 0.25
    weight_activity: float = 0.20
    weight_escrow_strength: float = 0.20

    # ── Output ──
    output_format: str = "text"  # text | markdown | json
    dry_run: bool = False
    verbose: bool = False
    output_md_file: str = "output.md"
    output_json_file: str = "output.json"

    # ── Filtering behaviour ──
    allow_assigned_if_stale: bool = True

    # ── Paths ──
    db_file: str = "bounty_stats.db"
    signals_config_file: str = DEFAULT_SIGNALS_FILE
    config_file: str = DEFAULT_CONFIG_FILE

    # ── Search queries (loaded from config JSON) ──
    search_queries: list[str] = field(default_factory=list)

    # ── Progress ──
    progress_every: int = 20


# ─── GitHub token resolution ────────────────────────────────────────
def resolve_github_token() -> str:
    """Return a GitHub PAT from the CLI tool or environment variables."""
    try:
        res = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        token = res.stdout.strip()
        if token:
            return token
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GITHUB_PAT")
        or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        or ""
    )


# ─── Signal config loader ───────────────────────────────────────────
def load_signals(path: str = DEFAULT_SIGNALS_FILE) -> dict[str, list[str]]:
    """Load signal keyword lists from an external JSON file.

    All signal strings are lowercased at load time for case-insensitive
    matching downstream.

    Falls back to empty lists if the file is missing or malformed.
    """
    log = get_logger()
    defaults: dict[str, list[str]] = {
        "positive_escrow": [],
        "negative_filters": [],
        "stale_signals": [],
        "active_signals": [],
        "kill_labels": [],
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        for key in defaults:
            if key in data and isinstance(data[key], list):
                defaults[key] = [s.lower() for s in data[key]]
        return defaults
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load %s: %s — using empty defaults.", path, exc)
        return defaults


# ─── Config file loader ─────────────────────────────────────────────
def load_config_file(path: str) -> dict[str, Any]:
    """Load the top-level scraper config JSON.  Returns ``{}`` on error."""
    log = get_logger()
    if not os.path.exists(path):
        log.debug("Config file %s not found — using defaults.", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load config %s: %s", path, exc)
        return {}


def build_config(cli_overrides: dict[str, Any] | None = None) -> ScraperConfig:
    """Assemble a ``ScraperConfig`` from defaults → config file → CLI.

    Precedence: CLI flags > config file > dataclass defaults.
    """
    cfg = ScraperConfig()

    # 1. Determine config file path (CLI may override).
    config_path = (cli_overrides or {}).get("config_file", DEFAULT_CONFIG_FILE)

    # 2. Load config file and apply.
    file_data = load_config_file(config_path)
    for key, value in file_data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    # 3. Apply CLI overrides (non-None values only).
    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)

    # 4. Resolve token if not already set.
    if not cfg.github_token:
        cfg.github_token = resolve_github_token()
    if not cfg.github_token:
        print("Error: No valid token available in GitHub CLI or environment variables.")
        sys.exit(1)

    return cfg
