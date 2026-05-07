from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.discovery import build_search_queries


def test_build_search_queries():
    config = ScraperConfig(min_stars=10)
    queries = build_search_queries(config)
    assert len(queries) > 0
    assert "stars:>=10" in queries[0]
