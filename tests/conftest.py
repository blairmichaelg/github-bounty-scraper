import pytest
from github_bounty_scraper.config import ScraperConfig

@pytest.fixture
def minimal_config():
    """Return a ScraperConfig with deterministic test defaults."""
    return ScraperConfig(
        min_stars=5,
        weight_amount=0.30,
        weight_recency=0.20,
        weight_activity=0.20,
        weight_escrow_strength=0.15,
        w_repo_reputation=0.10,
        w_vibe=0.05,
        max_sane_amount=100_000,
    )
