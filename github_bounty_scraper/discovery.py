"""
Discovery layer — builds search queries and fetches issue candidates
from the GitHub REST Search API.
"""

from __future__ import annotations

import asyncio

import aiohttp

from .config import ScraperConfig
from .log import get_logger

log = get_logger()


# ─── Query builder ───────────────────────────────────────────────────
def build_search_queries(config: ScraperConfig) -> list[str]:
    """Build the list of GitHub search queries from config + CLI filters.

    Each base query from ``config.search_queries`` is augmented with:
    - ``language:X`` for each entry in ``config.languages``.
    - ``stars:>N`` from ``config.min_stars``.
    - ``updated:>=YYYY-MM-DD`` from ``config.since``.
    """
    base_queries = config.search_queries
    if not base_queries:
        # Sensible built-in fallback if config has no queries.
        base_queries = [
            'is:open is:issue label:bounty',
            'is:open is:issue "bounty" OR "reward" OR "paid on merge"',
            'is:open is:issue "USDC" OR "crypto bounty"',
            'is:open is:issue "escrow locked" OR "smart contract funded"',
        ]

    # Build suffix fragments.
    suffixes: list[str] = []
    if config.min_stars and config.min_stars > 0:
        suffixes.append(f"stars:>={config.min_stars}")
    if config.since:
        suffixes.append(f"updated:>={config.since}")

    suffix = " ".join(suffixes)

    expanded: list[str] = []
    for q in base_queries:
        if config.languages:
            for lang in config.languages:
                parts = [q]
                parts.append(f"language:{lang}")
                if suffix:
                    parts.append(suffix)
                expanded.append(" ".join(parts))
        else:
            if suffix:
                expanded.append(f"{q} {suffix}")
            else:
                expanded.append(q)

    max_eq = config.max_expanded_queries
    if len(expanded) > max_eq:
        log.warning(
            "Query expansion produced %d queries (cap: %d). "
            "Consider fewer --language flags or base queries.",
            len(expanded), max_eq,
        )
        expanded = expanded[:max_eq]

    return expanded


# ─── REST search fetch ───────────────────────────────────────────────
async def fetch_rest_search(
    session: aiohttp.ClientSession,
    token: str,
    query: str,
    page: int,
    per_page: int = 100,
    sort_by: str = "updated",
    retries: int = 3,
) -> list[dict]:
    """Fetch one page of GitHub issue search results via the REST API."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {
        "q": query,
        "sort": sort_by,
        "order": "desc",
        "per_page": per_page,
        "page": page,
    }

    for attempt in range(retries):
        try:
            async with session.get(
                "https://api.github.com/search/issues",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status in (403, 429):
                    wait_t = 5 * (2 ** attempt)
                    log.warning("Search rate limit hit — sleeping %ds …", wait_t)
                    await asyncio.sleep(wait_t)
                    continue
                if not resp.ok:
                    log.warning("Search HTTP %d for query page %d", resp.status, page)
                    return []
                data = await resp.json()
                return data.get("items", [])
        except aiohttp.ClientError as exc:
            log.warning("Search HTTP error (attempt %d): %s", attempt, exc)
            await asyncio.sleep(2 * (attempt + 1))
    log.error(
        "Search query failed after %d retries (last status: rate-limit "
        "or network error). Results may be incomplete.",
        retries,
    )
    return []


# ─── Discovery orchestrator ─────────────────────────────────────────
async def discover_issues(config: ScraperConfig) -> list[dict]:
    """Run all search queries with pagination, dedup by URL.

    Respects ``config.max_pages_per_query`` and ``config.max_issues``.
    Stops paginating a query early when a page returns fewer than
    ``per_page`` results.
    """
    queries = build_search_queries(config)
    log.info("Discovery: running %d search queries (max %d pages each, ~%d API calls min) …",
             len(queries), config.max_pages_per_query,
             len(queries) * config.max_pages_per_query)

    unique_issues: dict[str, dict] = {}
    per_page = 100

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        for qi, query in enumerate(queries, 1):
            if config.max_issues and len(unique_issues) >= config.max_issues:
                break
            await asyncio.sleep(0.3)

            for page in range(1, config.max_pages_per_query + 1):
                if config.max_issues and len(unique_issues) >= config.max_issues:
                    break

                items = await fetch_rest_search(
                    session, config.github_token, query, page, per_page,
                    sort_by=config.sort_by,
                )

                new_count = 0
                for item in items:
                    url = item.get("html_url")
                    if url and url not in unique_issues:
                        unique_issues[url] = item
                        new_count += 1

                log.debug(
                    "  Query %d/%d page %d → %d items (%d new)",
                    qi, len(queries), page, len(items), new_count,
                )

                await asyncio.sleep(0.5)

                # Early stop: page is not full.
                if len(items) < per_page:
                    break

    issues = list(unique_issues.values())
    log.info("Discovery complete: %d unique issues found.", len(issues))
    return issues
