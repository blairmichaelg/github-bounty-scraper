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
    """GitHub Personal Access Token.  Required for GraphQL API calls."""

    # ── Search / discovery ──
    languages: list[str] = field(default_factory=list)
    """Filter by programming language.  Repeatable.  Default: []."""

    min_stars: int = 5
    """Minimum repository star count.  Issues from repos below this
    threshold are hard-disqualified in strict mode (ignored in
    opportunistic mode).  Range: 0–∞.  Default: 5."""

    since: str = ""  # YYYY-MM-DD
    """Only consider issues updated on or after this date.  Default: ''."""

    max_issues: int = 0  # 0 = unlimited
    """Hard upper bound on total issues processed per run.  Default: 0."""

    max_pages_per_query: int = 5
    """Max pages to fetch per search query.  Default: 5."""

    sort_by: str = "updated"
    """GitHub search sort criteria.  Default: 'updated'."""

    max_expanded_queries: int = 40
    """Limit on the number of generated search queries.  Default: 40."""

    search_delay_seconds: float = 1.0
    """Delay between search queries to avoid rate limits.  Default: 1.0."""

    # ── Thresholds ──
    min_bounty_amount: float = 25.0
    """Override minimum bounty amount threshold (strict).  Default: 25.0."""

    max_sane_amount: float = 1e7
    """Upper sanity bound for bounty amounts (default: $10M)."""

    new_repo_grace_days: int = 90
    """Grace period before dead-repo check.  Default: 90."""

    # ── Caching ──
    cache_ttl_dead: int = 259200  # 3 days  (merges=0)
    """Cache TTL for dead repos.  Default: 3 days."""

    cache_ttl_low: int = 43200    # 12 hours (merges 1-2)
    """Cache TTL for low-activity repos.  Default: 12 hours."""

    cache_ttl_active: int = 7200  # 2 hours  (merges >= 3)
    """Cache TTL for active repos.  Default: 2 hours."""

    no_cache: bool = False
    """Skip cache checks — re-enrich every issue.  Default: False."""

    # ── Concurrency ──
    semaphore_limit: int = 15
    """Max concurrent GraphQL enrichments.  Default: 15."""

    token_bucket_capacity: int = 500
    """Token bucket capacity for rate limiting.  Default: 500."""

    token_bucket_fill_rate: float = 10.0
    """Token bucket fill rate (tokens/sec).  Default: 10.0."""

    batch_commit_size: int = 25
    """Number of DB ops before commit.  Default: 25."""

    # ── Scoring weights ──
    weight_amount: float = 0.4
    """Weight for bounty amount in score calculation.  Default: 0.4."""

    weight_recency: float = 0.25
    """Weight for issue recency.  Default: 0.25."""

    weight_activity: float = 0.20
    """Weight for repo activity.  Default: 0.20."""

    weight_escrow_strength: float = 0.15
    """Weight for escrow signal strength.  Default: 0.15."""

    # ── Output ──
    output_format: str = "text"  # text | markdown | json
    """Output format.  Default: 'text'."""

    dry_run: bool = False
    """Run pipeline without writing to DB.  Default: False."""

    verbose: bool = False
    """Enable DEBUG-level logging.  Default: False."""

    output_md_file: str = "output.md"
    """Path to Markdown report.  Default: 'output.md'."""

    output_json_file: str = "output.json"
    """Path to JSON report.  Default: 'output.json'."""

    output_file: str = ""  # Base name for output files (e.g. 'results' -> results.md, results.json)
    """Base name for output files.  Default: ''."""

    # ── Filtering behaviour ──
    allow_assigned_if_stale: bool = True
    """Include assigned issues when assignment is stale.  Default: True."""

    active_signal_max_age_days: int = 90
    """Max age for active claim signals.  Default: 90 days."""

    proximity_window: int = 300
    """Window size for proximity scoring.  Default: 300."""

    # ── GraphQL pagination ──
    pr_cap: int = 200
    """Limit on PRs fetched per repo.  Default: 200."""

    tl_max_pages: int = 5
    """Max pages for timeline items.  Default: 5."""

    # ── Paths ──
    db_file: str = "bounty_stats.db"
    """Path to SQLite database.  Default: 'bounty_stats.db'."""

    signals_config_file: str = DEFAULT_SIGNALS_FILE
    """Path to signals config.  Default: 'signals_config.json'."""

    config_file: str = DEFAULT_CONFIG_FILE
    """Path to main scraper config.  Default: 'scraper_config.json'."""

    # ── Search queries (loaded from config JSON) ──
    search_queries: list[str] = field(default_factory=list)
    """List of search query strings.  Default: []."""

    # ── Progress ──
    progress_every: int = 20
    """Report progress every N issues.  Default: 20."""

    # ── Exploration / Runtime Mode ──
    mode: str = "strict"  # "strict" or "opportunistic"
    """Runtime mode.  Default: 'strict'."""

    log_raw_candidates: bool = False
    """Log rejected candidates to raw file.  Default: False."""
    
    opportunistic_allow_dead_repos: bool = True
    """Allow dead repos in opportunistic mode.  Default: True."""

    opportunistic_allow_no_escrow: bool = True
    """Allow no escrow signals if cue present.  Default: True."""

    opportunistic_min_amount: float = 10.0
    """Minimum amount for opportunistic leads.  Default: 10.0."""

    exploration_min_stars_raw: int = 1
    """Min stars for exploration logging.  Default: 1."""


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
    # 1. Determine config file path (CLI may override).
    overrides = cli_overrides or {}
    config_path = overrides.get("config_file", DEFAULT_CONFIG_FILE)

    # 2. Load config file and apply.
    data = load_config_file(config_path)
    
    from dataclasses import fields as dc_fields
    known = {f.name for f in dc_fields(ScraperConfig)}
    
    if data:
        unknown = set(data) - known
        if unknown:
            import warnings
            warnings.warn(
                f"scraper_config.json contains unrecognized keys (will be ignored): {unknown}",
                stacklevel=2,
            )
        data = {k: v for k, v in data.items() if k in known}

    # 3. Apply CLI overrides.
    cli_data = {}
    cli_unknown = set()
    for k, v in overrides.items():
        if k in known:
            cli_data[k] = v
        else:
            # config_file is a known override but not in ScraperConfig
            if k != "config_file":
                cli_unknown.add(k)
    
    if cli_unknown:
        import warnings
        warnings.warn(
            f"CLI provided unrecognized keys (will be ignored): {cli_unknown}",
            stacklevel=2,
        )

    # Merge: defaults (in dataclass) < config file < CLI
    combined = {**data, **cli_data}
    cfg = ScraperConfig(**combined)

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
