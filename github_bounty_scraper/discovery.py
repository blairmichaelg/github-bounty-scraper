"""
Discovery layer - builds search queries and fetches issue candidates
from the GitHub REST Search API. Supports both open and closed issues
to facilitate training data collection.
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

    Note: Closed issues are included in the base set to provide high-quality
    historical positives for fine-tuning datasets.
    """
    base_queries = config.search_queries
    if not base_queries:
        # Sensible built-in fallback if config has no queries.
        base_queries = [
            # Open — primary pipeline targets
            'is:open is:issue label:bounty',
            'is:open is:issue label:gitcoin',
            'is:open is:issue "bounty" "USDC" OR "DAI" OR "ETH"',
            'is:open is:issue "paid on merge" OR "reward on close"',
            'is:open is:issue "escrow" OR "escrow locked" OR "smart contract funded"',
            'is:open is:issue "gitcoin" OR "bounties network" OR "radicle"',
            'is:open is:issue "prize" OR "hackathon reward" OR "tip"',
            # Closed — verified historical positives (gold-standard training data)
            'is:closed is:issue label:bounty',
            'is:closed is:issue label:gitcoin',
            'is:closed is:issue "bounty" "USDC" OR "DAI" OR "ETH"',
            'is:closed is:issue "paid on merge" OR "reward on close"',
            'is:closed is:issue "escrow" "merged" OR "completed" OR "paid"',
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
        "User-Agent": "github-bounty-scraper",
    }
    params: dict[str, str | int] = {
        "q": query,
        "sort": sort_by,
        "order": "desc",
        "per_page": per_page,
        "page": page,
    }

    attempt = 0
    while attempt <= retries:
        try:
            async with session.get(
                "https://api.github.com/search/issues",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status in (403, 429):
                    retry_delays = [5, 10, 20, 40]
                    wait_t = retry_delays[attempt] if attempt < len(retry_delays) else 40
                    log.warning("Rate limit — sleeping %ds (attempt %d/%d)…",
                                wait_t, attempt + 1, retries + 1)
                    await asyncio.sleep(wait_t)
                    attempt += 1
                    continue
                if not resp.ok:
                    log.warning("Search HTTP %d page %d", resp.status, page)
                    return []
                data = await resp.json()
                return data.get("items", [])
        except aiohttp.ClientError as exc:
            log.warning("Search error (attempt %d): %s", attempt, exc)
            await asyncio.sleep(2 * (attempt + 1))
            attempt += 1
    log.error("Search failed after %d retries.", retries + 1)
    return []


# ─── Discovery orchestrator ─────────────────────────────────────────
async def discover_issues(config: ScraperConfig) -> list[dict]:
    """Run all search queries with pagination, dedup by URL.

    Respects ``config.max_pages_per_query`` and ``config.max_issues``.
    Stops paginating a query early when a page returns fewer than
    ``per_page`` results.
    """
    queries = build_search_queries(config)
    log.info("Discovery: running %d search queries (~%d API calls max, %d min) …",
             len(queries), len(queries) * config.max_pages_per_query, len(queries))

    unique_issues: dict[str, dict] = {}
    per_page = 100

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        for qi, query in enumerate(queries, 1):
            if config.max_issues and len(unique_issues) >= config.max_issues:
                break
            await asyncio.sleep(config.search_delay_seconds)

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

                await asyncio.sleep(config.search_delay_seconds / 2)

                # Early stop: page is not full.
                if len(items) < per_page:
                    break

    issues = list(unique_issues.values())
    log.info("Discovery complete: %d unique issues found.", len(issues))
    return issues
