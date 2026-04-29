"""
GitHub Bounty Scraper — Async pipeline that discovers and scores funded
crypto bounties on GitHub Issues using GraphQL enrichment and SQLite caching.
"""

import os
import re
import datetime
import json
import time
import asyncio
import aiohttp
import aiosqlite
import subprocess
import sys

# ─── Encoding safety ────────────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ─── Top-level constants ────────────────────────────────────────────
DB_FILE = "bounty_stats.db"
DEAD_REPOS_FILE = "dead_repos.json"
GRAPHQL_URL = "https://api.github.com/graphql"
SEMAPHORE_LIMIT = 15
TOKEN_BUCKET_CAPACITY = 500
TOKEN_BUCKET_FILL_RATE = 10.0
MIN_BOUNTY_THRESHOLD = 10.0
NEW_REPO_GRACE_DAYS = 90
OUTPUT_MD_FILE = "output.md"
SIGNALS_CONFIG_FILE = "signals_config.json"

CRYPTO_KEYWORDS = [
    "USDC", "ETH", "SOL", "OP", "ARB", "MATIC", "DAI", "WETH", "STRK", "ROXN",
]

# ─── Load externalised signal config ────────────────────────────────
def load_signals_config(path: str = SIGNALS_CONFIG_FILE) -> dict:
    """Load signal lists from an external JSON file.

    Falls back to empty lists if the file is missing or malformed so the
    scraper can still run (with reduced filtering accuracy).
    """
    defaults = {
        "positive_escrow": [],
        "negative_filters": [],
        "stale_signals": [],
        "active_signals": [],
        "kill_labels": [],
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for key in defaults:
            if key in data and isinstance(data[key], list):
                defaults[key] = data[key]
        return defaults
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(f"[!] Could not load {path}: {exc}  — using empty defaults.")
        return defaults


SIGNALS = load_signals_config()

# ─── GitHub token resolution ────────────────────────────────────────
def get_github_token() -> str | None:
    """Return a GitHub PAT from the CLI tool or environment variables."""
    try:
        res = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        token = res.stdout.strip()
        if token:
            return token
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GITHUB_PAT")
        or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    )


GITHUB_TOKEN = get_github_token()
if not GITHUB_TOKEN:
    print("Error: No valid token available in GitHub CLI or environment variables.")
    sys.exit(1)


# ─── Token-bucket rate limiter ──────────────────────────────────────
class TokenBucket:
    """Async token-bucket rate limiter for API calls."""

    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
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


# ─── Database initialisation ────────────────────────────────────────
async def init_db(conn: aiosqlite.Connection) -> None:
    """Create or migrate the SQLite schema for caching."""
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS repo_stats (
            repo_name TEXT PRIMARY KEY,
            last_checked_at REAL,
            last_merged_pr_at REAL,
            merges_last_45d INTEGER,
            escrows_seen INTEGER,
            rugs_seen INTEGER
        )
    """)
    try:
        await conn.execute(
            "ALTER TABLE repo_stats ADD COLUMN snipes_detected INTEGER DEFAULT 0;"
        )
    except aiosqlite.OperationalError:
        pass

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS issue_stats (
            issue_url TEXT PRIMARY KEY,
            checked_at REAL,
            scraped_amount REAL
        )
    """)
    await conn.commit()


# ─── GraphQL helpers ─────────────────────────────────────────────────
async def fetch_graphql(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    query: str,
    variables: dict | None = None,
    retries: int = 5,
) -> dict | None:
    """Execute a GraphQL query against the GitHub API with rate-limit retries."""
    await bucket.consume(1)

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
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
                    print(f"  [!] GraphQL Rate limit hit! Sleeping {wait_t}s...")
                    await asyncio.sleep(wait_t)
                    continue
                if not resp.ok:
                    resp_text = await resp.text()
                    print(f"  [!] HTTP {resp.status}: {resp_text}")
                    return None
                data = await resp.json()
                if "errors" in data and "data" not in data:
                    print(f"  [!] GraphQL Error: {data['errors']}")
                    return None
                return data.get("data")
        except aiohttp.ClientError as exc:
            print(f"  [!] HTTP client error (attempt {attempt}): {exc}")
            await asyncio.sleep(2 * (attempt + 1))
    return None


