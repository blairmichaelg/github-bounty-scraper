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
    max_expanded_queries: int = 40

    # ── Thresholds ──
    min_bounty_amount: float = 25.0
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
    active_signal_max_age_days: int = 90
    proximity_window: int = 300

    # ── GraphQL pagination ──
    pr_cap: int = 200
    tl_max_pages: int = 5

    # ── Paths ──
    db_file: str = "bounty_stats.db"
    signals_config_file: str = DEFAULT_SIGNALS_FILE
    config_file: str = DEFAULT_CONFIG_FILE

    # ── Search queries (loaded from config JSON) ──
    search_queries: list[str] = field(default_factory=list)

    # ── Progress ──
    progress_every: int = 20

    # ── Exploration / Runtime Mode ──
    mode: str = "strict"  # "strict" or "opportunistic"
    log_raw_candidates: bool = False
    
    opportunistic_allow_dead_repos: bool = True
    opportunistic_allow_no_escrow: bool = True
    opportunistic_min_amount: float = 10.0
    exploration_min_stars_raw: int = 1


# ─── GitHub token resolution ────────────────────────────────────────
def resolve_github_token() -> str:
    """Return a GitHub PAT — env vars checked first, then gh CLI fallback."""
    token = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GITHUB_PAT")
        or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        or ""
    )
    if token:
        return token
    try:
        res = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True,
            timeout=5,
        )
        token = res.stdout.strip()
        if token:
            return token
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


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
        "aggregator_repos": [],
        "active_label_signals": [],
        "soft_negative_signals": [],
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

    # 3. Apply CLI overrides.  With argument_default=SUPPRESS, every key
    #    present in cli_overrides was explicitly provided by the user —
    #    no additional None-guard is needed.
    if cli_overrides:
        for key, val in cli_overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, val)

    # ── Mode overrides ──
    if cfg.mode == "opportunistic":
        cfg.log_raw_candidates = True  # Auto-log raw candidates in opportunistic mode

    # 4. Resolve token if not already set.
    if not cfg.github_token:
        cfg.github_token = resolve_github_token()
    if not cfg.github_token:
        print("Error: No valid token available in GitHub CLI or environment variables.")
        sys.exit(1)

    # 5. Validate scoring weights.
    log = get_logger()
    total_weight = (
        cfg.weight_amount + cfg.weight_recency
        + cfg.weight_activity + cfg.weight_escrow_strength
    )
    if not (0.99 <= total_weight <= 1.01):
        log.warning(
            "Scoring weights sum to %.3f (expected 1.0). "
            "Scores may fall outside [0, 100].", total_weight
        )

    return cfg
