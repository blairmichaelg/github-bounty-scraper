import asyncio

import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.core import process_issue
from github_bounty_scraper.graphql import TokenBucket


@pytest.mark.asyncio
async def test_process_issue_skip():
    config = ScraperConfig()
    # Mock issue that fails URL check
    issue = {"html_url": "https://github.com/bad/url"}
    res = await process_issue(
        None, TokenBucket(1, 1), issue, None, asyncio.Semaphore(1), config, {"aggregator_repos": []}, None, set()
    )
    assert res is None
