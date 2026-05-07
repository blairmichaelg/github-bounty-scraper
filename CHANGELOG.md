# Changelog

All notable changes to `github-bounty-scraper` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
