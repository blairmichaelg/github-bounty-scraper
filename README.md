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
> GitHub Issues using GraphQL enrichment and SQLite caching.

---

## How It Works

The scraper runs a **two-phase pipeline**:

### Phase 1 — Discovery (REST Search API)
Multiple search queries ("dorks") hit the GitHub REST Search API in parallel
to find open issues mentioning bounty platforms, escrow signals, and crypto
payment keywords (USDC, ETH, SOL, OP, ARB, etc.). Results are deduplicated
by URL.

### Phase 2 — Enrichment & Scoring (GraphQL API)
Each candidate issue is enriched via a single GraphQL call that fetches:
- **Repo health** — merged PR count in the last 45 days
- **Issue metadata** — body, comments, labels, assignees, timeline events
- **Escrow signals** — positive/negative keyword matching
- **Lane status** — detects stale vs. active claims
- **Snipe detection** — open PRs that would auto-close the issue

Issues pass through a series of filters and only verified, actionable leads
survive.

---

## Pipeline Filters

| Filter | What It Does |
|--------|-------------|
| **Dead Repo** | Skips repos with 0 merged PRs in 45 days (unless repo is <90 days old) |
| **Kill Labels** | Drops issues tagged `security`, `audit`, `cve`, `internal`, etc. |
| **Negative Filters** | Removes spam platforms (Algora, Gitcoin, IssueHunt, BountySource) and cancelled bounties |
| **Lane Status** | Skips issues with an active `/claim` or `/attempt` more recent than any stale signal |
| **Positive Escrow** | Requires at least one positive funding signal (escrow locked, USDC funded, etc.) |
| **Snipe Detection** | Drops issues with a non-draft open PR that will auto-close the issue |
| **Ghost Squatter** | Skips freshly assigned issues, but allows stale/re-opened assignments through |
| **Amount Threshold** | Drops leads with numeric amount below $10 (configurable) |

---

## Setup

```bash
# Clone the repository
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper

# Install dependencies
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

```bash
# Run the scraper
python scraper.py

# View cached results from the database
python p.py

# Limit DB viewer output
python p.py --limit 10
```

---

## Output

### Console
The scraper prints verified leads sorted by amount (highest first), followed
by any Unknown/Custom Token leads at the end.

### `output.md`
A structured markdown file is generated after each run containing:
- Timestamp and pipeline stats
- Markdown table of verified leads (Amount, Repo, Title, Labels, Link)
- Separate section for Unknown/Custom Token leads

---

## `signals_config.json`

All signal strings are externalized into `signals_config.json` so you can
add or remove keywords **without editing Python code**.

| Key | Purpose |
|-----|---------|
| `positive_escrow` | Strings that indicate real funding (escrow, wallet payment, etc.) |
| `negative_filters` | Spam/cancelled bounty signals to reject |
| `stale_signals` | Indicators that a previous claim expired or was abandoned |
| `active_signals` | Indicators that someone is actively working on the issue |
| `kill_labels` | Issue labels that cause immediate rejection |

Simply edit the JSON arrays and re-run the scraper.

---

## Database

The scraper maintains a local SQLite database (`bounty_stats.db`) with
adaptive TTL caching to avoid redundant API calls.

### Schema

**`repo_stats`** — Per-repository health metrics:
| Column | Type | Description |
|--------|------|-------------|
| `repo_name` | TEXT PK | `owner/repo` |
| `last_checked_at` | REAL | Unix timestamp of last GraphQL check |
| `last_merged_pr_at` | REAL | Timestamp of most recent merged PR |
| `merges_last_45d` | INTEGER | Merged PRs in the last 45 days |
| `escrows_seen` | INTEGER | Count of positive escrow signals found |
| `rugs_seen` | INTEGER | Count of negative signals found |
| `snipes_detected` | INTEGER | Count of snipe PRs detected |

**`issue_stats`** — Per-issue extraction cache:
| Column | Type | Description |
|--------|------|-------------|
| `issue_url` | TEXT PK | Full GitHub issue URL |
| `checked_at` | REAL | Unix timestamp of last check |
| `scraped_amount` | REAL | Extracted bounty amount (-1 = unknown) |

---

## Disclaimer

> ⚠️ **This tool is for discovery only.** Always verify bounty legitimacy,
> funding status, and project reputation before investing your time.
> The authors are not responsible for any losses incurred from pursuing
> leads surfaced by this tool.

---

## License

MIT — see [LICENSE](LICENSE) for details.
