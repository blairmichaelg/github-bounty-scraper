"""
Core pipeline — orchestrates discovery → enrichment → scoring → output.

Fully async, with error isolation (return_exceptions=True), retry
wrapper, progress reporting, and batch DB commits.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import time
from typing import Any, TypedDict, cast



import aiohttp
import aiosqlite

from .bounty import extract_bounty_amount, detect_snipe
from .config import ScraperConfig, load_signals
from .db import (
    BatchCommitter,
    init_db,
    mark_issue_checked,
    repo_cache_check,
    should_skip_issue,
    upsert_issue_stats,
    upsert_repo_stats,
    get_repo_reputation,
)
from .discovery import discover_issues
from .graphql import TokenBucket, run_graphql_audit, fetch_graphql
from .log import get_logger
from .output import write_output
from .scoring import compute_score
from .signals import (
    apply_hard_disqualifiers,
    compute_soft_signals,
)
from .price_cache import refresh_prices

log = get_logger()
 
# Hard safety cap — prevents runaway API usage on large result sets.
MAX_ISSUES_PER_RUN = 1000
 
class LeadResult(TypedDict):
    """Enriched lead data for reporting."""
    AmountNum: float
    Amount: str
    Currency: str
    Score: float
    Repo: str
    Title: str
    Labels: str
    Link: str
    PrevScore: float | None


def _append_raw(path: str, line: str) -> None:
    """Sync helper to append a line to the raw candidate log."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


