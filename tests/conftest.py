import pytest
from github_bounty_scraper.config import ScraperConfig

@pytest.fixture
def minimal_config():
    """Return a ScraperConfig with deterministic test defaults."""
    return ScraperConfig(
        min_stars=5,
        weight_amount=0.4,
        weight_recency=0.25,
        weight_activity=0.2,
        weight_escrow_strength=0.15,
        max_sane_amount=100_000,
    )
