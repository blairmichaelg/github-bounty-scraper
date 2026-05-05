# GitHub Bounty Scraper

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Async](https://img.shields.io/badge/async-aiohttp%20%2B%20aiosqlite-green.svg)]()

An async Python pipeline that discovers, enriches, and scores **funded crypto bounties** on GitHub Issues. It uses the GitHub GraphQL API for deep enrichment, a composite scoring model, SQLite caching, and an optional Gemini LLM "vibe check" layer.

---

## Features

- **Two operational modes** — Strict (high-precision autopilot) and Opportunistic (high-recall scouting)
- **GraphQL enrichment** — repo health, PR activity, escrow signals, snipe detection, lane blocking
- **Composite scoring** — amount · recency · activity · escrow strength, configurable weights
- **SQLite persistence** — deduplication, per-issue caching with adaptive TTLs, mode flags
- **Optional LLM annotation** — Gemini 1.5 Flash vibe check with concurrency control
- **CLI-first** — all features accessible via `github-bounty-scraper` subcommands

---

## Installation

```bash
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper
pip install -e .
```

**Authentication (choose one):**

```bash
# Option A — GitHub CLI (recommended)
gh auth login

# Option B — environment variable
export GITHUB_TOKEN=ghp_your_token_here
```

---

## Modes

### Strict (default) — High Precision

Only inserts leads that look like real, funded bounties on active repositories.

| Gate | Requirement |
|------|-------------|
| Stars | ≥ 5 |
| Repo activity | ≥ 1 merge in last 45 days |
| Escrow signals | Required in body or comments |
| Minimum amount | ≥ $50 USD (configurable) |
| Dead repos | Excluded |
| Raw candidate logging | Disabled |

```bash
github-bounty-scraper scrape --since 2025-01-01 --max-issues 300 --mode strict
```

### Opportunistic — High Recall

Surfaces edge-case and low-signal bounties. All leads are clearly flagged in the DB.

| Gate | Requirement |
|------|-------------|
| Stars | ≥ 1 |
| Repo activity | Dead repos allowed (`is_dead_repo=1`) |
| Escrow signals | Optional if bounty cue in title/label |
| Minimum amount | ≥ $10 or `-1.0` (cue present, amount missing) |
| Raw candidate logging | Auto-enabled |

```bash
github-bounty-scraper scrape --since 2023-01-01 --max-issues 500 --mode opportunistic
```

---

## CLI Reference

### `scrape`
```
github-bounty-scraper scrape [OPTIONS]

  --since DATE          Only issues updated on or after this date (YYYY-MM-DD)
  --max-issues N        Cap total issues processed per run (0 = unlimited)
  --mode MODE           strict (default) or opportunistic
  --language LANG       Filter by language (repeatable)
  --no-cache            Skip all TTL caches
  --dry-run             Run pipeline without writing to DB
  -v, --verbose         Enable debug logging
  --log-raw-candidates  Write rejected candidates to exploration_raw.jsonl
```

### `inspect-leads`
```
github-bounty-scraper inspect-leads [OPTIONS]

  --mode MODE     strict | opportunistic | all (default: strict)
  --limit N       Number of leads to display (default: 20)
  --db-path PATH  Path to SQLite database (default: bounty_stats.db)
```

Output columns: `SCORE · AMOUNT · MODE · ESCROW · DEAD · VIBE · REPO · URL`

### `vibe-check` (optional, requires Gemini API key)
```
github-bounty-scraper vibe-check [OPTIONS]

  --mode MODE         unscored (default) | all | rescore
  --limit N           Max issues to score (default: 50)
  --concurrency N     Concurrent Gemini API calls (default: 5)
  --raw-file PATH     Path to exploration_raw.jsonl
  --db-path PATH      Path to SQLite database

export GEMINI_API_KEY=your_key_here
github-bounty-scraper vibe-check --mode unscored --limit 100
```

---

## Configuration

### `scraper_config.json`
Controls global thresholds, search queries, caching TTLs, concurrency, and scoring weights.
Key fields:

| Field | Default | Description |
|-------|---------|-------------|
| `min_stars` | `5` | Minimum repo stars (strict) |
| `min_bounty_amount` | `50.0` | Minimum USD amount (strict) |
| `opportunistic_min_amount` | `10.0` | Minimum USD amount (opportunistic) |
| `new_repo_grace_days` | `90` | Grace period before dead-repo check |
| `semaphore_limit` | `15` | Max concurrent GraphQL enrichments |
| `cache_ttl_active` | `7200` | Issue TTL for active repos (seconds) |

### `signals_config.json`
Contains all keyword lists used for signal detection:
- `positive_escrow` — phrases that indicate funds are locked (e.g. `escrow`, `funded`, `locked`)
- `negative_filters` — hard-disqualify phrases
- `kill_labels` — GitHub label names that immediately drop an issue
- `active_signals` / `stale_signals` — lane status detection
- `aggregator_repos` — repos to always skip (e.g. bounty aggregators)

---

## Database Schema

SQLite file: `bounty_stats.db` (git-ignored).

**`issue_stats`** — one row per unique issue URL:

| Column | Type | Description |
|--------|------|-------------|
| `issue_url` | TEXT PK | Full GitHub issue URL |
| `title` | TEXT | Issue title |
| `repo_name` | TEXT | `owner/repo` |
| `score` | REAL | Composite score 0–100 |
| `numeric_amount` | REAL | Parsed USD value (`-1` = cue present, amount missing) |
| `raw_display_amount` | TEXT | Original amount string from issue |
| `currency_symbol` | TEXT | USD, ETH, USDC, etc. |
| `lead_mode` | TEXT | `strict` or `opportunistic` |
| `escrow_verified` | INTEGER | 1 if positive escrow phrases found |
| `is_dead_repo` | INTEGER | 1 if 0 merges in 45 days |
| `vibe_score` | INTEGER | 0–100 LLM quality score (nullable) |
| `vibe_reason` | TEXT | LLM one-line rationale (nullable) |
| `checked_at` | REAL | Unix timestamp of last scrape |

---

## Project Structure

```
github-bounty-scraper/
├── github_bounty_scraper/
│   ├── __init__.py        # Package version
│   ├── __main__.py        # Entry point dispatcher
│   ├── bounty.py          # Amount extraction (dollar + crypto regex)
│   ├── cli.py             # Argparse subcommands
│   ├── config.py          # ScraperConfig dataclass + build_config()
│   ├── core.py            # Pipeline orchestration (async)
│   ├── db.py              # SQLite helpers, BatchCommitter, inspect query
│   ├── discovery.py       # REST search API + query builder
│   ├── graphql.py         # GraphQL enrichment + TokenBucket rate limiter
│   ├── log.py             # Logging setup
│   ├── output.py          # Text/Markdown/JSON output formatters
│   ├── scoring.py         # Composite scoring model
│   ├── signals.py         # Hard disqualifiers, soft signals, snipe detection
│   └── vibe.py            # Optional Gemini LLM annotation layer
├── tools/
│   └── analyze_raw.py     # Exploration helper: inspect exploration_raw.jsonl
├── scraper_config.json    # Global thresholds and search queries
├── signals_config.json    # Signal keyword lists
├── pyproject.toml         # Package metadata and dependencies
├── requirements.txt       # Pinned dependencies
├── CHANGELOG.md           # Version history
├── CONTRIBUTING.md        # Contribution guidelines
└── LICENSE                # MIT License
```

---

## Exploration Tools

When `--log-raw-candidates` is enabled, rejected-but-interesting candidates
are written to `exploration_raw.jsonl` (git-ignored). Inspect them with:

```bash
python tools/analyze_raw.py
```

Output includes: total count, amount breakdown, org vs. personal repos,
sample titles and URLs.

---

## License

MIT — see [LICENSE](LICENSE) for details.
