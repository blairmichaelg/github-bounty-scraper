"""
GitHub Bounty Scraper — thin entry-point wrapper.

Usage::

    python scraper.py [OPTIONS]

For full option list see ``python scraper.py --help``.
"""

import asyncio
import sys

# ─── Encoding safety (Windows terminals) ────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from github_bounty_scraper.cli import parse_args  # noqa: E402
from github_bounty_scraper.core import run_pipeline  # noqa: E402

if __name__ == "__main__":
    config = parse_args()
    asyncio.run(run_pipeline(config))
