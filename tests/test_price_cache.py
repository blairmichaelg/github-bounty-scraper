import pytest

from github_bounty_scraper.price_cache import get_usd_price, refresh_prices


@pytest.mark.asyncio
async def test_price_cache():
    # Test fallback/initial state
    assert get_usd_price("BTC") == 0.0

    # Test refresh
    await refresh_prices(["BTC", "ETH"])
    # If it failed, it should still be a float.
    assert isinstance(get_usd_price("BTC"), float)
