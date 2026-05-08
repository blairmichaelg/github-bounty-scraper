"""
ScrapeStatistics - Tracks metrics and funnel efficiency for a scraper run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Counter


@dataclass
class ScrapeStatistics:
    """Tracks counts and categories of issues processed during a pipeline run."""

    start_time: float = field(default_factory=time.time)
    discovered: int = 0
    processed: int = 0
    graduated: int = 0
    disqualified: Counter[str] = field(default_factory=Counter)
    skipped_cache: int = 0
    vibe_checks: int = 0
    vibe_cache_hits: int = 0
    errors: int = 0

    def record_disqualified(self, reason: str) -> None:
        """Increment count for a specific disqualification reason."""
        # Simplify reason strings for cleaner reporting
        clean_reason = reason.split(":")[0].strip().lower()
        if "kill label" in clean_reason:
            clean_reason = "kill label"
        elif "negative filter" in clean_reason:
            clean_reason = "negative filter"
        elif "archived" in clean_reason:
            clean_reason = "archived"
        elif "low-star" in clean_reason:
            clean_reason = "low-star repo"
        elif "dead repo" in clean_reason:
            clean_reason = "dead repo"
        elif "no bounty signal in title" in clean_reason:
            clean_reason = "no title signal"
        
        self.disqualified[clean_reason] += 1

    @property
    def elapsed(self) -> float:
        """Total seconds elapsed since instantiation."""
        return time.time() - self.start_time

    def summary(self) -> str:
        """Return a human-readable summary of the scrape metrics."""
        lines = [
            "─── Scrape funnel summary ───",
            f"Discovered:   {self.discovered}",
            f"Processed:    {self.processed}",
            f"  Graduated:  {self.graduated}",
            f"  Disqualified: {sum(self.disqualified.values())}",
        ]
        
        for reason, count in self.disqualified.most_common():
            lines.append(f"    - {reason}: {count}")
            
        lines.append(f"Skipped (Cache): {self.skipped_cache}")
        lines.append(f"Vibe Checks:     {self.vibe_checks} ({self.vibe_cache_hits} cached)")
        lines.append(f"Errors:          {self.errors}")
        lines.append(f"Elapsed:         {self.elapsed:.1f}s")
        
        return "\n".join(lines)
