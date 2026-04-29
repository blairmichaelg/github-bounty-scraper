"""
GitHub Bounty Scraper — Async pipeline that discovers and scores funded
crypto bounties on GitHub Issues using GraphQL enrichment and SQLite caching.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("github-bounty-scraper")
except PackageNotFoundError:
    __version__ = "dev"
