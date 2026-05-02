# 🏴‍☠️ GitHub Bounty Scraper

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

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

## Operational Modes

The scraper now supports two distinct operational modes tailored for different risk profiles:

### 1. Strict Mode (Default)
**High-precision, autopilot-ready.**
- Filters out "dead" repos (0 merges in 45 days).
- Requires at least 5 stars and excludes single-contributor personal repos.
- Requires explicit positive escrow signals in the body/comments.
- Enforces a minimum bounty threshold (default $50).

**Example:**
```bash
github-bounty-scraper scrape --since 2025-01-01 --max-issues 300 --mode strict
```

### 2. Opportunistic Mode
**Scouting mode for research and high-recall mining.**
- Allows dead repos and lower star counts (>= 1).
- Includes issues with "bounty-ish" titles or labels even if explicit escrow phrases are missing.
- Allows lower bounty amounts (default $10).
- Flags leads in the database with `lead_mode='opportunistic'`.

**Example:**
```bash
github-bounty-scraper scrape --since 2023-01-01 --max-issues 500 --mode opportunistic --log-raw-candidates
```

---

## Setup

```bash
# Clone the repository
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper

# Install as a CLI tool (editable mode)
pip install -e .

# Authenticate with GitHub (choose one):
# Option A: GitHub CLI (recommended)
gh auth login

# Option B: .env file
echo 'GITHUB_TOKEN=ghp_your_token_here' > .env
```

---

## Usage

### Scraping Bounties
```bash
# Basic run (Strict mode)
github-bounty-scraper scrape --since 2025-01-01

# Filter by language
github-bounty-scraper scrape --language Python --language TypeScript

# Opportunistic run with raw logging
github-bounty-scraper scrape --mode opportunistic --log-raw-candidates
```

### Inspecting Leads
View recently saved leads directly from the command line:

```bash
# View last 20 strict leads (default)
github-bounty-scraper inspect-leads

# View last 50 opportunistic leads
github-bounty-scraper inspect-leads --mode opportunistic --limit 50

# View all leads of any mode
github-bounty-scraper inspect-leads --mode all
```

---

## Exploration & Tools

When running with `--log-raw-candidates`, the scraper saves a noisy pool of structurally sane issues to `exploration_raw.jsonl`. This file is ignored by git and used for signal mining.

Use the provided analysis tool to inspect the raw pool:
```bash
python tools/analyze_raw.py
```

---

## Database Schema (`issue_stats`)

The scraper maintains a local SQLite database (`bounty_stats.db`). Each lead is enriched with mode-specific metadata:

| Column | Type | Description |
|--------|------|-------------|
| `score` | REAL | Composite score (0–100) |
| `numeric_amount` | REAL | Parsed USD value (-1 = missing but accepted) |
| `lead_mode` | TEXT | 'strict' or 'opportunistic' |
| `escrow_verified` | INTEGER | 1 if positive escrow phrases were found |
| `is_dead_repo` | INTEGER | 1 if repo has 0 merges in 45 days |
| `issue_url` | TEXT PK | Full GitHub issue URL |

---

## Project Structure

```
github-bounty-scraper/
├── github_bounty_scraper/     # Core package
│   ├── cli.py                 # Argparse commands (scrape, inspect-leads)
│   ├── config.py              # Strict vs Opportunistic overrides
│   ├── core.py                # Pipeline orchestration
│   ├── db.py                  # Database & inspection logic
│   └── ...
├── tools/
│   └── analyze_raw.py         # Exploration analysis helper
├── scraper_config.json        # Global thresholds & queries
├── signals_config.json        # Signal keyword lists
├── pyproject.toml             # Package configuration
└── README.md
```

---

## License

MIT — see [LICENSE](LICENSE) for details.
