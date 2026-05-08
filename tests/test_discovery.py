from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.discovery import build_search_queries, discover_issues_stream


def test_build_search_queries_default(cfg):
    queries = build_search_queries(cfg)
    assert len(queries) > 0
    assert any("is:open" in q for q in queries)
    assert any("is:closed" in q for q in queries)


def test_build_search_queries_with_filters(cfg):
    cfg.search.min_repo_stars = 100
    cfg.search.since = "2024-01-01"
    cfg.search.languages = ["Python", "Go"]
    queries = build_search_queries(cfg)
    for q in queries:
        assert "updated:>=2024-01-01" in q
        # Languages are combined in chunks of 3, so with 2 languages it should be one clause
        assert "language:Python OR language:Go" in q


def test_build_search_queries_chunking(cfg):
    # 4 languages should produce 2 chunks (size=3)
    cfg.search.languages = ["L1", "L2", "L3", "L4"]
    # If there's 1 base query, it should produce 2 expanded queries
    cfg.search.search_queries = ["base"]
    queries = build_search_queries(cfg)
    assert len(queries) == 2
    assert "(language:L1 OR language:L2 OR language:L3)" in queries[0]
    assert "(language:L4)" in queries[1]


class TestQueryExpansionCap:
    """Cover the query cap warning path."""

    def test_query_cap_truncates(self, cfg):
        """When expansion exceeds max_expanded_queries, it is truncated with a warning."""
        # 20 base queries × 1 lang chunk = 20. Set cap to 5.
        cfg.search.search_queries = [f"query{i}" for i in range(20)]
        cfg.search.max_expanded_queries = 5
        cfg.search.languages = []
        queries = build_search_queries(cfg)
        assert len(queries) == 5


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
                min_repo_stars=0,
            )
        )

        page_items = [
            {"html_url": "http://1", "id": 1},
            {"html_url": "http://2", "id": 2},
            {"html_url": "http://1", "id": 1},  # dup
        ]

        async def mock_fetch(*args, **kwargs):
            return page_items, None

        with patch("github_bounty_scraper.discovery.fetch_graphql_search", side_effect=mock_fetch):
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
                min_repo_stars=0,
            )
        )

        page1 = [{"html_url": f"http://{i}", "id": i} for i in range(3)]
        page2 = [{"html_url": f"http://{i + 3}", "id": i + 3} for i in range(3)]
        call_count = 0

        async def mock_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return (page1 if call_count == 1 else page2), None

        with patch("github_bounty_scraper.discovery.fetch_graphql_search", side_effect=mock_fetch):
            results = []
            async for item in discover_issues_stream(cfg):
                results.append(item)

        # First query yields 3 items, second query should be skipped due to max_issues_per_run
        assert len(results) == 3
