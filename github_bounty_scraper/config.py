"""
Configuration management — loads settings from JSON config, CLI args, and
hard-coded defaults.  CLI flags always override the config file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

# Ensure .env variables are loaded.
from .log import get_logger

load_dotenv()

# ─── Defaults ────────────────────────────────────────────────────────
DEFAULT_CONFIG_FILE = "scraper_config.json"
DEFAULT_SIGNALS_FILE = "signals_config.json"

ESCROW_WEIGHT_CAP: float = 5.0
ACTIVITY_TRUST_THRESHOLD: int = 40
ACTIVITY_TRUST_FLOOR: float = 0.4


@dataclass
class SearchConfig:
    """Configuration for discovery and GitHub search."""

    languages: list[str] = field(default_factory=list)
    min_repo_stars: int = 5
    repo_blocklist: list[str] = field(default_factory=list)
    since: str = ""
    query_override: str | None = None
    max_issues_per_run: int = 1000
    max_pages_per_query: int = 5
    sort_by: str = "updated"
    max_expanded_queries: int = 40
    search_delay_seconds: float = 1.0
    search_queries: list[str] = field(default_factory=list)
    pr_cap: int = 200
    tl_max_pages: int = 5
    timeline_page_size: int = 25


@dataclass
class ScoringConfig:
    """Configuration for the composite scoring formula."""

    weight_amount: float = 0.30
    weight_recency: float = 0.10
    weight_activity: float = 0.15
    weight_escrow_strength: float = 0.15
    w_repo_reputation: float = 0.10
    weight_vibe: float = 0.20
    weight_model: float = 0.0  # Default to 0.0 unless model is enabled
    hardware_penalty_factor: float = 0.5
    min_bounty_amount: float = 25.0
    amount_norm_cap: float = 100_000.0
    max_sane_amount: float = 1e7
    new_repo_grace_days: int = 90
    allow_assigned_if_stale: bool = True
    active_signal_max_age_days: int = 90
    proximity_window: int = 300
    vibe_ttl_hours: int = 480
    opportunistic_min_amount: float = 50.0
    exploration_min_stars_raw: int = 100
    opportunistic_allow_no_escrow: bool = True
    opportunistic_allow_dead_repos: bool = True


@dataclass
class ConcurrencyConfig:
    """Configuration for concurrency and rate limiting."""

    semaphore_limit: int = 15
    token_bucket_capacity: int = 500
    token_bucket_fill_rate: float = 10.0
    db_batch_size: int = 50
    vibe_check_concurrency: int = 3


@dataclass
class CacheConfig:
    """Configuration for SQLite and repository caching."""

    cache_ttl_dead: int = 259200
    cache_ttl_low: int = 43200
    cache_ttl_active: int = 7200
    db_file: str = "bounty_stats.db"


@dataclass
class OutputConfig:
    """Configuration for reports and candidate logging."""

    output_format: str = "text"
    output_md_file: str = "output.md"
    output_json_file: str = "output.json"
    output_file: str = ""
    raw_candidates_file: str = field(
        default_factory=lambda: os.environ.get("RAW_CANDIDATES_FILE", "exploration_raw.jsonl")
    )
    progress_every: int = 20
    verbose: bool = False
    log_raw_candidates: bool = False


@dataclass
class ScraperConfig:
    """Main configuration orchestrator."""

    github_token: str = ""
    mode: str = "strict"
    dry_run: bool = False
    enable_vibe: bool = True
    vibe_check_enabled: bool = False
    enable_live_prices: bool = False
    live_price_timeout_seconds: int = 5
    signals_config_file: str = DEFAULT_SIGNALS_FILE
    config_file: str = DEFAULT_CONFIG_FILE
    gemini_model: str = "gemini-1.5-flash"
    vibe_retry_file: str = "vibe_retry.json"

    # Sub-configs
    search: SearchConfig = field(default_factory=SearchConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @property
    def languages(self):
        return self.search.languages

    @languages.setter
    def languages(self, val):
        self.search.languages = val

    @property
    def min_repo_stars(self):
        return self.search.min_repo_stars

    @min_repo_stars.setter
    def min_repo_stars(self, val):
        self.search.min_repo_stars = val

    @property
    def repo_blocklist(self):
        return self.search.repo_blocklist

    @repo_blocklist.setter
    def repo_blocklist(self, val):
        self.search.repo_blocklist = val

    @property
    def since(self):
        return self.search.since

    @since.setter
    def since(self, val):
        self.search.since = val

    @property
    def query_override(self):
        return self.search.query_override

    @query_override.setter
    def query_override(self, val):
        self.search.query_override = val

    @property
    def max_issues_per_run(self):
        return self.search.max_issues_per_run

    @max_issues_per_run.setter
    def max_issues_per_run(self, val):
        self.search.max_issues_per_run = val

    @property
    def max_pages_per_query(self):
        return self.search.max_pages_per_query

    @max_pages_per_query.setter
    def max_pages_per_query(self, val):
        self.search.max_pages_per_query = val

    @property
    def sort_by(self):
        return self.search.sort_by

    @sort_by.setter
    def sort_by(self, val):
        self.search.sort_by = val

    @property
    def max_expanded_queries(self):
        return self.search.max_expanded_queries

    @max_expanded_queries.setter
    def max_expanded_queries(self, val):
        self.search.max_expanded_queries = val

    @property
    def search_delay_seconds(self):
        return self.search.search_delay_seconds

    @search_delay_seconds.setter
    def search_delay_seconds(self, val):
        self.search.search_delay_seconds = val

    @property
    def search_queries(self):
        return self.search.search_queries

    @search_queries.setter
    def search_queries(self, val):
        self.search.search_queries = val

    @property
    def pr_cap(self):
        return self.search.pr_cap

    @pr_cap.setter
    def pr_cap(self, val):
        self.search.pr_cap = val

    @property
    def tl_max_pages(self):
        return self.search.tl_max_pages

    @tl_max_pages.setter
    def tl_max_pages(self, val):
        self.search.tl_max_pages = val

    @property
    def timeline_page_size(self):
        return self.search.timeline_page_size

    @timeline_page_size.setter
    def timeline_page_size(self, val):
        self.search.timeline_page_size = val

    @property
    def weight_amount(self):
        return self.scoring.weight_amount

    @weight_amount.setter
    def weight_amount(self, val):
        self.scoring.weight_amount = val

    @property
    def weight_recency(self):
        return self.scoring.weight_recency

    @weight_recency.setter
    def weight_recency(self, val):
        self.scoring.weight_recency = val

    @property
    def weight_activity(self):
        return self.scoring.weight_activity

    @weight_activity.setter
    def weight_activity(self, val):
        self.scoring.weight_activity = val

    @property
    def weight_escrow_strength(self):
        return self.scoring.weight_escrow_strength

    @weight_escrow_strength.setter
    def weight_escrow_strength(self, val):
        self.scoring.weight_escrow_strength = val

    @property
    def w_repo_reputation(self):
        return self.scoring.w_repo_reputation

    @w_repo_reputation.setter
    def w_repo_reputation(self, val):
        self.scoring.w_repo_reputation = val

    @property
    def weight_vibe(self):
        return self.scoring.weight_vibe

    @weight_vibe.setter
    def weight_vibe(self, val):
        self.scoring.weight_vibe = val

    @property
    def hardware_penalty_factor(self):
        return self.scoring.hardware_penalty_factor

    @hardware_penalty_factor.setter
    def hardware_penalty_factor(self, val):
        self.scoring.hardware_penalty_factor = val

    @property
    def min_bounty_amount(self):
        return self.scoring.min_bounty_amount

    @min_bounty_amount.setter
    def min_bounty_amount(self, val):
        self.scoring.min_bounty_amount = val

    @property
    def amount_norm_cap(self):
        return self.scoring.amount_norm_cap

    @amount_norm_cap.setter
    def amount_norm_cap(self, val):
        self.scoring.amount_norm_cap = val

    @property
    def max_sane_amount(self):
        return self.scoring.max_sane_amount

    @max_sane_amount.setter
    def max_sane_amount(self, val):
        self.scoring.max_sane_amount = val

    @property
    def new_repo_grace_days(self):
        return self.scoring.new_repo_grace_days

    @new_repo_grace_days.setter
    def new_repo_grace_days(self, val):
        self.scoring.new_repo_grace_days = val

    @property
    def allow_assigned_if_stale(self):
        return self.scoring.allow_assigned_if_stale

    @allow_assigned_if_stale.setter
    def allow_assigned_if_stale(self, val):
        self.scoring.allow_assigned_if_stale = val

    @property
    def active_signal_max_age_days(self):
        return self.scoring.active_signal_max_age_days

    @active_signal_max_age_days.setter
    def active_signal_max_age_days(self, val):
        self.scoring.active_signal_max_age_days = val

    @property
    def proximity_window(self):
        return self.scoring.proximity_window

    @proximity_window.setter
    def proximity_window(self, val):
        self.scoring.proximity_window = val

    @property
    def vibe_ttl_hours(self):
        return self.scoring.vibe_ttl_hours

    @vibe_ttl_hours.setter
    def vibe_ttl_hours(self, val):
        self.scoring.vibe_ttl_hours = val

    @property
    def opportunistic_min_amount(self):
        return self.scoring.opportunistic_min_amount

    @opportunistic_min_amount.setter
    def opportunistic_min_amount(self, val):
        self.scoring.opportunistic_min_amount = val

    @property
    def exploration_min_stars_raw(self):
        return self.scoring.exploration_min_stars_raw

    @exploration_min_stars_raw.setter
    def exploration_min_stars_raw(self, val):
        self.scoring.exploration_min_stars_raw = val

    @property
    def opportunistic_allow_no_escrow(self):
        return self.scoring.opportunistic_allow_no_escrow

    @opportunistic_allow_no_escrow.setter
    def opportunistic_allow_no_escrow(self, val):
        self.scoring.opportunistic_allow_no_escrow = val

    @property
    def opportunistic_allow_dead_repos(self):
        return self.scoring.opportunistic_allow_dead_repos

    @opportunistic_allow_dead_repos.setter
    def opportunistic_allow_dead_repos(self, val):
        self.scoring.opportunistic_allow_dead_repos = val

    @property
    def semaphore_limit(self):
        return self.concurrency.semaphore_limit

    @semaphore_limit.setter
    def semaphore_limit(self, val):
        self.concurrency.semaphore_limit = val

    @property
    def token_bucket_capacity(self):
        return self.concurrency.token_bucket_capacity

    @token_bucket_capacity.setter
    def token_bucket_capacity(self, val):
        self.concurrency.token_bucket_capacity = val

    @property
    def token_bucket_fill_rate(self):
        return self.concurrency.token_bucket_fill_rate

    @token_bucket_fill_rate.setter
    def token_bucket_fill_rate(self, val):
        self.concurrency.token_bucket_fill_rate = val

    @property
    def db_batch_size(self):
        return self.concurrency.db_batch_size

    @db_batch_size.setter
    def db_batch_size(self, val):
        self.concurrency.db_batch_size = val

    @property
    def vibe_check_concurrency(self):
        return self.concurrency.vibe_check_concurrency

    @vibe_check_concurrency.setter
    def vibe_check_concurrency(self, val):
        self.concurrency.vibe_check_concurrency = val

    @property
    def cache_ttl_dead(self):
        return self.cache.cache_ttl_dead

    @cache_ttl_dead.setter
    def cache_ttl_dead(self, val):
        self.cache.cache_ttl_dead = val

    @property
    def cache_ttl_low(self):
        return self.cache.cache_ttl_low

    @cache_ttl_low.setter
    def cache_ttl_low(self, val):
        self.cache.cache_ttl_low = val

    @property
    def cache_ttl_active(self):
        return self.cache.cache_ttl_active

    @cache_ttl_active.setter
    def cache_ttl_active(self, val):
        self.cache.cache_ttl_active = val

    @property
    def db_file(self):
        return self.cache.db_file

    @db_file.setter
    def db_file(self, val):
        self.cache.db_file = val

    @property
    def output_format(self):
        return self.output.output_format

    @output_format.setter
    def output_format(self, val):
        self.output.output_format = val

    @property
    def output_md_file(self):
        return self.output.output_md_file

    @output_md_file.setter
    def output_md_file(self, val):
        self.output.output_md_file = val

    @property
    def output_json_file(self):
        return self.output.output_json_file

    @output_json_file.setter
    def output_json_file(self, val):
        self.output.output_json_file = val

    @property
    def output_file(self):
        return self.output.output_file

    @output_file.setter
    def output_file(self, val):
        self.output.output_file = val

    @property
    def raw_candidates_file(self):
        return self.output.raw_candidates_file

    @raw_candidates_file.setter
    def raw_candidates_file(self, val):
        self.output.raw_candidates_file = val

    @property
    def progress_every(self):
        return self.output.progress_every

    @progress_every.setter
    def progress_every(self, val):
        self.output.progress_every = val

    @property
    def verbose(self):
        return self.output.verbose

    @verbose.setter
    def verbose(self, val):
        self.output.verbose = val

    @property
    def log_raw_candidates(self):
        return self.output.log_raw_candidates

    @log_raw_candidates.setter
    def log_raw_candidates(self, val):
        self.output.log_raw_candidates = val

    # These don't have sub-configs but are often passed via CLI
    no_cache: bool = False
    include_closed_for_training: bool = False
    limit: int = 10
    top_n: int = 20

    def __post_init__(self) -> None:
        # Check for pathological weights
        weights = [
            self.scoring.weight_amount,
            self.scoring.weight_recency,
            self.scoring.weight_activity,
            self.scoring.weight_escrow_strength,
            self.scoring.w_repo_reputation,
            self.scoring.weight_vibe,
            self.scoring.weight_model,
        ]
        if any(w < 0 for w in weights):
            raise ValueError("Scoring weights cannot be negative.")

        weight_total = sum(weights)
        if weight_total <= 0:
            raise ValueError("Scoring weights must sum to a positive number.")

        if not (0.5 <= weight_total <= 2.0):
            raise ValueError(f"Scoring weights sum to {weight_total:.4f}, which is outside sane bounds [0.5, 2.0].")

        if abs(weight_total - 1.0) > 0.001:
            import warnings

            warnings.warn(
                f"Scoring weights sum to {weight_total:.4f}, not 1.0. Normalizing automatically.",
                stacklevel=2,
            )
            # Normalize in-place
            self.scoring.weight_amount /= weight_total
            self.scoring.weight_recency /= weight_total
            self.scoring.weight_activity /= weight_total
            self.scoring.weight_escrow_strength /= weight_total
            self.scoring.w_repo_reputation /= weight_total
            self.scoring.weight_vibe /= weight_total
            self.scoring.weight_model /= weight_total

    def __repr__(self) -> str:
        import dataclasses

        d = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        if d.get("github_token"):
            d["github_token"] = "ghp_***REDACTED***"
        return f"ScraperConfig({d})"

    __str__ = __repr__


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

    # Fallback to GitHub CLI if available and explicitly allowed or in developer mode
    if os.environ.get("USE_GH_CLI", "1") == "1":
        gh_path = shutil.which("gh")
        if gh_path:
            try:
                res = subprocess.run(
                    [gh_path, "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                token = res.stdout.strip()
                if token:
                    return token
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                pass
    return ""


# ─── Signal config loader ───────────────────────────────────────────
def load_signals(path: str = DEFAULT_SIGNALS_FILE) -> dict[str, list[str] | list[dict[str, Any]] | Any]:
    """Load signal keyword lists from an external JSON file.

    All signal strings are lowercased at load time for case-insensitive
    matching downstream. Pre-compiles regular expressions for performance.

    Falls back to empty lists if the file is missing or malformed.
    """
    import re

    log = get_logger()
    defaults: dict[str, Any] = {
        "positive_escrow": [],
        "negative_filters": [],
        "stale_signals": [],
        "active_signals": [],
        "kill_labels": [],
        "aggregator_repos": [],
        "active_label_signals": [],
        "soft_negative_signals": [],
        "no_kyc_phrases": [],
        "wallet_payout_phrases": [],
        "hardware_dependency_phrases": [],
        "completion_signals": [],
        "title_required_signals": [],
        "crypto_keywords": [],
        "stablecoin_symbols": [],
        "repo_blocklist": [],
        "snipe_phrases": [],
    }
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
            for key in list(defaults.keys()):
                if key in data and isinstance(data[key], list):
                    defaults[key] = [s.lower() for s in data[key]]
        else:
            log.debug("Signals file %s not found — using internal defaults.", path)
            # Add some hardcoded high-value defaults if file is missing
            defaults["positive_escrow"].extend(
                [
                    "escrow",
                    "safe multisig",
                    "payout",
                    "bounty",
                    "reward",
                    "hats finance",
                    "immunefi",
                    "algora",
                    "gitcoin",
                ]
            )
            defaults["crypto_keywords"].extend(["usdc", "usdt", "eth", "sol", "dai", "wbtc"])
            defaults["stablecoin_symbols"].extend(["usdc", "usdt", "dai"])

    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load %s: %s — using empty defaults.", path, exc)

    # Compile regexes for text matching optimization
    regex_keys = [
        "positive_escrow",
        "negative_filters",
        "stale_signals",
        "active_signals",
        "active_label_signals",
        "soft_negative_signals",
        "no_kyc_phrases",
        "wallet_payout_phrases",
        "hardware_dependency_phrases",
        "completion_signals",
        "title_required_signals",
        "snipe_phrases",
    ]
    for key in regex_keys:
        if defaults[key]:
            # Use \b for single words to avoid substring matches, but ONLY if they are alphanumeric.
            escaped_words = []
            for w in defaults[key]:
                esc = re.escape(w)
                # Only wrap in \b if word is alphanumeric at boundaries
                prefix = r"\b" if w[0].isalnum() else ""
                suffix = r"\b" if w[-1].isalnum() else ""
                escaped_words.append(f"{prefix}{esc}{suffix}")

            pattern = "|".join(escaped_words)
            defaults[f"{key}_re"] = re.compile(pattern, flags=re.IGNORECASE)
        else:
            defaults[f"{key}_re"] = None

    # Special regexes for bounty.py
    if defaults["crypto_keywords"]:
        # Match "NNN ETH", "NNN USDC", etc.
        pattern = r"([\d,]+(?:\.\d+)?)\s*(" + "|".join(re.escape(s) for s in defaults["crypto_keywords"]) + r")\b"
        defaults["crypto_amounts_re"] = re.compile(pattern, flags=re.IGNORECASE)

        # Match standalone keywords for fallback
        pattern = r"\b(" + "|".join(re.escape(s) for s in defaults["crypto_keywords"] if s.upper() != "USD") + r")\b"
        defaults["crypto_keywords_re"] = re.compile(pattern, flags=re.IGNORECASE)
    else:
        defaults["crypto_amounts_re"] = None
        defaults["crypto_keywords_re"] = None

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
    import dataclasses

    overrides = cli_overrides or {}
    config_path = overrides.get("config_file", DEFAULT_CONFIG_FILE)
    file_data = load_config_file(config_path)

    # 1. Start with base config
    cfg = ScraperConfig()

    def _map_to_sub(source: dict[str, Any], target_cfg: ScraperConfig):
        """Map flat dictionary keys to appropriate sub-configs."""
        # Mapping of keys to sub-config names
        mapping = {
            "search": {f.name for f in dataclasses.fields(SearchConfig)},
            "scoring": {f.name for f in dataclasses.fields(ScoringConfig)},
            "concurrency": {f.name for f in dataclasses.fields(ConcurrencyConfig)},
            "cache": {f.name for f in dataclasses.fields(CacheConfig)},
            "output": {f.name for f in dataclasses.fields(OutputConfig)},
        }

        # Key aliases for backward compatibility
        aliases = {
            "db_path": "db_file",
            "concurrency": "semaphore_limit",
            "raw_file": "raw_candidates_file",
            "batch_commit_size": "db_batch_size",
            "vibe_check_limit": "limit",
            "min_stars": "min_repo_stars",
        }

        # Root fields
        root_fields = {f.name for f in dataclasses.fields(ScraperConfig)}

        for k, v in list(source.items()):
            actual_k = aliases.get(k, k)

            # Check root fields first
            if actual_k in root_fields:
                setattr(target_cfg, actual_k, v)
                continue

            # Check sub-configs
            found = False
            for sub_name, fields in mapping.items():
                if actual_k in fields:
                    sub_obj = getattr(target_cfg, sub_name)
                    setattr(sub_obj, actual_k, v)
                    found = True
                    break

            if not found and k not in ["config_file", "command"]:
                import warnings
                warnings.warn(f"scraper_config.json contains unrecognized keys (will be ignored): {k}", UserWarning)
                pass

    # 2. Apply config file
    _map_to_sub(file_data, cfg)

    # 3. Apply CLI overrides
    _map_to_sub(overrides, cfg)

    # 4. Final resolution
    if not cfg.github_token:
        cfg.github_token = resolve_github_token()
    if not cfg.github_token:
        print("Error: No valid token available in GitHub CLI or environment variables.")
        sys.exit(1)

    return cfg
