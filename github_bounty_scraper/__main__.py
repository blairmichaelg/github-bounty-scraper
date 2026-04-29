"""
Entry point for ``python -m github_bounty_scraper``.
"""

import asyncio
import sys

# Encoding safety (Windows terminals).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from .cli import parse_args  # noqa: E402
from .core import run_pipeline  # noqa: E402


def main() -> None:
    config = parse_args()
    asyncio.run(run_pipeline(config))


if __name__ == "__main__":
    main()