# ─── Per-issue processing ───────────────────────────────────────────
async def process_issue(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    issue_item: dict,
    db_conn: aiosqlite.Connection,
    sem: asyncio.Semaphore,
    config: ScraperConfig,
    signals: dict[str, list[str] | list[dict[str, Any]]],
    committer: BatchCommitter,
    seen_aggregators: set[str],
) -> LeadResult | None:
    """Execute the full 15-stage enrichment and scoring pipeline for a single issue.

    Pipeline Stages:
        1.  Token bucket acquisition (rate limit safety).
        2.  Repo metadata cache check (SQLite).
        3.  Skip check (already processed in current mode).
        4.  GraphQL enrichment (Labels, Comments, Repository stats).
        5.  Dead-repo kill (0 merges in 45 days, new-repo grace period).
        6.  Hard disqualifiers (CLOSED status, kill labels, negative filters).
        7.  Lane blocking check (active claim detection).
        8.  Bounty amount extraction (USD/Crypto regex heuristics).
        9.  Mode-specific thresholding (Strict vs. Opportunistic).
        10. Raw candidate logging (exploration_raw.jsonl).
        11. Snipe detection (comment-based claim detection).
        12. Ghost squatter detection (fresh assignee check).
        13. Composite scoring (Amount, Recency, Activity, Escrow).
        14. Database upsert (Issue stats + Repo stats).
        15. Batch commit tick.

    Args:
        session: Active aiohttp ClientSession.
        bucket: Rate-limiting TokenBucket.
        issue_item: Raw issue item from the discovery phase.
        db_conn: aiosqlite connection.
        sem: Concurrency semaphore.
        config: ScraperConfig instance.
        signals: Signals dictionary.
        committer: BatchCommitter instance.

    Returns:
        LeadResult dict if the issue is a verified lead, else None.
    """
    url = issue_item.get("html_url", "")
    if not url:
        return None

    parts = url.replace("https://github.com/", "").split("/")
    if len(parts) < 4:
        return None

    owner, repo = parts[0], parts[1]
    repo_name = f"{owner}/{repo}"

    # Skip known aggregator repos (loaded from signals_config.json).
    aggregator_repos = cast(list[str], signals.get("aggregator_repos", []))
    if any(a in repo_name.lower() for a in aggregator_repos):
        if repo_name not in seen_aggregators:
            seen_aggregators.add(repo_name)
            log.debug("Skipping aggregator repo: %s", repo_name)
        return None

    issue_number = int(parts[3])

    # ── Cache check (repo-level adaptive TTL) ──
    if not config.no_cache:
        if await repo_cache_check(
            db_conn, repo_name,
            config.cache_ttl_dead, config.cache_ttl_low, config.cache_ttl_active,
        ):
            log.debug("Cache skip (repo TTL): %s", repo_name)
            return None

    # ── GraphQL enrichment ──
    async with sem:
        data = await run_graphql_audit(
            session, bucket, config.github_token,
            owner, repo, issue_number,
            pr_cap=config.pr_cap,
            tl_max_pages=config.tl_max_pages,
            tl_page_size=config.timeline_page_size,
        )

    if not data or not data.get("repository") or not data["repository"].get("issue"):
        log.debug("No GraphQL data for %s#%d", repo_name, issue_number)
        return None

    repository = data["repository"]
    
    repo_stars = repository.get("stargazerCount", 0)
    owner_type = repository.get("owner", {}).get("__typename", "")
    contrib_count = repository.get("mentionableUsers", {}).get("totalCount", 0)
    
    is_lead_candidate = True
    raw_reasons = []

    if repo_stars < config.min_stars:
        is_lead_candidate = False
        raw_reasons.append(f"too few stars ({repo_stars} < {config.min_stars})")
    if owner_type.upper() == "USER" and contrib_count < 2:
        is_lead_candidate = False
        raw_reasons.append("personal repo")

    if not is_lead_candidate:
        if not config.log_raw_candidates:
            log.debug("Skipping %s: %s", repo_name, " / ".join(raw_reasons))
            return None

    issue = repository["issue"]

    # ── Issue state check (Section 2.3) — drop CLOSED early ──
    issue_state = issue.get("state", "")
    lead_mode_override = None
    if issue_state.upper() == "CLOSED":
        if not config.include_closed_for_training:
            log.debug("Dropping CLOSED issue: %s", url)
            return None
        # Fall through: enrich closed issue for training data only
        # Mark it so dump_dataset knows it's a historical positive
        lead_mode_override = "closed_historical"

    issue_updated_at_raw = issue.get("updatedAt", "")

    # ── Issue-level cache check ──
    if not config.no_cache and issue_updated_at_raw:
        try:
            updated_dt = datetime.datetime.strptime(
                issue_updated_at_raw, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
            if await should_skip_issue(
                db_conn, url, updated_dt.timestamp(), config.cache_ttl_active
            ):
                log.debug("Cache skip (issue TTL): %s", url)
                return None
        except ValueError:
            pass

    # ── Repo health: merge count (with paginated PRs) ──
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

    # ── Dead-repo kill condition (with new-repo grace) ──
    is_dead_repo_flag = False
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
                is_new_repo = age_days < config.new_repo_grace_days
            except ValueError:
                pass
        if not is_new_repo:
            is_dead_repo_flag = True
            if config.mode == "opportunistic" and config.opportunistic_allow_dead_repos:
                pass
            else:
                log.debug("Dead repo (0 merges, not new): %s", repo_name)
                if not config.dry_run:
                    await mark_issue_checked(db_conn, url, time.time())
                    await committer.tick()
                return None

    labels = issue.get("labels", {}).get("nodes", [])
    comments = issue.get("comments", {}).get("nodes", [])
    body = issue.get("body") or ""
    title = issue.get("title") or ""
    timeline_nodes = issue.get("timelineItems", {}).get("nodes", [])

    # ── Accumulate repo_stats increments locally (single upsert at end) ──
    escrow_inc = 0
    rug_inc = 0
    snipe_inc = 0
    prev_score_for_output: float | None = None
    vibe_score_val: int | None = None

    # ── Hard disqualifiers ──
    disqualified, reason = apply_hard_disqualifiers(
        issue_state=issue_state,
        labels_nodes=labels,
        body=body,
        comments=comments,
        signals=signals,
    )
    if disqualified:
        log.debug("Hard disqualified (%s): %s", reason, url)
        if "negative" in reason or "kill label" in reason:
            rug_inc = 1
        if not config.dry_run:
            await upsert_repo_stats(
                db_conn, repo_name,
                last_merged_pr_at=last_merged_at_ts,
                merges_last_45d=merges_last_45,
                rug_increment=rug_inc,
            )
            await mark_issue_checked(db_conn, url, time.time())
            await committer.tick()
        return None

    # ── Lane status (soft but blocking for now) ──
    soft = compute_soft_signals(
        body=body,
        comments=comments,
        labels_nodes=labels,
        timeline_nodes=timeline_nodes,
        issue=issue,
        signals=signals,
        allow_assigned_if_stale=config.allow_assigned_if_stale,
        active_signal_max_age_days=config.active_signal_max_age_days,
    )

    if soft.lane_blocked:
        log.debug("Lane blocked: %s", url)
        if not config.dry_run:
            await mark_issue_checked(db_conn, url, time.time())
            await committer.tick()
        return None

    # ── Bounty amount extraction ──
    concat_text = f"{title} {body}"
    for c in comments:
        concat_text += " " + c.get("body", "")

    bounty = extract_bounty_amount(concat_text, max_sane=config.max_sane_amount, proximity_window=config.proximity_window, config=config)

    num_val = bounty.numeric_amount
    display = bounty.raw_display
    currency = bounty.currency_symbol

    title_lower = title.lower()
    labels_lower = [l.get("name", "").lower() for l in labels]
    has_bounty_title = any(w in title_lower for w in ["bounty", "reward", "paid", "pays", "bounties"])
    has_bounty_label = any("bounty" in l or "reward" in l for l in labels_lower)
    has_cue = has_bounty_title or has_bounty_label

    escrow_verified = soft.has_positive_escrow

    if is_lead_candidate:
        if config.mode == "opportunistic":
            if not escrow_verified and not (config.opportunistic_allow_no_escrow and has_cue):
                is_lead_candidate = False
                raw_reasons.append("no_positive_escrow_and_no_cue")
            if is_lead_candidate:
                if num_val >= config.opportunistic_min_amount:
                    pass
                elif num_val == 0.0 and has_cue:
                    num_val = -1.0
                else:
                    is_lead_candidate = False
                    raw_reasons.append("below_opportunistic_amount_threshold")
        else:
            if not escrow_verified:
                is_lead_candidate = False
                raw_reasons.append("no_positive_escrow")
            elif 0 < num_val < config.min_bounty_amount:
                is_lead_candidate = False
                raw_reasons.append("below_amount_threshold")
            elif num_val == 0.0:
                is_lead_candidate = False
                raw_reasons.append("no_parsable_amount")

    if not is_lead_candidate:
        if not escrow_verified and "no_positive_escrow" not in raw_reasons and "no_positive_escrow_and_no_cue" not in raw_reasons:
            raw_reasons.append("no_positive_escrow")
        if num_val == 0.0 and "no_parsable_amount" not in raw_reasons:
            raw_reasons.append("no_parsable_amount")

    # ── Raw Candidate Logging ──
    if config.log_raw_candidates and not detect_snipe(timeline_nodes) and not soft.ghost_squatter:
        cand = {
            "url": url,
            "repo_name": repo_name,
            "issue_number": issue_number,
            "title": title,
            "numeric_amount": num_val if num_val > 0 else -1,
            "raw_display_amount": display,
            "currency_symbol": currency,
            "merges_last_45d": merges_last_45,
            "stars": repo_stars,
            "is_org_owner": owner_type.upper() == "ORGANIZATION",
            "contributors_count": contrib_count,
            "is_fork": repository.get("isFork", False),
            "is_archived": repository.get("isArchived", False),
            "labels": [l.get("name") for l in labels],
            "body_snippet": body[:300].replace("\n", " ") if body else "",
            "reasons": raw_reasons if not is_lead_candidate else ["LEAD_CANDIDATE"]
        }
        await asyncio.get_running_loop().run_in_executor(
            None, _append_raw, "exploration_raw.jsonl", json.dumps(cand) + "\n"
        )

    # ── Lead Checks ──
    if not is_lead_candidate and not (lead_mode_override == "closed_historical"):
        log.debug("Skipping non-lead: %s", url)
        return None

    escrow_inc = 1 if escrow_verified else 0

    # ── Snipe detection ──
    if detect_snipe(timeline_nodes):
        log.debug("Snipe detected: %s", url)
        snipe_inc = 1
        if not config.dry_run:
            await upsert_repo_stats(
                db_conn, repo_name,
                last_merged_pr_at=last_merged_at_ts,
                merges_last_45d=merges_last_45,
                escrow_increment=escrow_inc,
                snipe_increment=snipe_inc,
            )
            await committer.tick()
        return None

    # ── Ghost squatter ──
    if soft.ghost_squatter:
        log.debug("Ghost squatter (fresh assignee): %s", url)
        if not config.dry_run:
            await upsert_repo_stats(
                db_conn, repo_name,
                last_merged_pr_at=last_merged_at_ts,
                merges_last_45d=merges_last_45,
                escrow_increment=escrow_inc,
            )
            await committer.tick()
        return None

    # Threshold logic:
    #   num_val < 0   → Unknown / Custom Tokens (include as low-priority)
    #   0 < num_val < min_bounty_amount → too small, skip
    #   num_val >= min_bounty_amount    → verified lead


    # ── Repo reputation ──
    if not config.dry_run:
        await upsert_repo_stats(
            db_conn, repo_name,
            last_merged_pr_at=last_merged_at_ts,
            merges_last_45d=merges_last_45,
            escrow_increment=escrow_inc,
            rug_increment=rug_inc,
            snipe_increment=snipe_inc,
            bounty_amount=num_val if num_val >= 0 else 0,
        )
        # No tick here; will tick after upsert_issue_stats.

    repo_rep = await get_repo_reputation(db_conn, repo_name)

    if not config.dry_run:
        # Fetch previous score for delta tracking in output
        async with db_conn.execute("SELECT score, vibe_score FROM issue_stats WHERE issue_url = ?", (url,)) as cursor:
            row = await cursor.fetchone()
            if row:
                prev_score_for_output = row[0]
                vibe_score_val = row[1]
            else:
                prev_score_for_output = None
                vibe_score_val = None

    # ── Scoring ──
    score = compute_score(
        numeric_amount=num_val if num_val > 0 else 0,
        issue_updated_at=issue_updated_at_raw,
        merges_last_45d=merges_last_45,
        positive_escrow_count=soft.positive_escrow_count,
        positive_escrow_weight_sum=soft.escrow_weight_sum,
        repo_reputation=repo_rep,
        vibe_score_int=vibe_score_val if not config.dry_run else None,
        has_negative_soft=soft.has_negative_soft,
        config=config,
    )

    # ── DB upsert (single repo_stats + issue_stats call) ──
    if not config.dry_run:
        # Parse updatedAt safely — malformed timestamps fall back to 0.0.
        try:
            _last_updated_ts = (
                datetime.datetime.strptime(issue_updated_at_raw, "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=datetime.timezone.utc)
                .timestamp()
            ) if issue_updated_at_raw else 0.0
        except ValueError:
            _last_updated_ts = 0.0

        await upsert_issue_stats(
            db_conn, url,
            scraped_amount=num_val,
            numeric_amount=num_val,
            raw_display_amount=display,
            currency_symbol=currency,
            score=score,
            last_updated_at=_last_updated_ts,
            title=title,
            repo_name=repo_name,
            lead_mode=lead_mode_override or config.mode,
            escrow_verified=escrow_verified,
            is_dead_repo=is_dead_repo_flag,
            has_onchain_escrow=soft.has_onchain_escrow,
            mentions_no_kyc=soft.mentions_no_kyc,
            mentions_wallet_payout=soft.mentions_wallet_payout,
            positive_escrow_count=soft.positive_escrow_count,
            escrow_weight_sum=soft.escrow_weight_sum,
            body_snippet=body_snippet,
        )
        await committer.tick()

    label_names = [la["name"] for la in labels]

    return {
        "AmountNum": num_val,
        "Amount": display,
        "Currency": currency,
        "Score": score,
        "Repo": repo_name,
        "Title": title,
        "Labels": f"[{', '.join(label_names)}]" if label_names else "[]",
        "Link": url,
        "PrevScore": prev_score_for_output if not config.dry_run else None,
    }


# ─── Retry wrapper ──────────────────────────────────────────────────
async def _process_with_retry(
    session: aiohttp.ClientSession,
    bucket: TokenBucket,
    issue_item: dict,
    db_conn: aiosqlite.Connection,
    sem: asyncio.Semaphore,
    config: ScraperConfig,
    signals: dict[str, list[str] | list[dict[str, Any]]],
    committer: BatchCommitter,
    seen_aggregators: set[str],
    max_retries: int = 2,
) -> LeadResult | None:
    """Wrap ``process_issue`` with a simple retry for transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return await process_issue(
                session, bucket, issue_item, db_conn, sem,
                config, signals, committer, seen_aggregators,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < max_retries:
                log.warning(
                    "Transient error on %s (attempt %d/%d): %s",
                    issue_item.get("html_url", "?"), attempt + 1, max_retries, exc,
                )
                await asyncio.sleep(1 * (attempt + 1))
            else:
                log.error(
                    "Failed after %d retries: %s — %s",
                    max_retries, issue_item.get("html_url", "?"), exc,
                )
                return None
        except aiosqlite.OperationalError as exc:
            log.error(
                "DB error (non-retryable) on %s: %s",
                issue_item.get("html_url", "?"), exc,
            )
            return None
    return None


# ─── Main pipeline ──────────────────────────────────────────────────
async def run_pipeline(config: ScraperConfig) -> None:
    """Run the full discovery → enrichment → scoring → output pipeline.

    Phases:
        1. Discovery: Search GitHub for potential bounty issues via REST API.
        2. Enrichment: Concurrently fetch deep metadata via GraphQL and score.
        3. Output: Generate Markdown/JSON reports and commit to DB.

    Args:
        config: Assembled ScraperConfig.

    Side Effects:
        - Creates/updates 'bounty_stats.db' SQLite database.
        - Appends to 'exploration_raw.jsonl' if enabled.
        - Writes 'output.md' and 'output.json' (if not dry-run).
    """
    log.info("Initiating GitHub Bounty Scraper pipeline …")
    start_time = time.time()

    signals = load_signals(config.signals_config_file)

    if config.enable_live_prices:
        from .config import CRYPTO_KEYWORDS
        await refresh_prices(CRYPTO_KEYWORDS)

    async with aiosqlite.connect(config.db_file) as db_conn:
        await init_db(db_conn)
        sem = asyncio.Semaphore(config.semaphore_limit)
        committer = BatchCommitter(db_conn, config.batch_commit_size)

        # Phase 1: Discovery
        issues = await discover_issues(config)
        if not issues:
            log.info("No issues discovered. Exiting.")
            return

        # Respect max_issues cap.
        if config.max_issues and len(issues) > config.max_issues:
            issues = issues[: config.max_issues]
            
        if len(issues) > MAX_ISSUES_PER_RUN:
            log.warning(
                "Issue list (%d) exceeded MAX_ISSUES_PER_RUN=%d; "
                "processing first %d only. Lower --max-issues to suppress.",
                len(issues), MAX_ISSUES_PER_RUN, MAX_ISSUES_PER_RUN,
            )

        issues_to_enrich = issues[:MAX_ISSUES_PER_RUN]

        log.info(
            "Processing %d issues with concurrent GraphQL enrichment …",
            len(issues_to_enrich),
        )

        # Phase 2: Enrichment + scoring (with error isolation)
        bucket = TokenBucket(config.token_bucket_capacity, config.token_bucket_fill_rate)
        seen_aggregators: set[str] = set()

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Pre-enrichment rate limit check
            rl_query = "query { rateLimit { remaining resetAt } }"
            rl_data = await fetch_graphql(session, bucket, config.github_token, rl_query)
            if rl_data and "rateLimit" in rl_data:
                rem = rl_data["rateLimit"]["remaining"]
                if rem < 200:
                    log.warning("GraphQL rate limit critically low (%d remaining). Stopping Phase 2 early.", rem)
                    issues_to_enrich = []

            tasks = [
                _process_with_retry(
                    session, bucket, issue, db_conn, sem,
                    config, signals, committer, seen_aggregators,
                )
                for issue in issues_to_enrich
            ]

            # Process with progress reporting.
            results: list[Any] = []
            completed = 0
            for coro in asyncio.as_completed(tasks):
                try:
                    result = await coro
                except Exception as exc:
                    log.error("Unhandled error processing issue: %s", exc)
                    result = None
                results.append(result)
                completed += 1
                if completed % config.progress_every == 0:
                    log.info("Progress: %d / %d issues processed …", completed, len(issues_to_enrich))

        # Final commit.
        await committer.flush()

    all_leads = [r for r in results if r]
    elapsed = time.time() - start_time

    log.info(
        "Pipeline complete: %d leads from %d issues in %.1fs.",
        len(all_leads), len(issues), elapsed,
    )

    # Output.
    if not config.dry_run and config.output_file:
        write_output(all_leads, elapsed, config)
