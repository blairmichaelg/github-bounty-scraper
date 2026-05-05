# Changelog

## [Unreleased]

### Added
- `inspect-leads` CLI subcommand with `--mode`, `--limit`, and `--db-path` flags
- `vibe_score` column surfaced in `inspect-leads` table output
- `--concurrency` flag for `vibe-check` subcommand (default: 5)
- Gemini API concurrency semaphore with per-call sleep throttle in `vibe.py`
- `exploration_min_stars_raw` config field for opportunistic raw candidate floor

### Changed
- `resolve_github_token()` now checks environment variables before invoking `gh` CLI subprocess
- Raw candidate logging in `core.py` is now non-blocking (async via `run_in_executor`)
- Search page requests in `discovery.py` now include 0.5s inter-page and 0.3s inter-query sleeps to stay within REST secondary rate limits
- `vibe-check` now accepts `concurrency` parameter in `run_vibe_check()`

### Fixed
- Removed duplicate threshold guards in `core.py` that could cause logic confusion in opportunistic mode
- Fixed tautological `None` check in `signals.py` `_is_assignment_stale()`
- Removed legacy root-level `scraper.py` shim (use `github-bounty-scraper` CLI or `python -m github_bounty_scraper`)

### Documentation
- README fully rewritten with mode comparison tables, full CLI reference, config field reference, and DB schema
- CONTRIBUTING.md rewritten with setup, test, and style guidelines
- Inline code comments added in `graphql.py` (comment pagination note) and `bounty.py` (short symbol risk note)

## [2.0.7] - 2026-04-29

### Fixed
- Added 11 new "job posting" negative filters (e.g. "salary:", "pm/ux audit") to kill non-bounty work requests
- Added 4 new aggregator repos to block `sol-bug-bench` style false positives
- Lowered `max_sane_amount` from 10,000,000 to 50,000 to effectively filter out $100k+ extreme outliers

## [2.0.6] - 2026-04-29

### Fixed
- Removed overly broad positive_escrow signals: "rewarding", "this pays", "grant:"
  which were matching non-bounty content like "rewarding experience" and "granted:"
- Fixed malformed GitHub search query for "will pay" — bare unquoted OR tokens
  are not valid in the REST search API; replaced with quoted phrase variants

## [2.0.5] - 2026-04-29

### Fixed
- Committed uncommitted graphql.py hotfix from live run session
- Expanded `positive_escrow` signal list with 40+ general-language patterns to recover real bounties using plain text instead of platform-specific language
- Added 19 spam/aggregator repo patterns to block bounty-farming accounts
- Lowered `min_stars` from 10 → 0 to stop silently killing small DeFi repos
- Lowered `min_bounty_amount` from 50 → 25 for better coverage
- Added 6 new "social task" negative filters to kill non-code engagement tasks
- Added 8 new search queries targeting plain-language bounty patterns

## [2.0.4] — 2026-04-29

### Fixed (Critical)
1. **core.py — escrow_inc lost on early returns** — Three exit paths after the escrow gate (ghost squatter, below-threshold, zero-amount) now call `upsert_repo_stats` with `escrow_increment` before returning `None`.
2. **scoring.py — recency default was 0.5** — Changed from `0.5` (free gift) to `0.0` (no bonus for unknown age). Prevents score inflation for issues with missing `updatedAt`.
3. **graphql.py + config.py — pr_cap/tl_max_pages configurable** — Added `pr_cap: int = 200` and `tl_max_pages: int = 5` to `ScraperConfig`, `scraper_config.json`, and `run_graphql_audit()`. Removed hardcoded `tl_max_pages = 5` local variable.
4. **signals.py — dead check_positive_escrow removed** — Deleted the entire function body (replaced by `SignalResult.has_positive_escrow` in v2.0.3).
5. **discovery.py — MAX_EXPANDED_QUERIES configurable** — Replaced module-level constant with `config.max_expanded_queries` (default 40). Added to `ScraperConfig` and `scraper_config.json`.

### Fixed (Medium)
6. **signals.py — label-based active signal check** — `_is_lane_blocked()` now accepts `labels_nodes` and checks for active claim labels (claimed, in-progress, assigned, wip). Added `active_label_signals` to `signals_config.json` and `load_signals()`.
7. **bounty.py — title-region proximity bonus** — Amounts in the first 200 chars get a conservative `0.5` proximity boost when no keyword context is nearby. Applied to both dollar and crypto match loops.
8. **graphql.py — ConnectedEvent schema verified** — Confirmed `willCloseTarget` is NOT present on `ConnectedEvent` fragments (correct behavior). No change needed.
9. **signals.py + graphql.py — UnassignedEvent handling** — Added `UNASSIGNED_EVENT` to timeline `itemTypes` in both GraphQL queries and `UnassignedEvent` fragment. `_is_assignment_stale()` now detects unassignment events: if the most recent event is an unassignment, the assignment is treated as stale.
10. **db.py — merges_last_45d refresh verified** — Confirmed `upsert_repo_stats` already sets `merges_last_45d = excluded.merges_last_45d` on conflict. No change needed.

### Added (Polish)
11. **signals.py — `__all__` export control** — Added `__all__` to restrict wildcard imports to `SignalResult`, `apply_hard_disqualifiers`, `compute_soft_signals`, `detect_snipe`.
12. **graphql.py — full error body logging** — Removed `[:200]` truncation from GraphQL error logging. Full body is now logged for non-200 responses.

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
