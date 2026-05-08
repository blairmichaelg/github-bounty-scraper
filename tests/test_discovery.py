from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.discovery import build_search_queries, discover_issues_stream, fetch_rest_search


def test_build_search_queries_default(cfg):
    queries = build_search_queries(cfg)
    assert len(queries) > 0
    assert any("is:open" in q for q in queries)
    assert any("is:closed" in q for q in queries)


def test_build_search_queries_with_filters(cfg):
    cfg.min_repo_stars = 100
    cfg.since = "2024-01-01"
    cfg.languages = ["Python", "Go"]
    queries = build_search_queries(cfg)
    for q in queries:
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


class TestQueryExpansionCap:
    """Cover the query cap warning path (L86-91)."""

    def test_query_cap_truncates(self, cfg):
        """When expansion exceeds max_expanded_queries, it is truncated with a warning."""
        # 20 base queries × 1 lang chunk = 20. Set cap to 5.
        cfg.search_queries = [f"query{i}" for i in range(20)]
        cfg.max_expanded_queries = 5
        cfg.languages = []
        queries = build_search_queries(cfg)
        assert len(queries) == 5


class TestFetchRestSearchRetries:
    """Cover rate limit, client error, and retry exhaustion paths."""

    @pytest.mark.asyncio
    async def test_rate_limit_retries(self, mock_aiohttp_session):
        """429 triggers retry with backoff, then 200 succeeds."""
        mock_429 = AsyncMock()
        mock_429.status = 429
        mock_429.ok = False

        mock_200 = AsyncMock()
        mock_200.status = 200
        mock_200.ok = True
        mock_200.json = AsyncMock(return_value={"items": [{"id": 99}]})

        mock_aiohttp_session.get.return_value.__aenter__.side_effect = [mock_429, mock_200]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            items = await fetch_rest_search(mock_aiohttp_session, "token", "query", 1)

        assert len(items) == 1
        assert items[0]["id"] == 99

    @pytest.mark.asyncio
    async def test_client_error_retries(self, mock_aiohttp_session):
        """ClientError triggers retry, second attempt succeeds."""
        mock_200 = AsyncMock()
        mock_200.status = 200
        mock_200.ok = True
        mock_200.json = AsyncMock(return_value={"items": [{"id": 42}]})

        mock_aiohttp_session.get.return_value.__aenter__.side_effect = [
            aiohttp.ClientError("transient"),
            mock_200,
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            items = await fetch_rest_search(mock_aiohttp_session, "token", "query", 1)

        assert len(items) == 1
        assert items[0]["id"] == 42

    @pytest.mark.asyncio
    async def test_retries_exhausted_returns_empty(self, mock_aiohttp_session):
        """After all retries are exhausted, returns empty list."""
        mock_429 = AsyncMock()
        mock_429.status = 429
        mock_429.ok = False

        mock_aiohttp_session.get.return_value.__aenter__.return_value = mock_429

        with patch("asyncio.sleep", new_callable=AsyncMock):
            items = await fetch_rest_search(mock_aiohttp_session, "token", "query", 1, retries=1)

        assert items == []


class TestDiscoverIssuesStream:
    """Cover discover_issues_stream pagination, dedup, and max_issues_per_run."""

    @pytest.mark.asyncio
    async def test_yields_items_and_deduplicates(self):
        """Stream yields unique items and skips duplicates."""
        from github_bounty_scraper.config import SearchConfig
        cfg = ScraperConfig(
            search=SearchConfig(
                search_queries=["q1"],
                languages=[],
                max_pages_per_query=1,
                max_issues_per_run=0,
                search_delay_seconds=0,
            )
        )

        page_items = [
            {"html_url": "http://1", "id": 1},
            {"html_url": "http://2", "id": 2},
            {"html_url": "http://1", "id": 1},  # dup
        ]

        async def mock_fetch(*args, **kwargs):
            return page_items

        with patch("github_bounty_scraper.discovery.fetch_rest_search", side_effect=mock_fetch):
            results = []
            async for item in discover_issues_stream(cfg):
                results.append(item)

        assert len(results) == 2
        assert results[0]["html_url"] == "http://1"
        assert results[1]["html_url"] == "http://2"

    @pytest.mark.asyncio
    async def test_stops_at_max_issues_per_run(self):
        """Stream stops after max_issues_per_run unique items."""
        from github_bounty_scraper.config import SearchConfig
        cfg = ScraperConfig(
            search=SearchConfig(
                search_queries=["q1", "q2"],
                languages=[],
                max_pages_per_query=1,
                max_issues_per_run=3,
                search_delay_seconds=0,
            )
        )

        page1 = [{"html_url": f"http://{i}", "id": i} for i in range(3)]
        page2 = [{"html_url": f"http://{i + 3}", "id": i + 3} for i in range(3)]
        call_count = 0

        async def mock_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return page1 if call_count == 1 else page2

        with patch("github_bounty_scraper.discovery.fetch_rest_search", side_effect=mock_fetch):
            results = []
            async for item in discover_issues_stream(cfg):
                results.append(item)

        # First query yields 3 items, second query should be skipped due to max_issues_per_run
        assert len(results) == 3
