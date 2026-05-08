"""
Argparse-based CLI for the GitHub Bounty Scraper.
"""

from __future__ import annotations

import argparse
import datetime
from typing import overload

from .config import ScraperConfig, build_config
from .log import setup_logging


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    """Add options that should work before or after the scrape subcommand."""
    parser.add_argument(
        "--max-issues",
        type=int,
        dest="max_issues_per_run",
        metavar="N",
        help="Hard upper bound on total issues processed this run (default: 1000).",
    )
    parser.add_argument(
        "--min-amount",
        type=float,
        dest="min_bounty_amount",
        metavar="USD",
        help="Override minimum bounty amount threshold (default: $25).",
    )
    parser.add_argument(
        "--output",
        "--output-file",
        type=str,
        dest="output_file",
        metavar="PATH",
        help="Base name for output files (e.g. 'results' -> results.md, results.json).",
    )
    parser.add_argument(
        "--db",
        "--db-path",
        type=str,
        dest="db_path",
        metavar="PATH",
        help="Path to the SQLite DB.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Run without writing to the database.",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        dest="min_repo_stars",
        metavar="N",
        help="Minimum repo star count.",
    )


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
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    _DEFAULT_SINCE = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()

    _add_run_options(main_parser)
    main_parser.add_argument(
        "--top",
        "--top-n",
        type=int,
        dest="top_n",
        metavar="N",
        help="Number of leads to show.",
    )
    main_parser.add_argument(
        "--no-vibe",
        action="store_false",
        dest="enable_vibe",
        help="Disable LLM vibe checks.",
    )

    subparsers = main_parser.add_subparsers(dest="command", required=False)

    # ── Scrape Command ──
    parser = subparsers.add_parser("scrape", help="Run the scraper pipeline", argument_default=argparse.SUPPRESS)
    _add_run_options(parser)

    # ── Discovery ──
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        metavar="LANG",
        help="Filter by programming language (repeatable, e.g. --language Python --language TypeScript).",
    )
    parser.add_argument(
        "--query",
        type=str,
        dest="query_override",
        help="Override config queries with a single custom search query.",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=_DEFAULT_SINCE,
        metavar="YYYY-MM-DD",
        help="Only consider issues updated on or after this date (default: 90 days ago, %(default)s).",
    )
    parser.add_argument(
        "--auto-refresh", action="store_true", help="Skip scrape if newest lead is < --refresh-days old."
    )
    parser.add_argument(
        "--refresh-days",
        type=int,
        default=argparse.SUPPRESS,
        help="Age threshold in days for --auto-refresh. Default: 3.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        dest="max_pages_per_query",
        metavar="N",
        help="Max pages to fetch per search query (default: 5).",
    )

    # ── Behaviour ──
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
    parser.add_argument(
        "--include-closed-for-training",
        action="store_true",
        dest="include_closed_for_training",
        help="Allow enrichment of CLOSED issues for training data collection.",
    )

    # ── Output ──
    parser.add_argument(
        "--output-format",
        choices=["text", "markdown", "json"],
        dest="output_format",
        help="Output format (default: text).",
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
    parser.add_argument(
        "--vibe-check",
        action="store_true",
        dest="vibe_check_enabled",
        help="Run Gemini vibe checks on high-potential candidates during the scrape.",
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
    inspect_parser.add_argument(
        "--mode", choices=["strict", "opportunistic", "all"], default="strict", help="Filter by lead mode"
    )
    inspect_parser.add_argument("--limit", type=int, help="Number of leads to show (default from config)")
    inspect_parser.add_argument(
        "--db-path",
        type=str,
        default="bounty_stats.db",
        help="Path to the SQLite DB (default: bounty_stats.db)",
    )
    inspect_parser.add_argument(
        "--min-ml-prob",
        type=float,
        default=0.0,
        help="Filter leads by minimum ML probability [0.0, 1.0] (default: 0.0)",
    )

    # ── Vibe Check Command ──
    vibe_parser = subparsers.add_parser(
        "vibe-check",
        help="Run LLM-based vibe checks over exploration_raw.jsonl",
    )
    vibe_parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of raw candidates to score (default from config).",
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
        "--raw-candidates-file",
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
    dump_parser = subparsers.add_parser("dump-dataset", help="Export issue and repo stats to CSV for fine-tuning.")
    dump_parser.add_argument(
        "--db-path", type=str, default="bounty_stats.db", help="Path to the SQLite DB (default: bounty_stats.db)"
    )
    dump_parser.add_argument(
        "--out-csv", type=str, required=True, help="Path to the output CSV file (e.g. bounty_dataset.csv)"
    )
    dump_parser.add_argument(
        "--raw-candidates-file",
        type=str,
        default="exploration_raw.jsonl",
        dest="raw_candidates_file",
        help="Path to exploration_raw.jsonl used to enrich CSV with body text (default: ./exploration_raw.jsonl).",
    )
    dump_parser.add_argument(
        "--label-threshold",
        type=float,
        default=25.0,
        help="Minimum numeric_amount to label a row is_bounty=1. Default: 25.0.",
    )

    return main_parser


@overload
def parse_args(argv: None = None) -> tuple[str, argparse.Namespace, ScraperConfig]: ...


@overload
def parse_args(argv: list[str]) -> ScraperConfig: ...


def parse_args(argv: list[str] | None = None) -> tuple[str, argparse.Namespace, ScraperConfig] | ScraperConfig:
    """Parse CLI arguments. Returns (command, namespace, ScraperConfig) or just ScraperConfig if argv is not None."""
    parser = _build_parser()

    # Make command optional for tests or if user just wants defaults
    parser.set_defaults(command="scrape")
    # Actually, to avoid "required=True" failure:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            action.required = False

    ns = parser.parse_args(argv)

    # vars(ns) now contains ONLY keys the user explicitly provided.
    overrides = dict(vars(ns))
    command = overrides.pop("command", "scrape")

    # Filter out command-specific flags that aren't in ScraperConfig
    if command == "vibe-check":
        for k in ["limit", "concurrency", "db_path", "raw_candidates_file", "mode"]:
            overrides.pop(k, None)
    elif command == "inspect-leads":
        for k in ["mode", "limit", "db_path", "min_ml_prob"]:
            overrides.pop(k, None)
    elif command == "dump-dataset":
        for k in ["db_path", "out_csv", "raw_candidates_file", "label_threshold"]:
            overrides.pop(k, None)

    # build_config handles this by ignoring unknown keys.
    config = build_config(overrides)

    if config.max_issues_per_run < 0:
        parser.error("--max-issues must be non-negative.")

    # Logging must be set up before anything else logs.
    setup_logging(config.verbose)

    if argv is not None:
        return config
    return command, ns, config
