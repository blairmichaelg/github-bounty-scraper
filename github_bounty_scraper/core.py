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

from .bounty import detect_snipe, extract_bounty_amount
from .config import ScraperConfig, load_signals
from .db import (
    BatchCommitter,
    get_repo_reputation,
    init_db,
    mark_issue_checked,
    repo_cache_check,
    should_skip_issue,
    upsert_issue_stats,
    upsert_repo_stats,
)
from .discovery import discover_issues_stream
from .graphql import TokenBucket, fetch_graphql, run_graphql_audit
from .log import get_logger
from .output import write_output
from .price_cache import refresh_prices
from .scoring import compute_score
from .signals import (
    apply_hard_disqualifiers,
    compute_soft_signals,
)

log = get_logger()


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
    HasOnchainEscrow: bool
    MentionsNoKyc: bool
    MentionsWalletPayout: bool


def _append_raw(path: str, line: str) -> None:
    """Sync helper to append a line to the raw candidate log."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


# ─── Helpers ────────────────────────────────────────────────────────
def _check_repo_health(repo_data: dict[str, Any], config: ScraperConfig) -> bool:
    """Return True if the repository meets basic health criteria."""
    if repo_data.get("isArchived", False):
        return False
    if repo_data.get("isDisabled", False):
        return False
    if repo_data.get("isFork", False):
        return False
    stars = repo_data.get("stargazerCount", 0)
    if stars < config.min_stars:
        return False
    owner_type = repo_data.get("owner", {}).get("__typename", "")
    contrib_count = repo_data.get("mentionableUsers", {}).get("totalCount", 0)
    if owner_type.upper() == "USER" and contrib_count < 2:
        return False
    return True


def _build_text_context(issue: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    """Build the combined text string for signal scanning and bounty extraction."""
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    labels = issue.get("labels", {}).get("nodes", [])
    label_text = " ".join([L.get("name", "") for L in labels])
    concat_text = f"{title} {body} {label_text}"
    for c in comments:
        concat_text += " " + c.get("body", "")
    return concat_text


def _resolve_numeric_amount(issue: dict[str, Any], config: ScraperConfig) -> tuple[float, str, str]:
    """Extract bounty amount, apply cue fallbacks, and return numeric value with display metadata."""
    comments = issue.get("comments", {}).get("nodes", [])
    concat_text = _build_text_context(issue, comments)

    bounty = extract_bounty_amount(
        concat_text, max_sane=config.max_sane_amount, proximity_window=config.proximity_window, config=config
    )
    num_val = bounty.numeric_amount

    title_lower = (issue.get("title") or "").lower()
    labels_lower = [lbl.get("name", "").lower() for lbl in issue.get("labels", {}).get("nodes", [])]
    has_bounty_title = any(w in title_lower for w in ["bounty", "reward", "paid", "pays", "bounties"])
    has_bounty_label = any("bounty" in lbl or "reward" in lbl for lbl in labels_lower)
    has_cue = has_bounty_title or has_bounty_label

    if num_val == 0.0 and has_cue:
        num_val = -1.0

    return num_val, bounty.raw_display, bounty.currency_symbol


def _assemble_lead_result(
    issue: dict[str, Any],
    num_val: float,
    display: str,
    currency: str,
    score: float,
    prev_score: float | None,
    repo_name: str,
    soft: Any,
) -> LeadResult:
    """Construct the final LeadResult dict for verified bounties."""
    title = issue.get("title") or ""
    url = issue.get("html_url") or ""
    labels = issue.get("labels", {}).get("nodes", [])
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
        "PrevScore": prev_score,
        "HasOnchainEscrow": soft.has_onchain_escrow,
        "MentionsNoKyc": soft.mentions_no_kyc,
        "MentionsWalletPayout": soft.mentions_wallet_payout,
    }


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
    url = issue_item.get("html_url", "")
    if not url:
        return None
    parts = url.replace("https://github.com/", "").split("/")
    if len(parts) < 4:
        return None
    owner, repo = parts[0], parts[1]
    repo_name = f"{owner}/{repo}"

    aggregator_repos = cast(list[str], signals.get("aggregator_repos", []))
    if any(a in repo_name.lower() for a in aggregator_repos):
        if repo_name not in seen_aggregators:
            seen_aggregators.add(repo_name)
        return None

    issue_number = int(parts[3])

    if not config.no_cache:
        if await repo_cache_check(
            db_conn, repo_name, config.cache_ttl_dead, config.cache_ttl_low, config.cache_ttl_active
        ):
            return None

    async with sem:
        data = await run_graphql_audit(
            session,
            bucket,
            config.github_token,
            owner,
            repo,
            issue_number,
            pr_cap=config.pr_cap,
            tl_max_pages=config.tl_max_pages,
            tl_page_size=config.timeline_page_size,
        )

    if not data or not data.get("repository") or not data["repository"].get("issue"):
        return None

    repository = data["repository"]
    issue = repository["issue"]

    is_lead_candidate = _check_repo_health(repository, config)
    raw_reasons = [] if is_lead_candidate else ["failed_repo_health"]

    if not is_lead_candidate and not config.log_raw_candidates:
        return None

    issue_state = issue.get("state", "")
    lead_mode_override = None
    if issue_state.upper() == "CLOSED":
        if not config.include_closed_for_training:
            return None
        lead_mode_override = "closed_historical"

    issue_updated_at_raw = issue.get("updatedAt", "")

    if not config.no_cache and issue_updated_at_raw:
        try:
            updated_dt = datetime.datetime.strptime(issue_updated_at_raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc
            )
            if await should_skip_issue(db_conn, url, updated_dt.timestamp(), config.cache_ttl_active):
                return None
        except ValueError:
            pass

    prs = repository.get("pullRequests", {}).get("nodes", [])
    forty_five_days_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)
    merges_last_45 = 0
    last_merged_at_ts = 0.0

    for pr in prs:
        merged_raw = pr.get("mergedAt")
        if merged_raw:
            try:
                dt = datetime.datetime.strptime(merged_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                ts = dt.timestamp()
                if ts > last_merged_at_ts:
                    last_merged_at_ts = ts
                if dt >= forty_five_days_ago:
                    merges_last_45 += 1
            except ValueError:
                pass

    is_dead_repo_flag = False
    if merges_last_45 == 0:
        repo_created_raw = repository.get("createdAt")
        is_new_repo = False
        if repo_created_raw:
            try:
                created_dt = datetime.datetime.strptime(repo_created_raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc
                )
                age_days = (datetime.datetime.now(datetime.timezone.utc) - created_dt).days
                is_new_repo = age_days < config.new_repo_grace_days
            except ValueError:
                pass
        if not is_new_repo:
            is_dead_repo_flag = True
            if not (config.mode == "opportunistic" and config.opportunistic_allow_dead_repos):
                if not config.dry_run:
                    await mark_issue_checked(db_conn, url, time.time())
                    await committer.tick()
                return None

    labels = issue.get("labels", {}).get("nodes", [])
    comments = issue.get("comments", {}).get("nodes", [])
    body = issue.get("body") or ""
    title = issue.get("title") or ""
    timeline_nodes = issue.get("timelineItems", {}).get("nodes", [])

    escrow_inc = rug_inc = snipe_inc = 0

    disqualified, reason = apply_hard_disqualifiers(
        issue_state=issue_state, labels_nodes=labels, body=body, comments=comments, signals=signals
    )
    if disqualified:
        if "negative" in reason or "kill label" in reason:
            rug_inc = 1
        if not config.dry_run:
            await upsert_repo_stats(
                db_conn,
                repo_name,
                last_merged_pr_at=last_merged_at_ts,
                merges_last_45d=merges_last_45,
                rug_increment=rug_inc,
            )
            await mark_issue_checked(db_conn, url, time.time())
            await committer.tick()
        return None

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

    if soft.is_blocked or soft.lane_blocked:
        if not config.dry_run:
            await mark_issue_checked(db_conn, url, time.time())
            await committer.tick()
        return None

    num_val, display, currency = _resolve_numeric_amount(issue, config)
    escrow_verified = soft.has_positive_escrow

    if is_lead_candidate:
        if config.mode == "opportunistic":
            if not escrow_verified and not (config.opportunistic_allow_no_escrow and num_val == -1.0):
                is_lead_candidate = False
                raw_reasons.append("no_positive_escrow_and_no_cue")
            if is_lead_candidate and num_val >= 0 and num_val < config.opportunistic_min_amount:
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
        if (
            not escrow_verified
            and "no_positive_escrow" not in raw_reasons
            and "no_positive_escrow_and_no_cue" not in raw_reasons
        ):
            raw_reasons.append("no_positive_escrow")
        if num_val == 0.0 and "no_parsable_amount" not in raw_reasons:
            raw_reasons.append("no_parsable_amount")

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
            "stars": repository.get("stargazerCount", 0),
            "is_org_owner": repository.get("owner", {}).get("__typename", "").upper() == "ORGANIZATION",
            "contributors_count": repository.get("mentionableUsers", {}).get("totalCount", 0),
            "is_fork": repository.get("isFork", False),
            "is_archived": repository.get("isArchived", False),
            "labels": [lbl.get("name") for lbl in labels],
            "body_snippet": body[:300].replace("\n", " ") if body else "",
            "reasons": raw_reasons if not is_lead_candidate else ["LEAD_CANDIDATE"],
        }
        await asyncio.to_thread(_append_raw, config.raw_candidates_file, json.dumps(cand) + "\n")

    if not is_lead_candidate and not (lead_mode_override == "closed_historical"):
        return None

    escrow_inc = 1 if escrow_verified else 0
    if detect_snipe(timeline_nodes):
        snipe_inc = 1
        if not config.dry_run:
            await upsert_repo_stats(
                db_conn,
                repo_name,
                last_merged_pr_at=last_merged_at_ts,
                merges_last_45d=merges_last_45,
                escrow_increment=escrow_inc,
                snipe_increment=snipe_inc,
            )
            await committer.tick()
        return None

    if soft.ghost_squatter:
        if not config.dry_run:
            await upsert_repo_stats(
                db_conn,
                repo_name,
                last_merged_pr_at=last_merged_at_ts,
                merges_last_45d=merges_last_45,
                escrow_increment=escrow_inc,
            )
            await committer.tick()
        return None

    if not config.dry_run:
        await upsert_repo_stats(
            db_conn,
            repo_name,
            last_merged_pr_at=last_merged_at_ts,
            merges_last_45d=merges_last_45,
            escrow_increment=escrow_inc,
            rug_increment=rug_inc,
            snipe_increment=snipe_inc,
            bounty_amount=num_val if num_val >= 0 else 0,
        )

    repo_rep = await get_repo_reputation(db_conn, repo_name)

    prev_score_for_output = None
    vibe_score_val = None
    if not config.dry_run:
        async with db_conn.execute(
            "SELECT score, vibe_score, vibe_scored_at FROM issue_stats WHERE issue_url = ?", (url,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                prev_score_for_output, vibe_score_val, vibe_scored_at = row[0], row[1], row[2] or 0.0
                if (
                    vibe_score_val is not None
                    and (time.time() - vibe_scored_at) > getattr(config, "vibe_ttl_hours", 48) * 3600
                ):
                    vibe_score_val = None

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
        has_onchain_escrow=soft.has_onchain_escrow,
        mentions_no_kyc=soft.mentions_no_kyc,
        mentions_wallet_payout=soft.mentions_wallet_payout,
        requires_hardware=soft.requires_hardware,
    )

    if not config.dry_run:
        try:
            _ts = (
                datetime.datetime.strptime(issue_updated_at_raw, "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=datetime.timezone.utc)
                .timestamp()
                if issue_updated_at_raw
                else 0.0
            )
        except ValueError:
            _ts = 0.0
        await upsert_issue_stats(
            db_conn,
            url,
            scraped_amount=num_val,
            numeric_amount=num_val,
            raw_display_amount=display,
            currency_symbol=currency,
            score=score,
            last_updated_at=_ts,
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
            body_snippet=body[:300].replace("\n", " ") if body else "",
        )
        await committer.tick()

    return _assemble_lead_result(issue, num_val, display, currency, score, prev_score_for_output, repo_name, soft)


# The rest is exactly the same as original

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
                session,
                bucket,
                issue_item,
                db_conn,
                sem,
                config,
                signals,
                committer,
                seen_aggregators,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < max_retries:
                log.warning(
                    "Transient error on %s (attempt %d/%d): %s",
                    issue_item.get("html_url", "?"),
                    attempt + 1,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(1 * (attempt + 1))
            else:
                log.error(
                    "Failed after %d retries: %s — %s",
                    max_retries,
                    issue_item.get("html_url", "?"),
                    exc,
                )
                return None
        except aiosqlite.OperationalError as exc:
            log.error(
                "DB error (non-retryable) on %s: %s",
                issue_item.get("html_url", "?"),
                exc,
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

        # Phase 1 & 2: Streaming Discovery and Enrichment
        bucket = TokenBucket(config.token_bucket_capacity, config.token_bucket_fill_rate)
        seen_aggregators: set[str] = set()
        queue: asyncio.Queue = asyncio.Queue(maxsize=config.semaphore_limit * 3)

        results: list[Any] = []
        completed = 0
        discovered = 0

        async def producer():
            nonlocal discovered
            try:
                async for issue in discover_issues_stream(config):
                    if config.max_issues_per_run > 0 and discovered >= config.max_issues_per_run:
                        log.info("Reached max_issues_per_run limit (%d)", config.max_issues_per_run)
                        break
                    await queue.put(issue)
                    discovered += 1
            except Exception as e:
                log.error("Producer error: %s", e)
            finally:
                await queue.put(None)  # Sentinel

        async def worker(session: aiohttp.ClientSession) -> None:
            nonlocal completed
            while True:
                item = await queue.get()
                if item is None:
                    await queue.put(None)  # Re-broadcast sentinel to other workers
                    queue.task_done()
                    break
                try:
                    result = await _process_with_retry(
                        session,
                        bucket,
                        item,
                        db_conn,
                        sem,
                        config,
                        signals,
                        committer,
                        seen_aggregators,
                    )
                except Exception as exc:
                    log.error("Unhandled error processing issue: %s", exc)
                    result = None

                if result:
                    results.append(result)
                completed += 1
                if completed % config.progress_every == 0:
                    log.info("Progress: %d issues processed ...", completed)
                queue.task_done()

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Pre-enrichment rate limit check
            rl_query = "query { rateLimit { remaining resetAt } }"
            rl_data = await fetch_graphql(session, bucket, config.github_token, rl_query)
            if rl_data and "rateLimit" in rl_data:
                rem = rl_data["rateLimit"]["remaining"]
                if rem < 200:
                    log.warning("GraphQL rate limit critically low (%d remaining). Aborting.", rem)
                    return

            log.info("Starting concurrent streaming pipeline...")
            prod_task = asyncio.create_task(producer())
            worker_tasks = [asyncio.create_task(worker(session)) for _ in range(config.semaphore_limit)]
            await asyncio.gather(prod_task, *worker_tasks)

        # Final commit.
        await committer.flush()

    all_leads = [r for r in results if r]
    elapsed = time.time() - start_time

    log.info(
        "Pipeline complete: %d leads from %d issues in %.1fs.",
        len(all_leads),
        discovered,
        elapsed,
    )

    # Output.
    if not config.dry_run and config.output_file:
        write_output(all_leads, elapsed, config)
