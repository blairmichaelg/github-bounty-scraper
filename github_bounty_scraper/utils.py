"""
General utilities for the GitHub Bounty Scraper.
"""

from __future__ import annotations

import datetime

def parse_github_timestamp(raw: str | None) -> datetime.datetime | None:
    """Parse a GitHub ISO-8601 timestamp string into a UTC datetime."""
    if not raw:
        return None
    try:
        # Standard GitHub format: 2026-05-08T17:42:44Z
        return datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None

def timestamp_to_float(dt: datetime.datetime | None) -> float:
    """Convert a datetime to a Unix timestamp float, or 0.0 if None."""
    if dt is None:
        return 0.0
    return dt.timestamp()
