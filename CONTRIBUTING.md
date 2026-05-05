# Contributing

## Prerequisites
- Python 3.11+
- A GitHub personal access token with `repo` and `read:org` scopes
- (Optional) A Gemini API key for `vibe-check`

## Setup

```bash
git clone https://github.com/blairmichaelg/github-bounty-scraper.git
cd github-bounty-scraper
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

## Code Style
- Follow PEP 8. Run `ruff check .` before submitting.
- All async functions must be properly awaited — no sync blocking calls in `async def`.
- New config fields go in `ScraperConfig` with a sensible default and a comment.
- New signal types go in `signals_config.json`, not hardcoded in Python.

## Submitting Changes
1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/your-feature`.
3. Make your changes with clear, atomic commits.
4. Open a pull request against `master` with a description of what changed and why.

## Signal List Updates
To add new escrow keywords, kill labels, or aggregator repos, edit
`signals_config.json` directly. All signal strings are automatically
lowercased at load time.
