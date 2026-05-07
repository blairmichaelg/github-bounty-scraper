from __future__ import annotations

import pytest

from github_bounty_scraper.discovery import build_search_queries, fetch_rest_search


def test_build_search_queries_default(cfg):
    queries = build_search_queries(cfg)
    assert len(queries) > 0
    assert any("is:open" in q for q in queries)
    assert any("is:closed" in q for q in queries)


def test_build_search_queries_with_filters(cfg):
    cfg.min_stars = 100
    cfg.since = "2024-01-01"
    cfg.languages = ["Python", "Go"]
    queries = build_search_queries(cfg)
    for q in queries:
        assert "stars:>=100" in q
        assert "updated:>=2024-01-01" in q
        # Languages are combined in chunks of 3, so with 2 languages it should be one clause
        assert "language:Python OR language:Go" in q


def test_build_search_queries_chunking(cfg):
    # 4 languages should produce 2 chunks (size=3)
    cfg.languages = ["L1", "L2", "L3", "L4"]
    # If there's 1 base query, it should produce 2 expanded queries
    cfg.search_queries = ["base"]
    queries = build_search_queries(cfg)
    assert len(queries) == 2
    assert "(language:L1 OR language:L2 OR language:L3)" in queries[0]
    assert "(language:L4)" in queries[1]


@pytest.mark.asyncio
async def test_fetch_rest_search_success(mock_aiohttp_session):
    mock_aiohttp_session.get.return_value.__aenter__.return_value.json.return_value = {"items": [{"id": 1}]}
    items = await fetch_rest_search(mock_aiohttp_session, "token", "query", 1)
    assert len(items) == 1
    assert items[0]["id"] == 1


@pytest.mark.asyncio
async def test_fetch_rest_search_failure(mock_aiohttp_session):
    mock_aiohttp_session.get.return_value.__aenter__.return_value.status = 500
    mock_aiohttp_session.get.return_value.__aenter__.return_value.ok = False
    items = await fetch_rest_search(mock_aiohttp_session, "token", "query", 1)
    assert items == []
