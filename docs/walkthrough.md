# Operational Walkthrough

This walkthrough describes the current production-oriented workflow for GitHub Bounty Scraper.

## 1. Prepare The Environment

```bash
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
```

Authenticate with GitHub:

```bash
gh auth login
```

Optional Gemini support:

```bash
copy .env.example .env
```

Then fill in `GEMINI_API_KEY` in `.env`.

## 2. Run A Focused Scrape

Start with a bounded run and inspect the results before scaling up:

```bash
github-bounty-scraper scrape --mode strict --since 2025-01-01 --max-issues 300 --output-format json --output-file results
github-bounty-scraper inspect-leads --mode all --limit 20
```

For broader scouting and future model labeling:

```bash
github-bounty-scraper scrape --mode opportunistic --since 2025-01-01 --max-issues 1000 --log-raw-candidates
```

## 3. Run Vibe Checks

Vibe checks score raw candidates from `exploration_raw.jsonl` and update `bounty_stats.db`.

```bash
github-bounty-scraper vibe-check --mode unscored --limit 500 --concurrency 3
```

Rows created only by vibe checks are marked `vibe_only`. They are not shown by `inspect-leads` and are not exported for training until a scrape enriches their title, repository, amount, and signal fields.

## 4. Export And Train

```bash
github-bounty-scraper dump-dataset --db-path bounty_stats.db --raw-file exploration_raw.jsonl --out-csv bounty_dataset.csv
python tools/balance_dataset.py --input bounty_dataset.csv --output bounty_dataset_train.csv
python tools/train_bounty_model.py
```

This produces local generated artifacts such as `bounty_model.pkl`, `bounty_model.pkl.sha256`, and `best_threshold.json`. These files are intentionally ignored by git.

## 5. Verify Before Commit

Run the full local quality gate:

```bash
ruff check github_bounty_scraper tests scripts tools
ruff format --check github_bounty_scraper tests scripts tools
mypy github_bounty_scraper
pytest --cov=github_bounty_scraper --cov-fail-under=80
```

## 6. Artifact Policy

Commit source, tests, configs, and docs only. Do not commit:

- `.env`
- SQLite databases
- raw exploration logs
- generated datasets
- trained model pickle files
- output reports
- scratch files

Publish valuable generated artifacts through GitHub Releases or another artifact store.
