# Contributing to GitHub Bounty Scraper

Welcome! This guide will help you get started with development and testing.

## Dev Environment Setup

1. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS/Linux
   source .venv/bin/activate
   ```

2. **Install in editable mode with dev dependencies:**
   ```bash
   pip install -e ".[dev]"
   ```

## Code Quality Checks

Run these before submitting any changes:

- **Linting:**
  ```bash
  ruff check .
  ```

- **Type Checking:**
  ```bash
  mypy github_bounty_scraper
  ```

## Smoke Testing

Verify the pipeline with these commands:

### Strict Mode (Default)
High-precision leads with strict structural filters.
```bash
github-bounty-scraper scrape --since 2025-01-01 --max-issues 50 --mode strict
```

### Opportunistic Mode
Loosened filters for broad signal mining.
```bash
github-bounty-scraper scrape --since 2023-01-01 --max-issues 50 --mode opportunistic --log-raw-candidates
```

### Inspecting Leads
```bash
github-bounty-scraper inspect-leads --mode opportunistic --limit 10
```

## Exploration Data
Raw candidates are logged to `exploration_raw.jsonl` when `--log-raw-candidates` is used. Use the analysis tool to inspect them:
```bash
python tools/analyze_raw.py
```
