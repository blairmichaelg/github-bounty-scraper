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

if __name__ == "__main__":
    from github_bounty_scraper.__main__ import main
    main()
