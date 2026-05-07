from unittest.mock import AsyncMock, MagicMock

import pytest

from github_bounty_scraper.config import ScraperConfig


@pytest.fixture
def cfg():
    """Default ScraperConfig instance for testing."""
    return ScraperConfig()


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp.ClientSession with async context manager support."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.ok = True
    mock_response.json = AsyncMock(return_value={})
    mock_response.text = AsyncMock(return_value="")
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    session.post = MagicMock(return_value=mock_response)
    session.get = MagicMock(return_value=mock_response)
    return session


@pytest.fixture
def mock_token_bucket():
    """Mock token bucket (TokenBucket) with async consume."""
    bucket = MagicMock()
    bucket.consume = AsyncMock()
    return bucket


@pytest.fixture
async def mock_db_conn(tmp_path):
    """In-memory aiosqlite connection for isolated DB tests."""
    import aiosqlite

    from github_bounty_scraper.db import init_db

    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        yield conn


@pytest.fixture
def minimal_issue():
    """Minimal valid GitHub issue dict for pipeline tests."""
    import datetime

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "url": "https://github.com/test/repo/issues/1",
        "html_url": "https://github.com/test/repo/issues/1",
        "number": 1,
        "title": "$500 bounty for fixing memory leak",
        "body": "Fix the memory leak in the connection pool. Reward: $500 USDC.",
        "state": "OPEN",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "comments": {
            "totalCount": 2,
            "nodes": [
                {"body": "I can fix this.", "createdAt": "2026-01-02T00:00:00Z"},
                {"body": "Escrow set up on Immunefi.", "createdAt": "2026-01-03T00:00:00Z"},
            ],
        },
        "labels": {"nodes": [{"name": "bounty"}, {"name": "good first issue"}]},
        "repository": {
            "nameWithOwner": "test/repo",
            "url": "https://github.com/test/repo",
            "stargazerCount": 500,
            "forkCount": 50,
            "isArchived": False,
            "isDisabled": False,
            "isFork": False,
            "primaryLanguage": {"name": "Python"},
            "owner": {"__typename": "Organization"},
            "mentionableUsers": {"totalCount": 10},
            "createdAt": "2020-01-01T00:00:00Z",
            "pullRequests": {"nodes": [{"mergedAt": now_str}]},
        },
    }
