# 🏴‍☠️ GitHub Bounty Scraper

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/blairmichaelg/github-bounty-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/blairmichaelg/github-bounty-scraper/actions)

```
 ╔══════════════════════════════════════════╗
 ║   GITHUB  BOUNTY  SCRAPER               ║
 ║   ─────────────────────────              ║
 ║   Discover funded crypto bounties        ║
 ║   on GitHub Issues — async & fast.       ║
 ╚══════════════════════════════════════════╝
```

> Async Python pipeline that discovers and scores funded crypto bounties on
> GitHub Issues using GraphQL enrichment, a scoring model, and SQLite caching.

---

## How It Works

The scraper runs a **two-phase pipeline**:

### Phase 1 — Discovery (REST Search API)
Multiple search queries hit the GitHub REST Search API to find open issues
mentioning bounty platforms, escrow signals, and crypto payment keywords
(USDC, ETH, SOL, OP, ARB, etc.). Queries are externalized to
`scraper_config.json` and can be filtered by language, stars, and recency
via CLI flags. Results are deduplicated by URL.

### Phase 2 — Enrichment & Scoring (GraphQL API)
Each candidate issue is enriched via GraphQL with PR pagination (up to 200
merged PRs scanned for 45-day activity), and scored using a composite model:

- **Repo health** — merged PR count in the last 45 days
- **Issue metadata** — body, comments, labels, assignees, timeline events
- **Escrow signals** — positive/negative keyword matching
- **Bounty amount** — robust extraction with currency detection
- **Lane status** — detects stale vs. active claims
- **Snipe detection** — open PRs that would auto-close the issue
- **Scoring** — weighted composite of amount, recency, activity, escrow strength

---

## Scoring Model

Each surviving issue receives a composite score (0–100) based on:

| Component | Weight | Description |
|-----------|--------|-------------|
| **Amount** | 35% | `log10(amount + 1)` normalized, capped at $100k |
| **Recency** | 25% | Exponential decay, 30-day half-life from `updatedAt` |
| **Repo Activity** | 20% | `min(merges_45d, 20) / 20` |
| **Escrow Strength** | 20% | Ratio of positive escrow signal hits |

Soft negative signals apply a −10 penalty. Weights are configurable in
`scraper_config.json`.

---

## Pipeline Filters

| Filter | What It Does |
|--------|-------------|
| **Dead Repo** | Skips repos with 0 merged PRs in 45 days (unless repo is <90 days old) |
| **Issue State** | Drops CLOSED issues immediately after GraphQL fetch |
| **Kill Labels** | Drops issues tagged `security`, `audit`, `cve`, `internal`, etc. |
| **Negative Filters** | Removes spam platforms and cancelled bounties |
| **Lane Status** | Skips issues with an active `/claim` more recent than any stale signal |
| **Positive Escrow** | Requires at least one positive funding signal |
| **Snipe Detection** | Drops issues with a non-draft open PR that will auto-close the issue |
| **Ghost Squatter** | Skips freshly assigned issues, allows stale assignments through |
| **Amount Threshold** | Drops leads with numeric amount below threshold (default $10) |

---

## Setup

```bash
# Clone the repository
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper

# Install (editable mode recommended for development)
pip install -e ".[dev]"

# Or traditional install
pip install -r requirements.txt

# Authenticate with GitHub (choose one):

# Option A: GitHub CLI (recommended)
gh auth login

# Option B: Environment variable
export GITHUB_TOKEN="ghp_your_token_here"

# Option C: .env file (requires python-dotenv)
echo 'GITHUB_TOKEN=ghp_your_token_here' > .env
```

---

## Usage

### Basic

```bash
# Run with defaults
python scraper.py

# Or as a Python module
python -m github_bounty_scraper
```

### CLI Flags

```bash
# Filter by language
python scraper.py --language Python --language TypeScript

# Set minimum star count
python scraper.py --min-stars 100

# Only issues updated in the last 7 days
python scraper.py --since 2026-04-22

# Limit total issues processed
python scraper.py --max-issues 50

# Override minimum bounty amount
python scraper.py --min-amount 200

# Output as JSON
python scraper.py --output-format json

# Dry run (no database writes)
python scraper.py --dry-run --max-issues 10

# Verbose (DEBUG-level logging)
python scraper.py --verbose

# Skip cache (re-enrich everything)
python scraper.py --no-cache

# Custom config file
python scraper.py --config my_config.json
```

### Example: Find Python bounties updated recently with min $200

```bash
python scraper.py --language Python --since 2026-04-22 --min-amount 200
```

### Example: Quick JSON export of top 50 issues

```bash
python scraper.py --max-issues 50 --output-format json
```

---

## DB Viewer (`p.py`)

