"""
Argparse-based CLI for the GitHub Bounty Scraper.
"""

from __future__ import annotations

import argparse

from .config import ScraperConfig, build_config
from .log import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-bounty-scraper",
        description="Discover and score funded crypto bounties on GitHub Issues.",
    )

    # ── Discovery ──
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        metavar="LANG",
        help="Filter by programming language (repeatable, e.g. --language Python --language TypeScript).",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        default=None,
        metavar="N",
        help="Minimum repo star count for search queries (default: 10).",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only consider issues updated on or after this date.",
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        default=None,
        metavar="N",
        help="Hard upper bound on total issues processed this run (default: unlimited).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        dest="max_pages_per_query",
        metavar="N",
        help="Max pages to fetch per search query (default: 5).",
    )

    # ── Thresholds ──
    parser.add_argument(
        "--min-amount",
        type=float,
        default=None,
        dest="min_bounty_amount",
        metavar="USD",
        help="Override minimum bounty amount threshold (default: $10).",
    )

    # ── Behaviour ──
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        dest="dry_run",
        help="Run the pipeline without writing to the database.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=None,
        dest="no_cache",
        help="Skip cache checks — re-enrich every issue.",
    )
    parser.add_argument(
        "--allow-assigned-if-stale",
        action="store_true",
        default=None,
        dest="allow_assigned_if_stale",
        help="Include assigned issues when the assignment looks stale.",
    )

    # ── Output ──
    parser.add_argument(
        "--output-format",
        choices=["text", "markdown", "json"],
        default=None,
        dest="output_format",
        help="Output format (default: text). Markdown and JSON also write files.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=None,
        help="Enable DEBUG-level logging.",
    )

    # ── Config file ──
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        dest="config_file",
        metavar="PATH",
        help="Path to scraper_config.json (default: ./scraper_config.json).",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> ScraperConfig:
    """Parse CLI arguments and build a merged ``ScraperConfig``.

    Precedence: CLI flags > config file > dataclass defaults.
    """
    parser = _build_parser()
    ns = parser.parse_args(argv)

    # Collect only explicitly-set values so build_config can distinguish
    # "user didn't pass this flag" from "user passed the default value".
    overrides: dict = {}
    for key, value in vars(ns).items():
        if value is not None:
            overrides[key] = value

    config = build_config(overrides)

    # Logging must be set up before anything else logs.
    setup_logging(config.verbose)

    return config