# ─── REST search (discovery phase) ──────────────────────────────────
async def async_fetch_rest_search(
    session: aiohttp.ClientSession, query: str, page: int, retries: int = 3
) -> list[dict]:
    """Fetch one page of GitHub issue search results via REST API."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {
        "q": query,
        "sort": "updated",
        "order": "desc",
        "per_page": 100,
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
                    print(f"  [!] Search Rate Limit, sleeping {wait_t}s...")
                    await asyncio.sleep(wait_t)
                    continue
                if not resp.ok:
                    return []
                data = await resp.json()
                return data.get("items", [])
        except aiohttp.ClientError as exc:
            print(f"  [!] Search HTTP error (attempt {attempt}): {exc}")
            await asyncio.sleep(2 * (attempt + 1))
    return []


async def async_two_phase_search() -> list[dict]:
    """Run multiple GitHub search queries and deduplicate by URL."""
    print("Executing Async Search with Custom Dorks...")
    queries = [
        'is:open is:issue label:bounty,USDC,reward,funded,"help wanted",sponsored,grant',
        'is:open is:issue "bountydotnew" OR "collaborators.build" OR "GitGig-Base" OR "roxonn" OR "onlydust.xyz" OR "workprotocol" OR "x402" OR "hats.finance"',
        'is:open is:issue "escrow locked" OR "smart contract funded" OR "paid via smart contract" OR "usdc has been funded"',
        'is:open is:issue "paid on merge" OR "reward on merge" OR "bounty on merge"',
        'is:open is:issue "post your wallet" OR "send usdc to" OR "wallet address on merge" OR "ens address"',
        'is:open is:issue "USDC" "$1000" OR "$2000" OR "$5000" stars:>100',
        'is:open is:issue "OP reward" OR "ARB bounty" OR "STRK funded" OR "SOL bounty" stars:>50',
    ]

    tasks = []
    async with aiohttp.ClientSession() as session:
        for q in queries:
            for page in range(1, 3):
                tasks.append(async_fetch_rest_search(session, q, page))
        results = await asyncio.gather(*tasks)

    unique_issues: dict[str, dict] = {}
    for batch in results:
        for item in batch:
            url = item.get("html_url")
            if url and url not in unique_issues:
                unique_issues[url] = item
    return list(unique_issues.values())


# ─── GraphQL enrichment query ───────────────────────────────────────
async def run_graphql_audit(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict | None:
    """Fetch detailed issue + repo health data via GraphQL."""
    query = """
    query($owner: String!, $name: String!, $issue: Int!) {
      repository(owner: $owner, name: $name) {
        createdAt
        pullRequests(last: 20, states: MERGED, orderBy: {field: UPDATED_AT, direction: ASC}) {
          nodes { mergedAt }
        }
        issue(number: $issue) {
          title
          body
          url
          assignees(first: 1) { totalCount }
          labels(first: 10) { nodes { name } }
          comments(last: 50) { nodes { body createdAt } }
          timelineItems(first: 25, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT, ASSIGNED_EVENT]) {
            nodes {
              ... on CrossReferencedEvent { createdAt willCloseTarget source { ... on PullRequest { state isDraft createdAt updatedAt } } }
              ... on ConnectedEvent { createdAt source { ... on PullRequest { state isDraft createdAt updatedAt } } }
              ... on AssignedEvent { createdAt }
            }
          }
        }
      }
    }
    """
    variables = {"owner": owner, "name": repo, "issue": issue_number}
    return await fetch_graphql(session, bucket, query, variables=variables)


# ─── Signal / filter functions (all read from SIGNALS config) ────────
def extract_bounty_amount(text: str) -> tuple[float, str]:
    """Extract the maximum bounty amount from free-form text.

    Returns (numeric_value, display_string).  When no numeric value is found
    but a crypto keyword exists, returns (-1.0, "Unknown / Custom Tokens").
    """
    pattern = (
        r'(\$[0-9,]+(?:[.,]\d{2})?'
        r'|\d+[0-9,]*(?:\.\d+)?\s*'
        r'(?:USDC|USDT|ETH|SOL|OP|ARB|MATIC|ROXN|XDC|DAI|WETH|STRK))'
    )
    matches = re.findall(pattern, text, re.IGNORECASE)

    best_val = 0.0
    best_display = "Unknown Amount"

    for match in matches:
        num_str = re.sub(r'[^\d.]', '', match.replace(',', ''))
        if num_str:
            try:
                val = float(num_str)
                if val > best_val:
                    best_val = val
                    best_display = match.strip()
            except ValueError:
                continue

    # Fallback: crypto keyword detected but no numeric amount (#6)
    if best_val == 0.0:
        text_upper = text.upper()
        if any(kw in text_upper for kw in CRYPTO_KEYWORDS):
            return -1.0, "Unknown / Custom Tokens"

    return best_val, best_display


def check_negative_filters(body: str, comments: list[dict]) -> bool:
    """Return True if negative / spam signals are present."""
    neg_signals = SIGNALS["negative_filters"]
    body_lower = body.lower()
    if any(s in body_lower for s in neg_signals):
        return True
    for c in comments:
        if any(s in c.get("body", "").lower() for s in neg_signals):
            return True
    return False


def check_positive_escrow(body: str, comments: list[dict]) -> bool:
    """Return True if at least one positive escrow signal is present."""
    pos_signals = SIGNALS["positive_escrow"]
    if any(s in body.lower() for s in pos_signals):
        return True
    for c in comments:
        if any(s in c.get("body", "").lower() for s in pos_signals):
            return True
    return False


def is_assignment_stale(comments: list[dict], timeline_nodes: list[dict]) -> bool:
    """Return True if the most recent assignment looks stale.

    An assignment is stale when a stale-signal comment was posted *after*
    the last AssignedEvent timestamp.
    """
    stale_signals = SIGNALS["stale_signals"]

    # Find the latest AssignedEvent timestamp
    last_assigned_ts: datetime.datetime | None = None
    for node in timeline_nodes:
        if "source" not in node and "willCloseTarget" not in node:
            # Likely an AssignedEvent
            raw = node.get("createdAt")
            if raw:
                try:
                    dt = datetime.datetime.strptime(
                        raw, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=datetime.timezone.utc)
                    if last_assigned_ts is None or dt > last_assigned_ts:
                        last_assigned_ts = dt
                except ValueError:
                    continue

    if last_assigned_ts is None:
        return False

    # Check if any stale signal comment is newer than the assignment
    for c in comments:
        c_body = c.get("body", "").lower()
        created_at = c.get("createdAt")
        if not created_at:
            continue
        if any(s in c_body for s in stale_signals):
            try:
                dt = datetime.datetime.strptime(
                    created_at, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
                if dt > last_assigned_ts:
                    return True
            except ValueError:
                continue
    return False


def check_ghost_squatter(
    issue: dict, comments: list[dict], timeline_nodes: list[dict]
) -> bool:
    """Return True if the issue has a FRESH (non-stale) assignee.

    Fixed: original code returned True for *any* assignee, blocking all
    assigned issues.  Now we allow stale/re-opened assignments through.
    """
    if issue.get("assignees", {}).get("totalCount", 0) > 0:
        if is_assignment_stale(comments, timeline_nodes):
            return False  # Stale assignment — let it through
        return True  # Fresh assignment — skip
    return False


def check_kill_labels(labels_nodes: list[dict]) -> bool:
    """Return True if any label matches the kill-switch list."""
    kill_switches = SIGNALS["kill_labels"]
    for label in labels_nodes:
        l_name = label.get("name", "").lower()
        if any(k in l_name for k in kill_switches):
            return True
    return False


def evaluate_lane_status(comments: list[dict]) -> bool:
    """Return True if an active claim appears more recent than any stale signal."""
    stale_signals = SIGNALS["stale_signals"]
    active_signals = SIGNALS["active_signals"]

    max_stale_ts: datetime.datetime | None = None
    max_active_ts: datetime.datetime | None = None

    for c in comments:
        c_body = c.get("body", "").lower()
        created_at = c.get("createdAt")
        if not created_at:
            continue
        try:
            dt = datetime.datetime.strptime(
                created_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue

        if any(s in c_body for s in stale_signals):
            if max_stale_ts is None or dt > max_stale_ts:
                max_stale_ts = dt
        if any(s in c_body for s in active_signals):
            if max_active_ts is None or dt > max_active_ts:
                max_active_ts = dt

    if max_active_ts is not None and (
        max_stale_ts is None or max_active_ts > max_stale_ts
    ):
        return True
    return False


# ─── Issue processing (enrichment phase) ────────────────────────────
async def process_issue(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    issue_item: dict,
    db_conn: aiosqlite.Connection,
    sem: asyncio.Semaphore,
) -> dict | None:
    """Enrich a single issue via GraphQL and apply all pipeline filters.

    Returns a lead dict on success, or None if filtered out.
    """
    url = issue_item.get("html_url", "")
    if not url:
        return None

    parts = url.replace("https://github.com/", "").split("/")
    if len(parts) < 4:
        return None

    owner, repo = parts[0], parts[1]
    repo_name = f"{owner}/{repo}"

    # Skip known aggregator repos
    if any(
        b in repo_name.lower()
        for b in ["algora", "gitcoin", "issuehunt", "bountysource"]
    ):
        return None

    issue_number = int(parts[3])

    # ── Adaptive TTL cache check ──
    async with db_conn.execute(
        "SELECT merges_last_45d, last_checked_at FROM repo_stats WHERE repo_name=?",
        (repo_name,),
    ) as cursor:
        row = await cursor.fetchone()

    current_time = time.time()

    if row:
        merges_last_45d_cache = row[0]
        last_checked_at = row[1]
        time_since_check = current_time - last_checked_at

        if merges_last_45d_cache == 0 and time_since_check < 259200:
            return None
        elif merges_last_45d_cache in (1, 2) and time_since_check < 43200:
            return None
        elif merges_last_45d_cache >= 3 and time_since_check < 7200:
            return None

    # ── GraphQL enrichment ──
    async with sem:
        data = await run_graphql_audit(session, bucket, owner, repo, issue_number)

    if not data or not data.get("repository") or not data["repository"].get("issue"):
        return None

    repository = data["repository"]
    issue = repository["issue"]

    # ── Repo health: merge count ──
    prs = repository.get("pullRequests", {}).get("nodes", [])
    forty_five_days_ago = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(days=45)

    merges_last_45 = 0
    last_merged_at_ts = 0.0

    for pr in prs:
        merged_raw = pr.get("mergedAt")
        if merged_raw:
            try:
                dt = datetime.datetime.strptime(
                    merged_raw, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
                ts = dt.timestamp()
                if ts > last_merged_at_ts:
                    last_merged_at_ts = ts
                if dt >= forty_five_days_ago:
                    merges_last_45 += 1
            except ValueError:
                pass

    await db_conn.execute(
        """
        INSERT INTO repo_stats
            (repo_name, last_checked_at, last_merged_pr_at, merges_last_45d, escrows_seen, rugs_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_name) DO UPDATE SET
            last_checked_at=excluded.last_checked_at,
            last_merged_pr_at=excluded.last_merged_pr_at,
            merges_last_45d=excluded.merges_last_45d
        """,
        (repo_name, current_time, last_merged_at_ts, merges_last_45, 0, 0),
    )
    await db_conn.commit()

    # ── Dead-repo kill condition (FIX #3: new-repo grace) ──
    if merges_last_45 == 0:
        repo_created_raw = repository.get("createdAt")
        is_new_repo = False
        if repo_created_raw:
            try:
                created_dt = datetime.datetime.strptime(
                    repo_created_raw, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
                age_days = (
                    datetime.datetime.now(datetime.timezone.utc) - created_dt
                ).days
                is_new_repo = age_days < NEW_REPO_GRACE_DAYS
            except ValueError:
                pass
        if not is_new_repo:
            return None

    labels = issue.get("labels", {}).get("nodes", [])
    comments = issue.get("comments", {}).get("nodes", [])
    body = issue.get("body") or ""
    title = issue.get("title") or ""
    timeline_nodes = issue.get("timelineItems", {}).get("nodes", [])

    if check_kill_labels(labels):
        return None

    if check_negative_filters(body, comments):
        await db_conn.execute(
            "UPDATE repo_stats SET rugs_seen = rugs_seen + 1 WHERE repo_name=?",
            (repo_name,),
        )
        await db_conn.commit()
        return None

    if evaluate_lane_status(comments):
        return None

    if not check_positive_escrow(body, comments):
        return None

    await db_conn.execute(
        "UPDATE repo_stats SET escrows_seen = escrows_seen + 1 WHERE repo_name=?",
        (repo_name,),
    )
    await db_conn.commit()

    # ── Snipe detection ──
    for node in timeline_nodes:
        if "source" in node:
            source = node["source"]
            if (
                source
                and source.get("state") == "OPEN"
                and source.get("isDraft") is False
                and node.get("willCloseTarget") is True
            ):
                await db_conn.execute(
                    "UPDATE repo_stats SET snipes_detected = snipes_detected + 1 WHERE repo_name=?",
                    (repo_name,),
                )
                await db_conn.commit()
                return None

    # ── Ghost squatter check (FIX #2: stale-aware) ──
    if check_ghost_squatter(issue, comments, timeline_nodes):
        return None

    # ── Amount extraction (FIX #1: max match, FIX #6: custom tokens) ──
    concat_text = f"{title} {body}"
    for c in comments:
        concat_text += " " + c.get("body", "")

    num_val, display = extract_bounty_amount(concat_text)

    # Revised threshold logic (#6):
    #   num_val < 0   → Unknown / Custom Tokens (include as low-priority)
    #   0 < num_val < MIN_BOUNTY_THRESHOLD → too small, skip
    #   num_val >= MIN_BOUNTY_THRESHOLD    → verified lead
    if 0 < num_val < MIN_BOUNTY_THRESHOLD:
        return None
    if num_val == 0.0:
        return None

    await db_conn.execute(
        """
        INSERT OR REPLACE INTO issue_stats (issue_url, checked_at, scraped_amount)
        VALUES (?, ?, ?)
        """,
        (url, current_time, num_val),
    )
    await db_conn.commit()

    label_names = [la["name"] for la in labels]

    return {
        "AmountNum": num_val,
        "Amount": display,
        "Repo": repo_name,
        "Title": title,
        "Labels": f"[{', '.join(label_names)}]" if label_names else "[]",
        "Link": url,
    }


# ─── Markdown output writer (#7) ────────────────────────────────────
def write_output_md(
    verified: list[dict], unknown: list[dict], elapsed: float
) -> None:
    """Write a structured markdown report to OUTPUT_MD_FILE."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# GitHub Bounty Scraper — Results\n",
        f"**Generated:** {now_str}  ",
        f"**Verified leads:** {len(verified)}  ",
        f"**Unknown/Custom Token leads:** {len(unknown)}  ",
        f"**Pipeline time:** {elapsed:.2f}s\n",
        "---\n",
    ]

    if verified:
        lines.append("## Verified Bounty Leads\n")
        lines.append("| Amount | Repo | Title | Labels | Link |")
        lines.append("|--------|------|-------|--------|------|")
        for lead in verified:
            safe_title = lead["Title"].replace("|", "\\|")[:80]
            lines.append(
                f"| {lead['Amount']} | {lead['Repo']} | {safe_title} "
                f"| {lead['Labels']} | [link]({lead['Link']}) |"
            )
        lines.append("")

    if unknown:
        lines.append("## Unknown / Custom Token Leads\n")
        lines.append("| Amount | Repo | Title | Labels | Link |")
        lines.append("|--------|------|-------|--------|------|")
        for lead in unknown:
            safe_title = lead["Title"].replace("|", "\\|")[:80]
            lines.append(
                f"| {lead['Amount']} | {lead['Repo']} | {safe_title} "
                f"| {lead['Labels']} | [link]({lead['Link']}) |"
            )
        lines.append("")

    if not verified and not unknown:
        lines.append("_No leads survived pipeline filtering._\n")

    lines.append("---\n")
    lines.append(
        "> **Disclaimer:** This tool is for discovery only. "
        "Always verify bounty legitimacy before investing time.\n"
    )

    with open(OUTPUT_MD_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"Markdown report written to {OUTPUT_MD_FILE}")