```bash
# View top results (sorted by score)
python p.py

# Limit output
python p.py --limit 10

# Filter by amount and date
python p.py --min-amount 100 --since 2026-04-22

# Include unknown/custom token leads
python p.py --show-unknown

# Sort by amount instead of score
python p.py --sort-by amount

# Export to CSV
python p.py --export results.csv
```

---

## Configuration

### `scraper_config.json`

Top-level configuration with all tunable parameters:

| Key | Type | Description |
|-----|------|-------------|
| `search_queries` | array | GitHub search query templates |
| `min_stars` | int | Default minimum star count (default: 10) |
| `max_pages_per_query` | int | Pages to fetch per query (default: 5) |
| `min_bounty_amount` | float | Minimum bounty threshold (default: $10) |
| `max_sane_amount` | float | Upper sanity bound (default: $10M) |
| `weight_amount` | float | Scoring weight for amount (default: 0.35) |
| `weight_recency` | float | Scoring weight for recency (default: 0.25) |
| `weight_activity` | float | Scoring weight for repo activity (default: 0.20) |
| `weight_escrow_strength` | float | Scoring weight for escrow signals (default: 0.20) |
| `batch_commit_size` | int | DB commits per N issues (default: 25) |

### `signals_config.json`

All signal strings for filtering:

| Key | Purpose |
|-----|---------|
| `positive_escrow` | Strings indicating real funding |
| `negative_filters` | Spam/cancelled bounty signals |
| `stale_signals` | Indicators of expired/abandoned claims |
| `active_signals` | Indicators of active work |
| `kill_labels` | Labels causing immediate rejection |

CLI flags always override config file values, and config file values
override built-in defaults.

---

## Database

The scraper maintains a local SQLite database (`bounty_stats.db`) with
adaptive TTL caching.

### Schema

**`repo_stats`** — Per-repository health metrics:
| Column | Type | Description |
|--------|------|-------------|
| `repo_name` | TEXT PK | `owner/repo` |
| `last_checked_at` | REAL | Unix timestamp of last check |
| `last_merged_pr_at` | REAL | Timestamp of most recent merged PR |
| `merges_last_45d` | INTEGER | Merged PRs in last 45 days |
| `escrows_seen` | INTEGER | Positive escrow signals (per-run) |
| `rugs_seen` | INTEGER | Negative signals (per-run) |
| `snipes_detected` | INTEGER | Snipe PRs detected (per-run) |
| `first_seen_at` | REAL | First time this repo was seen |
| `last_seen_at` | REAL | Most recent time this repo was seen |
| `total_escrows_seen` | INTEGER | Cumulative escrow signal count |
| `max_bounty_amount` | REAL | Highest bounty amount seen in this repo |

**`issue_stats`** — Per-issue extraction cache:
| Column | Type | Description |
|--------|------|-------------|
| `issue_url` | TEXT PK | Full GitHub issue URL |
| `checked_at` | REAL | Unix timestamp of last check |
| `scraped_amount` | REAL | Extracted bounty amount (-1 = unknown) |
| `first_seen_at` | REAL | First time this issue was seen |
| `last_seen_at` | REAL | Most recent time this issue was seen |
| `last_updated_at` | REAL | Issue's updatedAt from GitHub |
| `numeric_amount` | REAL | Parsed numeric bounty value |
| `raw_display_amount` | TEXT | Original matched text |
| `currency_symbol` | TEXT | Currency (USD, ETH, SOL, etc.) |
| `score` | REAL | Composite score (0–100) |

---

## Project Structure

```
github-bounty-scraper/
├── scraper.py                    # Entry point (thin wrapper)
├── p.py                          # DB viewer utility
├── scraper_config.json           # Runtime configuration
├── signals_config.json           # Signal keyword lists
├── pyproject.toml                # Package metadata & dependencies
├── requirements.txt              # Legacy dependency list
├── github_bounty_scraper/
│   ├── __init__.py
│   ├── __main__.py               # python -m entry point
│   ├── cli.py                    # Argparse CLI
│   ├── config.py                 # Configuration management
│   ├── core.py                   # Main pipeline orchestrator
│   ├── db.py                     # Database schema & helpers
│   ├── discovery.py              # REST search API layer
│   ├── graphql.py                # GraphQL enrichment
│   ├── bounty.py                 # Amount extraction & currency
│   ├── signals.py                # Signal filtering
│   ├── scoring.py                # Scoring model
│   ├── output.py                 # Output formatters
│   └── log.py                    # Structured logging
└── .github/workflows/ci.yml     # CI pipeline
```

---

## Disclaimer

> ⚠️ **This tool is for discovery only.** Always verify bounty legitimacy,
> funding status, and project reputation before investing your time.
> The authors are not responsible for any losses incurred from pursuing
> leads surfaced by this tool.

---

## License

MIT — see [LICENSE](LICENSE) for details.
