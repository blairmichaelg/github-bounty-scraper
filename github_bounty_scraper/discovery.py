"""
Discovery layer - builds search queries and fetches issue candidates
from the GitHub REST Search API. Supports both open and closed issues
to facilitate training data collection.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import aiohttp

from .config import ScraperConfig
from .graphql import TokenBucket, fetch_graphql
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
    if config.query_override:
        base_queries = [config.query_override]
    else:
        base_queries = config.search_queries
    if not base_queries:
        # Sensible built-in fallback if config has no queries.
        base_queries = [
            # Open — primary pipeline targets
            "is:open is:issue label:bounty",
            "is:open is:issue label:gitcoin",
            'is:open is:issue "bounty" "USDC" OR "DAI" OR "ETH"',
            'is:open is:issue "paid on merge" OR "reward on close"',
            'is:open is:issue "escrow" OR "escrow locked" OR "smart contract funded"',
            'is:open is:issue "gitcoin" OR "bounties network" OR "radicle"',
            'is:open is:issue "prize" OR "hackathon reward" OR "tip"',
            # Closed — verified historical positives (gold-standard training data)
            "is:closed is:issue label:bounty",
            "is:closed is:issue label:gitcoin",
            'is:closed is:issue "bounty" "USDC" OR "DAI" OR "ETH"',
            'is:closed is:issue "paid on merge" OR "reward on close"',
            'is:closed is:issue "escrow" "merged" OR "completed" OR "paid"',
        ]

    # Build suffix fragments.
    suffixes: list[str] = []
    # Note: GitHub issue search does not support the 'stars' qualifier for repo stars.
    # This must be handled downstream during enrichment (repo health check).
    if config.since:
        suffixes.append(f"updated:>={config.since}")

    suffix = " ".join(suffixes)

    expanded: list[str] = []

    # GitHub search query length limit is 256 characters.
    # To reduce API calls, we combine languages using OR, but we chunk them
    # to avoid overly long queries if there are many languages.
    lang_chunks: list[str] = []
    if config.languages:
        chunk_size = 3
        for i in range(0, len(config.languages), chunk_size):
            chunk = config.languages[i : i + chunk_size]
            lang_chunks.append(" OR ".join(f"language:{lang}" for lang in chunk))
    else:
        lang_chunks = [""]

    for q in base_queries:
        for lang_clause in lang_chunks:
            parts = [q]
            if lang_clause:
                parts.append(f"({lang_clause})")
            if suffix:
                parts.append(suffix)
            expanded.append(" ".join(parts))

    max_eq = config.max_expanded_queries
    if len(expanded) > max_eq:
        log.warning(
            "Query expansion produced %d queries (cap: %d). Consider fewer base queries.",
            len(expanded),
            max_eq,
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
                    log.warning("Rate limit — sleeping %ds (attempt %d/%d)…", wait_t, attempt + 1, retries + 1)
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


async def fetch_graphql_search(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    token: str,
    query: str,
    first: int = 100,
    after: str | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch issues via GraphQL Search API for more data-dense discovery."""
    gql_query = """
    query($q: String!, $first: Int!, $after: String) {
      search(query: $q, type: ISSUE, first: $first, after: $after) {
        issueCount
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          ... on Issue {
            html_url: url
            number
            title
            state
            updatedAt
            repository {
              nameWithOwner
              stargazerCount
              isArchived
              isFork
            }
          }
        }
      }
    }
    """
    variables = {"q": query, "first": first, "after": after}
    data = await fetch_graphql(session, bucket, token, gql_query, variables)
    if not data or "search" not in data:
        return [], None

    search = data["search"]
    items = search.get("nodes", [])
    has_next = search.get("pageInfo", {}).get("hasNextPage", False)
    cursor = search.get("pageInfo", {}).get("endCursor") if has_next else None

    return items, cursor


# ─── Discovery orchestrator ─────────────────────────────────────────


async def discover_issues_stream(config: ScraperConfig) -> AsyncIterator[dict]:
    """Run all search queries with pagination using GraphQL Search, dedup by URL.

    Yields issues as they are discovered with basic metadata included.
    Respects ``config.max_pages_per_query`` and ``config.max_issues_per_run``.
    """
    queries = build_search_queries(config)
    log.info(
        "Discovery (GraphQL): running %d search queries (~%d API calls max, %d min) …",
        len(queries),
        len(queries) * config.max_pages_per_query,
        len(queries),
    )

    unique_urls: set[str] = set()
    first_per_page = 100
    bucket = TokenBucket(config.token_bucket_capacity, config.token_bucket_fill_rate)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for qi, query in enumerate(queries, 1):
            if config.max_issues_per_run and len(unique_urls) >= config.max_issues_per_run:
                break

            cursor = None
            for page in range(1, config.max_pages_per_query + 1):
                if config.max_issues_per_run and len(unique_urls) >= config.max_issues_per_run:
                    break

                items, cursor = await fetch_graphql_search(
                    session,
                    bucket,
                    config.github_token,
                    query,
                    first=first_per_page,
                    after=cursor,
                )

                if not items:
                    break

                new_count = 0
                for item in items:
                    # Early health check: skip archived or forked repos during discovery
                    repo_info = item.get("repository", {})
                    if repo_info.get("isArchived") or repo_info.get("isFork"):
                        continue

                    url = item.get("html_url")
                    if url and url not in unique_urls:
                        unique_urls.add(url)
                        new_count += 1
                        yield item

                log.debug(
                    "  Query %d/%d page %d → %d items (%d new)",
                    qi,
                    len(queries),
                    page,
                    len(items),
                    new_count,
                )

                if not cursor:
                    break

                await asyncio.sleep(config.search_delay_seconds / 2)

    log.info("Discovery complete: %d unique issues found.", len(unique_urls))