# ─── Main entry point ───────────────────────────────────────────────
async def main() -> None:
    """Run the full two-phase bounty discovery pipeline."""
    print("Initiating Asynchronous GraphQL Web3 Pipeline...")
    start_time = time.time()

    if os.path.exists(DEAD_REPOS_FILE):
        try:
            os.remove(DEAD_REPOS_FILE)
        except OSError:
            pass

    async with aiosqlite.connect(DB_FILE) as db_conn:
        await init_db(db_conn)
        sem = asyncio.Semaphore(SEMAPHORE_LIMIT)

        issues = await async_two_phase_search()
        if not issues:
            print("No issues discovered across either footprint pipeline phase.")
            return

        print(
            f"Discovered {len(issues)} raw potentials. "
            "Initiating high-speed Concurrent GraphQL Enrichment..."
        )

        bucket = TokenBucket(TOKEN_BUCKET_CAPACITY, TOKEN_BUCKET_FILL_RATE)

        async with aiohttp.ClientSession() as session:
            tasks = [
                process_issue(session, bucket, issue, db_conn, sem)
                for issue in issues
            ]
            results = await asyncio.gather(*tasks)

        all_leads = [r for r in results if r]

    # Sort: verified numeric leads first (desc), then unknown at end
    verified_leads = sorted(
        [l for l in all_leads if l["AmountNum"] > 0],
        key=lambda x: x["AmountNum"],
        reverse=True,
    )
    unknown_leads = [l for l in all_leads if l["AmountNum"] < 0]

    elapsed = time.time() - start_time

    # ── Console output ──
    print("\n" + "=" * 60)
    print("VERIFIED BOUNTY LEADS (Sorted Highest to Lowest)")
    print("=" * 60)

    if not verified_leads:
        print("No robust verified leads survived the pipeline filtering.")
    else:
        for lead in verified_leads:
            print(f"Amount  : {lead['Amount']}")
            print(f"Repo    : {lead['Repo']}")
            safe_title = str(lead["Title"]).encode("ascii", "ignore").decode("ascii")
            print(f"Title   : {safe_title} {lead['Labels']}")
            print(f"Link    : {lead['Link']}")
            print("-" * 60)

    if unknown_leads:
        print("\n" + "=" * 60)
        print("UNKNOWN / CUSTOM TOKEN LEADS")
        print("=" * 60)
        for lead in unknown_leads:
            print(f"Amount  : {lead['Amount']}")
            print(f"Repo    : {lead['Repo']}")
            safe_title = str(lead["Title"]).encode("ascii", "ignore").decode("ascii")
            print(f"Title   : {safe_title} {lead['Labels']}")
            print(f"Link    : {lead['Link']}")
            print("-" * 60)

    print(f"Pipeline executed in {elapsed:.2f} seconds.")

    # ── Markdown report (#7) ──
    write_output_md(verified_leads, unknown_leads, elapsed)


if __name__ == "__main__":
    asyncio.run(main())
