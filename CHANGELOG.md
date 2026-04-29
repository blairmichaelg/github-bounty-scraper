# Changelog

## [2.0.1] — 2026-04-29

### Fixed (Critical)
1. **pyproject.toml** — Corrected broken build backend from `setuptools.backends._legacy:_Backend` to `setuptools.build_meta`.
2. **CLI boolean override bug** — `store_true` flags (e.g. `--dry-run`, `--no-cache`) with `default=None` would shadow config-file values via the `if value is not None` guard. Fixed by using `argument_default=SUPPRESS` so unprovided flags are absent from the namespace entirely.
3. **Unguarded strptime in core.py** — The inline `datetime.strptime` for `last_updated_at` near the DB upsert was not wrapped in try/except. Now falls back to `0.0` on malformed timestamps.
4. **Fragile AssignedEvent detection** — Replaced key-based heuristic (`"source" not in node`) with explicit `__typename` checking. Added `__typename` to the GraphQL timelineItems query. Also updated `detect_snipe` to use `__typename` for `CrossReferencedEvent`/`ConnectedEvent`.
5. **first_seen_at overwritten on re-upsert** — Both `upsert_repo_stats` and `upsert_issue_stats` now use `COALESCE(table.first_seen_at, excluded.first_seen_at)` to preserve existing values and backfill NULLs from migration.

### Fixed (Medium)
6. **ISO string comparison for PR window** — Replaced string comparison `merged_at < forty_five_ago` with proper `datetime` comparison using `strptime`. Added comment explaining why the early-stop is safe.
7. **Markdown written on every run** — `write_markdown_output()` is now only called when `output_format == "markdown"`, not on `"text"` runs. Docstring updated.
8. **escrow_norm nearly always 0** — The old divisor (total signal list length, often 25+) made the escrow component negligible. New formula: `min(positive_escrow_count / 5.0, 1.0)` — 5+ distinct hits = full escrow score.
9. **Proximity window too tight** — Widened bounty keyword proximity window from 120 to 300 characters to better handle GitHub issue bodies where amounts may be far from keywords.
10. **subprocess.run for gh auth token** — Added `timeout=5` and `subprocess.TimeoutExpired` catch to prevent hanging on systems where `gh` waits for browser auth.

### Fixed (Polish)
11. **Aggregator repos hardcoded** — Moved `_AGGREGATOR_REPOS` set from `core.py` into `signals_config.json` as `"aggregator_repos"` key. Loaded via `config.load_signals()`.
12. **Version sync** — Replaced hardcoded `__version__ = "2.0.0"` with `importlib.metadata.version()` for single-source versioning. Falls back to `"dev"` when not installed.
13. **Stale requirements.txt** — Updated with version pins matching `pyproject.toml` and a comment documenting it as legacy.
14. **min_bounty_amount too low** — Raised default from $10 to $25 to filter "$10 gas refund" noise. Updated `scraper_config.json`, `config.py`, `cli.py`, and `README.md`.
15. **Console noise on JSON runs** — Added `suppress_console` parameter to `write_text_output()`. Set to `True` when `output_format == "json"` so JSON-only runs are clean.
16. **p.py --export json** — Added JSON export support (auto-detected by `.json` extension). Same field structure as the scraper's `output.json`.
17. **CI pip cache** — Added `actions/cache@v4` for pip. Added comment explaining GITHUB_TOKEN unavailability in CI.

## [2.0.0] — 2026-04-29

### Added

#### Discovery Layer
- Externalized search queries to `scraper_config.json` — add queries without code changes.
- Configurable pagination: `--max-pages` per query (default 5), early-stop on partial pages.
- Language filters: `--language Python --language TypeScript`.
- Stars filter: `--min-stars N` (default 10).
- Recency filter: `--since YYYY-MM-DD` injects `updated:>=` into queries.
- `--max-issues` hard cap on total issues processed per run.

#### GraphQL Enrichment
- PR pagination: scans up to 200 merged PRs (was 20) to accurately measure 45-day merge activity.
- Issue state check: drops CLOSED issues immediately after GraphQL fetch.
- Timeline items: now fetches 100 items (was 25) for better assignment/snipe detection.
- Issue `updatedAt` fetched and stored for cache validation and scoring.

#### Bounty Parsing
- Robust regex with proper thousand-separator handling (`$1,000.50`, `10,000 USDC`).
- Crypto denomination detection (USDC, ETH, SOL, OP, ARB, etc.).
- Stablecoin normalization: USDC/DAI/USDT treated as 1:1 USD.
- Proximity scoring: amounts near "bounty", "reward" keywords are preferred.
- Sanity bounds: amounts > $10M are discarded (configurable).
- Currency symbol stored in DB (`currency_symbol` column).

#### Scoring Model
- Composite score (0–100) with four weighted components:
  - Amount (35%), Recency (25%), Repo Activity (20%), Escrow Strength (20%).
- Weights configurable in `scraper_config.json`.
- Output sorted by score (descending), with amount as tie-breaker.

#### CLI
- Full argparse CLI: `--language`, `--min-stars`, `--since`, `--max-issues`, `--min-amount`, `--dry-run`, `--output-format`, `--verbose`, `--no-cache`, `--config`.
- `--output-format json` writes `output.json` with all fields.
- `--dry-run` skips all database writes.
- `--verbose` enables DEBUG-level logging.

#### Database
- New `issue_stats` columns: `first_seen_at`, `last_seen_at`, `last_updated_at`, `numeric_amount`, `raw_display_amount`, `currency_symbol`, `score`.
- New `repo_stats` columns: `first_seen_at`, `last_seen_at`, `total_escrows_seen`, `max_bounty_amount`.
- Batch commits (configurable, default 25) for better performance.
- `PRAGMA synchronous = NORMAL` for Windows performance.
- Issue-level cache check using `updatedAt` comparison.

#### p.py Viewer
- `--since YYYY-MM-DD` — filter by first_seen_at.
- `--min-amount` — filter by amount.
- `--show-unknown` — include Unknown/Custom Token leads.
- `--sort-by` (amount, score, date).
- `--export FILE.csv` — export to CSV.
- Adapts to both old and new DB schemas.

#### Infrastructure
- Package layout: `github_bounty_scraper/` with 10 focused modules.
- `python -m github_bounty_scraper` entry point.
- `pyproject.toml` with dependencies and ruff config.
- GitHub Actions CI: lint + smoke test on Python 3.11/3.12.
- Structured logging via Python's `logging` module.

### Fixed
- **Upsert bug**: `repo_stats` INSERT ON CONFLICT no longer resets `escrows_seen`, `rugs_seen`, `snipes_detected` to 0.
- **Case sensitivity**: All signal matching is now case-insensitive (signals lowercased at load time, text lowercased before comparison).

### Changed
- `evaluate_lane_status()` renamed concept: "True = lane is blocked" (was ambiguous).
- Removed overly broad signals: plain `x402` (kept `x402 payment`), `reward pool`, `grant funded`.
- `scraper.py` is now a thin wrapper delegating to the package.
- Error isolation: `process_issue` failures no longer kill the pipeline.
- Retry wrapper: 2 retries for transient network/DB errors.

### Removed
- Hardcoded search queries (now in `scraper_config.json`).
- `DEAD_REPOS_FILE` (`dead_repos.json`) — no longer needed.
