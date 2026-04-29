"""
Argparse-based CLI for the GitHub Bounty Scraper.
"""

from __future__ import annotations

import argparse

from .config import ScraperConfig, build_config
from .log import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    # argument_default=SUPPRESS ensures unprovided args are absent from the
    # namespace entirely, so we can distinguish "not passed" from "passed
    # with a falsy value" (e.g. --dry-run is store_true → True, but if the
    # user never passed it, it won't appear in vars(ns)).
    parser = argparse.ArgumentParser(
        prog="github-bounty-scraper",
        description="Discover and score funded crypto bounties on GitHub Issues.",
        argument_default=argparse.SUPPRESS,
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
        metavar="N",
        help="Minimum repo star count for search queries (default: 10).",
    )
    parser.add_argument(
        "--since",
        type=str,
        metavar="YYYY-MM-DD",
        help="Only consider issues updated on or after this date.",
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        metavar="N",
        help="Hard upper bound on total issues processed this run (default: unlimited).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        dest="max_pages_per_query",
        metavar="N",
        help="Max pages to fetch per search query (default: 5).",
    )

    # ── Thresholds ──
    parser.add_argument(
        "--min-amount",
        type=float,
        dest="min_bounty_amount",
        metavar="USD",
        help="Override minimum bounty amount threshold (default: $25).",
    )

    # ── Behaviour ──
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Run the pipeline without writing to the database.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        dest="no_cache",
        help="Skip cache checks — re-enrich every issue.",
    )
    parser.add_argument(
        "--allow-assigned-if-stale",
        action="store_true",
        dest="allow_assigned_if_stale",
        help="Include assigned issues when the assignment looks stale.",
    )

    # ── Output ──
    parser.add_argument(
        "--output-format",
        choices=["text", "markdown", "json"],
        dest="output_format",
        help="Output format (default: text). Markdown and JSON also write files.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    # ── Config file ──
    parser.add_argument(
        "--config",
        type=str,
        dest="config_file",
        metavar="PATH",
        help="Path to scraper_config.json (default: ./scraper_config.json).",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> ScraperConfig:
    """Parse CLI arguments and build a merged ``ScraperConfig``.

    Uses ``argument_default=SUPPRESS`` so that only flags the user
    explicitly passed appear in the namespace.  This avoids the boolean
    override bug where ``store_true`` defaults (``False``) would shadow
    config-file values via the ``if value is not None`` guard.

    Precedence: CLI flags > config file > dataclass defaults.
    """
    parser = _build_parser()
    ns = parser.parse_args(argv)

    # vars(ns) now contains ONLY keys the user explicitly provided.
    overrides = vars(ns)

    config = build_config(overrides)

    # Logging must be set up before anything else logs.
    setup_logging(config.verbose)

    return config
