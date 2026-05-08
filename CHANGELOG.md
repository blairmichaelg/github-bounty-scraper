# Changelog

All notable changes to `github-bounty-scraper` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

***

## [2.4.1] - 2026-05-08

### Added
- `idx_issue_stats_vibe_composite` index on `(vibe_score, vibe_scored_at)` to optimize unscored issue discovery in `vibe-check`.
- New unit tests in `tests/test_core_meta.py` covering `_get_issue_meta` TTL expiry and dry-run logic.

### Changed
- Moved `aiosqlite` import to module level in `vibe.py` to support correct type hints and static analysis.

### Fixed
- Hardened `vibe_retry.txt` path validation in `vibe-check` using `is_relative_to(project_root)` to prevent potential path traversal.

***

## [2.4.0] - 2026-05-07

### Added
- Backward-compatible `extract_bounty_amount()` wrapper for older scripts and tests.
- `PROD_MODEL_FEATURES` constant documenting the production inference feature contract.
- Regression coverage for scrape options after the `scrape` subcommand and for excluding incomplete vibe-only rows from lead inspection.

### Changed
- Scrape options such as `--max-issues`, `--dry-run`, `--output-file`, `--db-path`, `--min-amount`, and `--min-stars` now work before or after the `scrape` subcommand.
- CLI compatibility fields now map into runtime fields: `min_amount` to `min_bounty_amount`, `db_path` to `db_file`, and `min_stars` to `min_repo_stars`.
- `tools/balance_dataset.py` now accepts input/output arguments instead of relying on a hard-coded generated CSV.
- Documentation now reflects generated artifact policy, current CLI behavior, and the local model training workflow.

### Fixed
- Unknown bounty amounts consistently use the `-1.0` sentinel when a bounty cue exists but no amount can be parsed.
- `detect_snipe()` accepts both current pipeline calls and legacy timeline-only helper calls.
- Vibe-only rows are marked `lead_mode='vibe_only'`, `escrow_verified=0`, and excluded from `inspect-leads` and dataset export until enriched by a scrape.
- Production model metadata and local regenerated model feature counts were brought back into alignment.

### Removed
- Generated datasets, model binaries, sample result JSON, and stale model sidecars from source control.

### Verification
- `ruff check github_bounty_scraper tests scripts tools`
- `ruff format --check github_bounty_scraper tests scripts tools`
- `mypy github_bounty_scraper`
- `pytest --cov=github_bounty_scraper --cov-fail-under=80`
- Result: 199 passed, 1 skipped, 88.12% coverage locally.

***

## [2.3.0] — 2026-05-06

### Changed
- Decomposed `process_issue()` monolith in `core.py` into `_enrich_issue()`,
  `_persist_lead()`, `_build_lead_result()`, `_get_repo_activity()`,
  `_is_new_repo_grace()`, and `_is_qualified_lead()` helper functions

### Added
- `.github/workflows/ci.yml` — GitHub Actions CI with Python 3.11/3.12 matrix,
  ruff, mypy, and pytest enforcing ≥80% coverage on every push
- `.env.example` — documents all required environment variables
- `TestModelLoadAndInspect` in `test_main.py` — covers model-load CLI paths
- `TestRunPipeline` in `test_core.py` — covers full async pipeline dispatch

### Fixed
- `signals.py` — `apply_hard_disqualifiers` and `_is_lane_blocked` no longer
  crash on malformed labels or None values
- `__main__.py` — model checksum verification enforced before loading

### Coverage
Total: 87% (156 tests)

***

## [2.2.1] — 2026-05-06

### Added
- Verified all v2.1.0 safety features: `asyncio.to_thread` for `_append_raw`,
  `ESCROW_WEIGHT_CAP` constant, `_SecretStr` token masking, SQLite PRAGMA tuning
- Restored `scoring.py` to 100% coverage
- Permanent removal of `docs/task.md` from git tracking

### Coverage
Total: 80% (156 tests)

***

## [2.2.0] — 2026-05-06

### Added
- Shared `conftest.py` fixtures: `cfg`, `mock_db_conn`, `mock_aiohttp_session`, `minimal_issue`
- `TestProcessIssueIntegration` in `test_core.py`
- `TestMainDispatch` in `test_main.py`
- Merged `test_db_label.py` into `test_db.py`, deleted orphan file
- Gemini API error path coverage (429, 500, malformed JSON)
- `signals.py` hardened for malformed label data

### Coverage
Total: 80% (130 tests)

***

## [2.1.0] — 2026-05-06

### Added
- `asyncio.to_thread` wrapper around `_append_raw` file writes
- `ESCROW_WEIGHT_CAP = 5.0` constant in `config.py` replacing magic numbers
- `_SecretStr` class in `graphql.py` for GitHub PAT log masking
- SQLite PRAGMA: WAL mode, 32MB cache, 256MB mmap in `init_db()`
- `.gitignore` entries for `*.pkl`, `*.csv`, `results_*.json`

### Fixed
- Scoring weight normalization guard in `ScraperConfig.__post_init__`
- `vibe_score=None` now redistributes weight rather than scoring 0

### Coverage
Total: 67% (84 tests, first structured audit baseline)

***

## [2.0.7] — Prior to audit series

Initial tracked version. 15-stage async pipeline with GraphQL enrichment,
signals analysis, Gemini vibe-checking, and SQLite caching.
