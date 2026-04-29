# Changelog

## [Unreleased]

## [2.0.3] — 2026-04-29

### Fixed (Critical)
1. **pyproject.toml version out of sync** — Version was stuck at `"2.0.1"`. Bumped directly to `"2.0.3"`.
2. **db.py + core.py — title/repo_name not persisted** — Added `title TEXT` and `repo_name TEXT` columns to `issue_stats` schema, migration loop, and `upsert_issue_stats()`. `core.py` now passes both values. `p.py` reads them from DB for console output and JSON export instead of empty strings / URL parsing.
3. **bounty.py — crypto keyword false positives** — Replaced naive `kw in text_upper` substring matching with a compiled word-boundary regex (`_CRYPTO_KEYWORD_RE`). Prevents false positives like 'OPERATION' matching 'OP'.
4. **graphql.py — timelineItems never paginated** — Added `_TIMELINE_PAGE_QUERY` and a 5-page pagination loop (500 events max) in `run_graphql_audit()` for issues with >100 timeline events.
5. **signals.py — lane_blocked has no age cap** — Added `active_signal_max_age_days` parameter (default 90) to `_is_lane_blocked()`. Claims older than the cap are treated as stale. Wired through `ScraperConfig`, `scraper_config.json`, and `compute_soft_signals()`.

### Fixed (Medium)
6. **discovery.py — query explosion guard** — Capped expanded queries at 40 with a warning. Added estimated API call count to discovery log.
7. **config.py — scoring weight validation** — `build_config()` now warns if `weight_*` values don't sum to ~1.0.
8. **bounty.py + config.py — proximity_window configurable** — Added `proximity_window: int = 300` to `ScraperConfig`, `scraper_config.json`, and `extract_bounty_amount()`. Hardcoded 300 replaced.
9. **core.py — kill labels now increment rugs_seen** — Expanded `rug_inc` condition to also match `"kill label"` reason strings.
10. **signals.py — check_positive_escrow merged into compute_soft_signals** — Added `has_positive_escrow: bool` to `SignalResult`. `core.py` now uses `soft.has_positive_escrow` instead of a separate function call.

### Added (Polish)
11. **ci.yml — Python 3.13** — Added `"3.13"` to the CI test matrix.
12. **pyproject.toml** — `[tool.mypy]` config confirmed present (already added in v2.0.2).



## [2.0.2] — 2026-04-29

### Fixed (Critical)
1. **pyproject.toml version out of sync** — `version` was still `"2.0.0"` while the git tag was `v2.0.1`. Now `"2.0.1"` so `importlib.metadata.version()` is correct.
2. **discovery.py sort_by never wired** — `config.sort_by` was loaded from JSON but `fetch_rest_search()` hardcoded `"sort": "updated"`. Added `sort_by` parameter, wired from `discover_issues()`.
3. **scoring.py dead parameter** — Removed unused `total_positive_signals` from `compute_score()` signature and its call site. The escrow norm formula uses a fixed `/5.0` divisor.
4. **core.py has_negative_soft always False** — Added `has_negative_soft` field to `SignalResult`. `compute_soft_signals` now scans for `soft_negative_signals` (loaded from `signals_config.json`). `core.py` passes `soft.has_negative_soft` to `compute_score()`.
5. **p.py JSON export empty repo/title** — `repo` is now parsed from the issue URL at export time. `title` is documented as not stored in DB.

### Fixed (Medium)
6. **signals.py escrow double-counting** — Replaced per-occurrence counting with set-based dedup: only unique signal types are counted, not total occurrences across body+comments.
7. **graphql.py PR early-stop field mismatch** — Changed `orderBy` from `UPDATED_AT` to `MERGED_AT` so the sort field matches the `mergedAt` early-stop check.
8. **config.py zero/empty-string CLI override bug** — Removed the `if value is not None` guard; with `SUPPRESS`, all keys in `cli_overrides` were explicitly set by the user.
9. **output.py naive datetime** — Replaced `datetime.now()` with `datetime.now(datetime.timezone.utc)` in both markdown and JSON reports. Markdown appends " UTC".
10. **core.py upsert_repo_stats called 3–4× per issue** — Consolidated into a single call on the happy path (with early-exit calls only for disqualified/sniped issues). Accumulates `escrow_inc`, `rug_inc`, `snipe_inc` as local variables.

### Added (Polish)
11. **mypy** — Added `mypy>=1.10` to dev deps, `[tool.mypy]` config, and a CI step.
12. **search_queries** — Added `label:"good first issue" "reward"` query to `scraper_config.json`.
13. **soft_negative_signals** — New key in `signals_config.json` with terms like "wip", "on hold", "blocked", "postponed", etc.

### Previous v2.0.1 fixes (from earlier polish pass)
- Corrected broken build backend (`setuptools.build_meta`).
- Fixed CLI boolean override bug via `argument_default=SUPPRESS`.
- Guarded `strptime` in core.py DB upsert.
- Replaced fragile `AssignedEvent` key-heuristic with `__typename` checking.
- Added `COALESCE` for `first_seen_at` preservation in both upsert statements.
- Fixed ISO string comparison for PR window cutoff.
- Restricted markdown output to `output_format == "markdown"` only.
- Fixed escrow_norm formula (divisor: 5.0 instead of total signal count).
- Widened bounty proximity window from 120 to 300 characters.
- Added `timeout=5` to `gh auth token` subprocess call.
- Externalized aggregator repos to `signals_config.json`.
- Single-source versioning via `importlib.metadata`.
- Updated `requirements.txt` with version pins.
- Raised `min_bounty_amount` default from $10 to $25.
- Suppressed console output for JSON-only runs.
- Added JSON export to `p.py --export`.
- Added pip cache to CI workflow.

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
