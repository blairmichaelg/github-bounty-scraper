"""
Argparse-based CLI for the GitHub Bounty Scraper.
"""

from __future__ import annotations

import argparse
import datetime

from .config import ScraperConfig, build_config
from .log import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    # argument_default=SUPPRESS ensures unprovided args are absent from the
    # namespace entirely, so we can distinguish "not passed" from "passed
    # with a falsy value" (e.g. --dry-run is store_true → True, but if the
    # user never passed it, it won't appear in vars(ns)).
    main_parser = argparse.ArgumentParser(
        prog="github-bounty-scraper",
        description="Discover and score funded crypto bounties on GitHub Issues.",
        argument_default=argparse.SUPPRESS,
    )

    main_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    
    _DEFAULT_SINCE = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    
    subparsers = main_parser.add_subparsers(dest="command", required=True)
    
    # ── Scrape Command ──
    parser = subparsers.add_parser("scrape", help="Run the scraper pipeline", argument_default=argparse.SUPPRESS)

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
        default=_DEFAULT_SINCE,
        metavar="YYYY-MM-DD",
        help="Only consider issues updated on or after this date (default: 90 days ago, %(default)s).",
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
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        dest="output_file",
        metavar="PATH",
        help="Base name for output files (e.g. 'results' -> results.md, results.json). Only written if this flag is passed.",
    )

    parser.add_argument(
        "--mode",
        choices=["strict", "opportunistic"],
        dest="mode",
        help="Runtime mode: strict (default) or opportunistic.",
    )
    parser.add_argument(
        "--log-raw-candidates",
        action="store_true",
        dest="log_raw_candidates",
        help="Log raw candidate issues to exploration_raw.jsonl",
    )

    # ── Config file ──
    parser.add_argument(
        "--config",
        type=str,
        dest="config_file",
        metavar="PATH",
        help="Path to scraper_config.json (default: ./scraper_config.json).",
    )

    # ── Inspect Leads Command ──
    inspect_parser = subparsers.add_parser("inspect-leads", help="Inspect recently saved leads")
    inspect_parser.add_argument("--mode", choices=["strict", "opportunistic", "all"], default="strict", help="Filter by lead mode")
    inspect_parser.add_argument("--limit", type=int, default=20, help="Number of leads to show")
    inspect_parser.add_argument(
        "--db-path",
        type=str,
        default="bounty_stats.db",
        help="Path to the SQLite DB (default: bounty_stats.db)",
    )

    # ── Vibe Check Command ──
    vibe_parser = subparsers.add_parser(
        "vibe-check",
        help="Run LLM-based vibe checks over exploration_raw.jsonl",
    )
    vibe_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of raw candidates to score (default: 100).",
    )
    vibe_parser.add_argument(
        "--mode",
        choices=["all", "unscored"],
        default="unscored",
        help=(
            "Which candidates to score: "
            "'unscored' only issues without vibe_score in the DB, "
            "'all' scores every raw candidate up to --limit."
        ),
    )
    vibe_parser.add_argument(
        "--raw-file",
        type=str,
        default="exploration_raw.jsonl",
        help="Path to exploration_raw.jsonl (default: ./exploration_raw.jsonl).",
    )
    vibe_parser.add_argument(
        "--db-path",
        type=str,
        default="bounty_stats.db",
        help="Path to the SQLite DB used for storing vibe scores (default: bounty_stats.db).",
    )
    vibe_parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent Gemini API calls (default: 5).",
    )

    # ── Dump Dataset Command ──
    dump_parser = subparsers.add_parser(
        "dump-dataset",
        help="Export issue and repo stats to CSV for fine-tuning."
    )
    dump_parser.add_argument(
        "--db-path",
        type=str,
        default="bounty_stats.db",
        help="Path to the SQLite DB (default: bounty_stats.db)"
    )
    dump_parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Path to the output CSV file (e.g. bounty_dataset.csv)"
    )
    dump_parser.add_argument(
        "--raw-file",
        type=str,
        default="exploration_raw.jsonl",
        dest="raw_file",
        help="Path to exploration_raw.jsonl used to enrich CSV with body text (default: ./exploration_raw.jsonl).",
    )

    return main_parser


def parse_args(argv: list[str] | None = None) -> tuple[str, argparse.Namespace, ScraperConfig]:
    """Parse CLI arguments. Returns (command, namespace, ScraperConfig)."""
    parser = _build_parser()
    ns = parser.parse_args(argv)
    
    # vars(ns) now contains ONLY keys the user explicitly provided.
    overrides = dict(vars(ns))
    overrides.pop("command", None)

    # build_config handles this by ignoring unknown keys.
    config = build_config(overrides)

    # Logging must be set up before anything else logs.
    setup_logging(config.verbose)

    return ns.command, ns, config
