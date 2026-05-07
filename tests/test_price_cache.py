from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from github_bounty_scraper.price_cache import _PRICE_CACHE, HARDCODED_FALLBACKS, get_usd_price, refresh_prices


def test_get_usd_price_fallback():
    # Ensure cache is empty for this symbol
    if "eth" in _PRICE_CACHE:
        del _PRICE_CACHE["eth"]
    price = get_usd_price("eth")
    assert price == HARDCODED_FALLBACKS["eth"]


def test_get_usd_price_cache_hit():
    now = time.time()
    _PRICE_CACHE["btc"] = (60000.0, now)
    price = get_usd_price("btc")
    assert price == 60000.0


def test_get_usd_price_cache_expired():
    expired = time.time() - 4000
    _PRICE_CACHE["btc"] = (60000.0, expired)
    price = get_usd_price("btc")
    # Should fall back to hardcoded (which is 0.0 for btc)
    assert price == 0.0


@pytest.mark.asyncio
async def test_refresh_prices_success():
    with patch("aiohttp.ClientSession") as mock_session_class:
        # mock_session is what aiohttp.ClientSession(...) returns
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # Mock Response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ethereum": {"usd": 4000.0}})

        # Mock Context Manager for session.get
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock()

        mock_session.get.return_value = mock_cm

        await refresh_prices(["eth"])
        assert _PRICE_CACHE["eth"][0] == 4000.0
        assert get_usd_price("eth") == 4000.0


@pytest.mark.asyncio
async def test_refresh_prices_coinbase_fallback():
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value.__aenter__.return_value = mock_session

        # 1. Mock CoinGecko failure (status 500)
        mock_resp_cg = MagicMock()
        mock_resp_cg.status = 500

        # 2. Mock Coinbase success (status 200)
        mock_resp_cb = MagicMock()
        mock_resp_cb.status = 200
        mock_resp_cb.json = AsyncMock(return_value={"data": {"rates": {"ETH": "0.00025"}}})  # 1/4000

        # Mock Context Managers
        mock_cm_cg = MagicMock()
        mock_cm_cg.__aenter__ = AsyncMock(return_value=mock_resp_cg)
        mock_cm_cg.__aexit__ = AsyncMock()

        mock_cm_cb = MagicMock()
        mock_cm_cb.__aenter__ = AsyncMock(return_value=mock_resp_cb)
        mock_cm_cb.__aexit__ = AsyncMock()

        # The first call to get() is for CoinGecko, second is for Coinbase
        mock_session.get.side_effect = [mock_cm_cg, mock_cm_cb]

        await refresh_prices(["eth"])
        # 1/0.00025 = 4000
        assert get_usd_price("eth") == 4000.0
