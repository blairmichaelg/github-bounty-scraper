# Contributing

## Requirements

- Python 3.11 or newer
- GitHub CLI or a GitHub personal access token
- Optional Gemini API key for vibe-check development

## Setup

```bash
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
```

On macOS or Linux, activate with `source venv/bin/activate`.

## Local Checks

Run the same checks used by CI:

```bash
ruff check github_bounty_scraper tests scripts tools
ruff format --check github_bounty_scraper tests scripts tools
mypy github_bounty_scraper
pytest --cov=github_bounty_scraper --cov-fail-under=80
```

## Development Guidelines

- Keep async code non-blocking. Use `asyncio.to_thread` for unavoidable file I/O from async paths.
- Add new runtime settings to `ScraperConfig` and document them in `README.md` when user-facing.
- Add new signal phrases to `signals_config.json` instead of hardcoding them in Python.
- Keep generated artifacts out of git. Databases, datasets, model pickle files, logs, and scrape outputs are ignored intentionally.
- Prefer focused tests for parser, scoring, DB migration, CLI, and pipeline behavior when changing those areas.

## Pull Requests

1. Create a feature branch.
2. Keep commits focused and descriptive.
3. Include the verification commands you ran in the PR description.
4. Call out any schema, config, CLI, or artifact-policy changes explicitly.
