"""
GraphQL API helpers — enrichment queries with PR pagination,
timeline pagination, and issue state checks.
"""

from __future__ import annotations

import asyncio
import datetime
import time

import aiohttp

from .log import get_logger

log = get_logger()

GRAPHQL_URL = "https://api.github.com/graphql"


# ─── Token-bucket rate limiter ──────────────────────────────────────
class TokenBucket:
    """Async token-bucket rate limiter for API calls."""

    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = float(capacity)
        self.timestamp = time.monotonic()
        self.lock = asyncio.Lock()

    async def consume(self, tokens: int = 1) -> None:
        """Wait until *tokens* are available, then consume them."""
        async with self.lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.timestamp
                self.timestamp = now
                self.tokens = min(
                    self.capacity, self.tokens + elapsed * self.fill_rate
                )
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                wait_time = (tokens - self.tokens) / self.fill_rate
                await asyncio.sleep(wait_time)


# ─── Low-level GraphQL fetch ────────────────────────────────────────
async def fetch_graphql(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    token: str,
    query: str,
    variables: dict | None = None,
    retries: int = 5,
) -> dict | None:
    """Execute a GraphQL query against the GitHub API with rate-limit retries."""
    await bucket.consume(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(retries):
        try:
            async with session.post(
                GRAPHQL_URL, json=payload, headers=headers
            ) as resp:
                if resp.status in (403, 429):
                    wait_t = 5 * (2 ** attempt)
                    log.warning("GraphQL rate limit hit — sleeping %ds …", wait_t)
                    await asyncio.sleep(wait_t)
                    continue
                if not resp.ok:
                    resp_text = await resp.text()
                    log.warning("GraphQL HTTP %d: %s", resp.status, resp_text)
                    return None
                data = await resp.json()
                if "errors" in data and "data" not in data:
                    log.warning("GraphQL error: %s", data["errors"])
                    return None
                return data.get("data")
        except aiohttp.ClientError as exc:
            log.warning("GraphQL HTTP error (attempt %d): %s", attempt, exc)
            await asyncio.sleep(2 * (attempt + 1))
    return None


# ─── Initial enrichment query ───────────────────────────────────────
_ENRICHMENT_QUERY = """
query($owner: String!, $name: String!, $issue: Int!, $tl_page_size: Int!) {
  repository(owner: $owner, name: $name) {
    createdAt
    stargazerCount
    owner { __typename }
    mentionableUsers(first: 1) { totalCount }
    pullRequests(first: 50, states: MERGED, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes { mergedAt }
      pageInfo { hasNextPage endCursor }
    }
    issue(number: $issue) {
      title
      body
      url
      state
      updatedAt
      assignees(first: 1) { totalCount }
      labels(first: 10) { nodes { name } }
      # NOTE: Only the last 50 comments are fetched. Backward pagination is
      # intentionally omitted to limit API call volume. Older comments on
      # high-traffic issues may occasionally miss escrow signals.
      comments(last: 50) {
        nodes { body createdAt }
        pageInfo { hasPreviousPage startCursor }
      }
      timelineItems(first: $tl_page_size, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT, ASSIGNED_EVENT, UNASSIGNED_EVENT]) {
        nodes {
          __typename
          ... on CrossReferencedEvent { createdAt willCloseTarget source { ... on PullRequest { state isDraft createdAt updatedAt } } }
          ... on ConnectedEvent { createdAt source { ... on PullRequest { state isDraft createdAt updatedAt } } }
          ... on AssignedEvent { createdAt }
          ... on UnassignedEvent { createdAt }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

# ─── PR pagination query ────────────────────────────────────────────
_PR_PAGE_QUERY = """
query($owner: String!, $name: String!, $after: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, states: MERGED, orderBy: {field: CREATED_AT, direction: DESC}, after: $after) {
      nodes { mergedAt }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# ─── Timeline pagination query ───────────────────────────────────────────
_TIMELINE_PAGE_QUERY = """
query($owner: String!, $name: String!, $issue: Int!, $after: String!, $tl_page_size: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $issue) {
      timelineItems(first: $tl_page_size, after: $after,
        itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT, ASSIGNED_EVENT, UNASSIGNED_EVENT]) {
        nodes {
          __typename
          ... on CrossReferencedEvent {
            createdAt willCloseTarget
            source { ... on PullRequest { state isDraft createdAt updatedAt } }
          }
          ... on ConnectedEvent {
            createdAt
            source { ... on PullRequest { state isDraft createdAt updatedAt } }
          }
          ... on AssignedEvent { createdAt }
          ... on UnassignedEvent { createdAt }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


async def run_graphql_audit(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    pr_cap: int = 200,
    tl_max_pages: int = 5,
    tl_page_size: int = 25,
) -> dict | None:
    """Fetch detailed issue + repo health data via GraphQL.

    Paginates pull requests until:
      - All PRs within the 45-day window have been consumed, OR
      - The configurable *pr_cap* (default 200) is reached.

    Returns the full ``data`` dict or ``None`` on failure.
    """
    variables = {"owner": owner, "name": repo, "issue": issue_number, "tl_page_size": tl_page_size}
    data = await fetch_graphql(session, bucket, token, _ENRICHMENT_QUERY, variables)

    if not data or not data.get("repository"):
        return data

    # ── PR pagination (Section 2.1) ──
    repo_data = data["repository"]
    pr_info = repo_data.get("pullRequests", {})
    page_info = pr_info.get("pageInfo", {})
    all_prs = list(pr_info.get("nodes", []))

    # Cutoff: PRs older than 45 days are outside the activity window.
    forty_five_ago_dt = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)
    )

    while (
        page_info.get("hasNextPage")
        and len(all_prs) < pr_cap
    ):
        # Early-stop: PRs are ordered by MERGED_AT DESC, so once the
        # last PR on a page is older than the 45-day window, all
        # subsequent pages will also be older — safe to stop.
        last_pr = all_prs[-1] if all_prs else None
        if last_pr:
            merged_at_raw = last_pr.get("mergedAt", "")
            if merged_at_raw:
                try:
                    merged_dt = datetime.datetime.strptime(
                        merged_at_raw, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=datetime.timezone.utc)
                    if merged_dt < forty_five_ago_dt:
                        break
                except ValueError:
                    pass  # Malformed timestamp — continue paginating.

        cursor = page_info.get("endCursor")
        if not cursor:
            break

        page_data = await fetch_graphql(
            session, bucket, token, _PR_PAGE_QUERY,
            {"owner": owner, "name": repo, "after": cursor},
        )
        if not page_data or not page_data.get("repository"):
            break

        next_prs = page_data["repository"].get("pullRequests", {})
        new_nodes = next_prs.get("nodes", [])
        if not new_nodes:
            break

        all_prs.extend(new_nodes)
        page_info = next_prs.get("pageInfo", {})

    # Replace the truncated PR list with the full paginated set.
    repo_data["pullRequests"]["nodes"] = all_prs

    # ── Timeline pagination (Section 2.2) ──
    issue_data = repo_data.get("issue")
    if issue_data:
        tl_info = issue_data.get("timelineItems", {}).get("pageInfo", {})
        all_tl_nodes = list(issue_data.get("timelineItems", {}).get("nodes", []))
        tl_pages = 0

        while tl_info.get("hasNextPage") and tl_pages < tl_max_pages:
            tl_cursor = tl_info.get("endCursor")
            if not tl_cursor:
                break

            tl_data = await fetch_graphql(
                session, bucket, token, _TIMELINE_PAGE_QUERY,
                {"owner": owner, "name": repo, "issue": issue_number, "after": tl_cursor, "tl_page_size": tl_page_size},
            )
            if not tl_data or not tl_data.get("repository"):
                break

            tl_issue = tl_data["repository"].get("issue", {})
            tl_items = tl_issue.get("timelineItems", {})
            new_tl_nodes = tl_items.get("nodes", [])
            if not new_tl_nodes:
                break

            all_tl_nodes.extend(new_tl_nodes)
            tl_info = tl_items.get("pageInfo", {})
            tl_pages += 1

        # Replace the truncated timeline with the full paginated set.
        if issue_data.get("timelineItems"):
            issue_data["timelineItems"]["nodes"] = all_tl_nodes

    return data
