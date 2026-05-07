# GitHub Bounty Scraper

[![CI](https://github.com/blairmichaelg/github-bounty-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/blairmichaelg/github-bounty-scraper/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

GitHub Bounty Scraper is an async Python pipeline for discovering, enriching, and ranking GitHub issues that may represent funded crypto or open-source bounty work.

It combines GitHub REST search, GraphQL enrichment, SQLite caching, heuristic scoring, signal detection, and optional Gemini-based review. The repository intentionally does not track generated databases, scrape outputs, datasets, or trained model binaries.

## Current Status

- Package version: `2.4.0`
- CI target: Python 3.11 and 3.12
- Quality gates: `ruff`, `ruff format --check`, `mypy`, and `pytest --cov-fail-under=80`
- Local verification baseline: CI-equivalent checks pass with coverage above the 80% gate
- Generated artifacts are ignored by git and should be regenerated locally or attached to releases

## Installation

```bash
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
```

On macOS or Linux, activate with:

```bash
source venv/bin/activate
```

## Authentication

The scraper requires a GitHub token for REST and GraphQL API calls.

```bash
gh auth login
```

Or set an environment variable:

```bash
GITHUB_TOKEN=ghp_your_token_here
```

Optional vibe checks require:

```bash
GEMINI_API_KEY=your_gemini_key_here
```

Copy `.env.example` to `.env` for local development. Never commit `.env`.

## Core Commands

Run a strict scrape:

```bash
github-bounty-scraper scrape --since 2025-01-01 --max-issues 300 --mode strict
```

Run a broader opportunistic scrape:

```bash
github-bounty-scraper scrape --since 2025-01-01 --max-issues 500 --mode opportunistic --log-raw-candidates
```

Inspect enriched leads:

```bash
github-bounty-scraper inspect-leads --mode all --limit 20
```

Run Gemini vibe checks over raw candidates:

```bash
github-bounty-scraper vibe-check --mode unscored --limit 100 --concurrency 3 --raw-file exploration_raw.jsonl
```

Export a training dataset:

```bash
github-bounty-scraper dump-dataset --db-path bounty_stats.db --raw-file exploration_raw.jsonl --out-csv bounty_dataset.csv
```

## CLI Notes

Scrape runtime options work both before and after the `scrape` subcommand:

```bash
github-bounty-scraper --max-issues 50 --dry-run scrape
github-bounty-scraper scrape --max-issues 50 --dry-run
```

Useful scrape options:

| Option | Purpose |
| --- | --- |
| `--since YYYY-MM-DD` | Only search issues updated on or after a date |
| `--max-issues N` | Cap total issues processed in a run |
| `--mode strict|opportunistic` | Choose precision-first or recall-first filtering |
| `--query TEXT` | Override configured search queries with one query |
| `--no-cache` | Re-enrich even if TTL caches would skip records |
| `--output-format text|markdown|json` | Select output format |
| `--output-file NAME` | Write `NAME.md` or `NAME.json` where supported |
| `--db-path PATH` | Use a non-default SQLite database path |

## Data Model

The primary SQLite database is `bounty_stats.db`, which is ignored by git.

Important `issue_stats` fields:

| Field | Meaning |
| --- | --- |
| `issue_url` | GitHub issue URL, primary key |
| `title`, `repo_name` | Enriched issue metadata |
| `numeric_amount` | Parsed USD amount; `-1.0` means bounty cue exists but amount is unknown |
| `lead_mode` | `strict`, `opportunistic`, `closed_historical`, or `vibe_only` |
| `escrow_verified` | Positive escrow or payout signal detected |
| `score` | Composite heuristic score |
| `vibe_score`, `vibe_reason` | Optional Gemini assessment |

Rows inserted only by `vibe-check` are marked `lead_mode='vibe_only'`. They are intentionally excluded from `inspect-leads` and dataset export until a scrape enriches title, repository, amount, and signal fields.

## Model Workflow

Model and dataset files are generated locally and ignored by git.

Recommended workflow:

```bash
github-bounty-scraper scrape --mode opportunistic --log-raw-candidates --max-issues 1000
github-bounty-scraper vibe-check --mode unscored --limit 500 --concurrency 3
github-bounty-scraper dump-dataset --out-csv bounty_dataset.csv
python tools/balance_dataset.py --input bounty_dataset.csv --output bounty_dataset_train.csv
python tools/train_bounty_model.py
```

Generated files include:

- `bounty_stats.db`
- `exploration_raw.jsonl`
- `bounty_dataset*.csv`
- `bounty_model.pkl`
- `bounty_model.pkl.sha256`
- `best_threshold.json`

Keep these local, publish them as release assets when needed, or regenerate them from the documented pipeline.

## Project Layout

```text
github_bounty_scraper/
  bounty.py       Amount parsing and snipe detection
  cli.py          Argparse command surface
  config.py       Runtime configuration and signal loading
  core.py         Async discovery, enrichment, filtering, scoring, persistence
  db.py           SQLite schema, migrations, inspection, dataset export
  discovery.py    GitHub REST search
  graphql.py      GitHub GraphQL enrichment and rate limiting
  output.py       Text, Markdown, and JSON output
  scoring.py      Composite scoring model
  signals.py      Hard filters and soft signal extraction
  vibe.py         Gemini-based candidate scoring
scripts/          Operational helper scripts
tools/            Dataset and model utilities
tests/            Unit and integration tests
```

## Development Checks

Run the same checks as CI:

```bash
ruff check github_bounty_scraper tests scripts tools
ruff format --check github_bounty_scraper tests scripts tools
mypy github_bounty_scraper
pytest --cov=github_bounty_scraper --cov-fail-under=80
```

## Repository Hygiene

The repository tracks source, tests, configuration, and documentation. It does not track:

- SQLite databases
- model pickle files
- training datasets
- scrape outputs
- logs
- local scratch files
- environment files

If a local run produces a valuable model or dataset, attach it to a GitHub release or store it in an external artifact store rather than committing it to the repository.

## License

MIT. See [LICENSE](LICENSE).
