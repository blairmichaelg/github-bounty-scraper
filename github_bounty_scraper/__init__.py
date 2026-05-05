"""
github-bounty-scraper: discover and score funded crypto bounties
on GitHub Issues.
"""
from .config import ScraperConfig, build_config
from .scoring import compute_score

__version__ = "2.0.7"

__all__ = ["ScraperConfig", "build_config", "compute_score", "__version__"]
