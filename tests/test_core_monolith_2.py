import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.core import run_pipeline


@pytest.mark.asyncio
async def test_run_pipeline_empty():
    config = ScraperConfig()

    # Mocking discover_issues_stream to return nothing
    async def mock_discover(*args, **kwargs):
        if False:
            yield {}

    import github_bounty_scraper.core as core

    original = core.discover_issues_stream
    core.discover_issues_stream = mock_discover
    try:
        await run_pipeline(config)
    finally:
        core.discover_issues_stream = original
