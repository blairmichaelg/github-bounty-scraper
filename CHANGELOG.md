# Changelog

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
