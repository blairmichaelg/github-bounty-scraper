"""
Live crypto price normalization and caching.
"""

import time
import asyncio
import aiohttp
from typing import Any
from .log import get_logger

log = get_logger()

# Mapping lowercase symbol -> (usd_price, fetched_epoch)
_PRICE_CACHE: dict[str, tuple[float, float]] = {}

# Authoritative backup if live fetching fails.
HARDCODED_FALLBACKS: dict[str, float] = {
    "eth": 3000.0,
    "sol": 150.0,
    "op": 2.5,
    "arb": 1.0,
    "matic": 0.7,
    "dai": 1.0,
    "weth": 3000.0,
    "strk": 0.5,
    "roxn": 0.01,
}

# Mapping symbols to CoinGecko IDs
SYMBOL_TO_ID: dict[str, str] = {
    "eth": "ethereum",
    "sol": "solana",
    "op": "optimism",
    "arb": "arbitrum",
    "matic": "matic-network",
    "dai": "dai",
    "weth": "weth",
    "strk": "starknet",
    "roxn": "roxonn",
}

async def refresh_prices(symbols: list[str]) -> None:
    """Fetch live prices for the given symbols from CoinGecko.
    
    Updates _PRICE_CACHE on success. Never raises.
    """
    id_list: list[str] = [SYMBOL_TO_ID[s.lower()] for s in symbols if s.lower() in SYMBOL_TO_ID]
    if not id_list:
        return

    url = "https://api.coingecko.com/api/v3/simple/price"
    params: dict[str, str] = {
        "ids": ",".join(id_list),
        "vs_currencies": "usd"
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    now = time.time()
                    # Reverse map ID back to symbol
                    id_to_symbol = {v: k for k, v in SYMBOL_TO_ID.items()}
                    for coin_id, prices in data.items():
                        symbol = id_to_symbol.get(coin_id)
                        if symbol:
                            _PRICE_CACHE[symbol] = (float(prices["usd"]), now)
                    log.info("price cache: refreshed for %d symbols (live)", len(data))
                else:
                    log.warning("price cache: CoinGecko HTTP %d — using fallbacks", resp.status)
    except Exception as exc:
        log.warning("price cache: fetch failed (%s) — using fallbacks", exc)

def get_usd_price(symbol: str) -> float:
    """Return the cached or fallback USD price for a symbol."""
    sym = symbol.lower()
    
    # Check cache (1 hour TTL)
    if sym in _PRICE_CACHE:
        price, fetched_at = _PRICE_CACHE[sym]
        if time.time() - fetched_at < 3600:
            return price
            
    return HARDCODED_FALLBACKS.get(sym, 0.0)
