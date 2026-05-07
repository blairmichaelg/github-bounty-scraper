from __future__ import annotations

import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.discovery import build_search_queries, fetch_rest_search


def test_build_search_queries_default():
    config = ScraperConfig()
    queries = build_search_queries(config)
    assert len(queries) > 0
    assert any("is:open" in q for q in queries)
    assert any("is:closed" in q for q in queries)


def test_build_search_queries_with_filters():
    config = ScraperConfig(min_stars=100, since="2024-01-01", languages=["Python", "Go"])
    queries = build_search_queries(config)
    for q in queries:
        assert "stars:>=100" in q
        assert "updated:>=2024-01-01" in q
        # Languages are combined in chunks of 3, so with 2 languages it should be one clause
        assert "language:Python OR language:Go" in q


def test_build_search_queries_chunking():
    # 4 languages should produce 2 chunks (size=3)
    config = ScraperConfig(languages=["L1", "L2", "L3", "L4"])
    # If there's 1 base query, it should produce 2 expanded queries
    config.search_queries = ["base"]
    queries = build_search_queries(config)
    assert len(queries) == 2
    assert "(language:L1 OR language:L2 OR language:L3)" in queries[0]
    assert "(language:L4)" in queries[1]


@pytest.mark.asyncio
async def test_fetch_rest_search_success():
    class MockResponse:
        status = 200
        ok = True

        async def json(self):
            return {"items": [{"id": 1}]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockSession:
        def get(self, *args, **kwargs):
            return MockResponse()

    items = await fetch_rest_search(MockSession(), "token", "query", 1)
    assert len(items) == 1
    assert items[0]["id"] == 1


@pytest.mark.asyncio
async def test_fetch_rest_search_failure():
    class MockResponse:
        status = 500
        ok = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockSession:
        def get(self, *args, **kwargs):
            return MockResponse()

    items = await fetch_rest_search(MockSession(), "token", "query", 1)
    assert items == []
