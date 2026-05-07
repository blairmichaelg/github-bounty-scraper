from __future__ import annotations

import pytest

from github_bounty_scraper.graphql import TokenBucket, fetch_graphql, run_graphql_audit


@pytest.mark.asyncio
async def test_token_bucket(mock_token_bucket):
    # The fixture is already mocked
    await mock_token_bucket.consume(1)
    mock_token_bucket.consume.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_graphql_failure(mock_aiohttp_session, mock_token_bucket):
    mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 500
    mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = False
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {"errors": ["Error"]}

    res = await fetch_graphql(mock_aiohttp_session, mock_token_bucket, "token", "query")
    assert res is None


@pytest.mark.asyncio
async def test_run_graphql_audit(mock_aiohttp_session, mock_token_bucket):
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {
        "data": {
            "repository": {
                "issue": {"id": "1", "timelineItems": {"nodes": [], "pageInfo": {}}},
                "pullRequests": {"nodes": [], "pageInfo": {}},
            }
        }
    }
    res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
    assert res["repository"]["issue"]["id"] == "1"
