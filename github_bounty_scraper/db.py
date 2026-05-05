"""
Database initialisation, schema migration, and caching helpers.
"""

from __future__ import annotations
 
from typing import Any

import time

import aiosqlite

from .log import get_logger

log = get_logger()


# ─── Schema creation & migration ────────────────────────────────────
async def init_db(conn: aiosqlite.Connection) -> None:
    """Create or migrate the SQLite schema for caching.

    Uses ``ALTER TABLE … ADD COLUMN`` wrapped in try/except so the
    migration is idempotent and safe to run on every startup.
    """
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")

    # ── repo_stats ──
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS repo_stats (
            repo_name          TEXT PRIMARY KEY,
            last_checked_at    REAL,
            last_merged_pr_at  REAL,
            merges_last_45d    INTEGER DEFAULT 0,
            escrows_seen       INTEGER DEFAULT 0,
            rugs_seen          INTEGER DEFAULT 0,
            snipes_detected    INTEGER DEFAULT 0,
            first_seen_at      REAL,
            last_seen_at       REAL,
            total_escrows_seen INTEGER DEFAULT 0,
            max_bounty_amount  REAL DEFAULT 0
        )
    """)

    # Migration columns for repo_stats (safe to call repeatedly)
    for col_def in [
        "snipes_detected INTEGER DEFAULT 0",
        "first_seen_at REAL",
        "last_seen_at REAL",
        "total_escrows_seen INTEGER DEFAULT 0",
        "max_bounty_amount REAL DEFAULT 0",
    ]:
        try:
            await conn.execute(f"ALTER TABLE repo_stats ADD COLUMN {col_def};")
        except aiosqlite.OperationalError:
            pass  # Column already exists.

    # ── issue_stats ──
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS issue_stats (
            issue_url         TEXT PRIMARY KEY,
            checked_at        REAL,
            scraped_amount    REAL,
            first_seen_at     REAL,
            last_seen_at      REAL,
            last_updated_at   REAL,
            numeric_amount    REAL,
            raw_display_amount TEXT,
            currency_symbol   TEXT,
            score             REAL DEFAULT 0,
            title             TEXT DEFAULT '',
            repo_name         TEXT DEFAULT ''
        )
    """)

    # Migration columns for issue_stats
    for col_def in [
        "first_seen_at REAL",
        "last_seen_at REAL",
        "last_updated_at REAL",
        "numeric_amount REAL",
        "raw_display_amount TEXT",
        "currency_symbol TEXT",
        "score REAL DEFAULT 0",
        "title TEXT DEFAULT ''",
        "repo_name TEXT DEFAULT ''",
        "lead_mode TEXT DEFAULT 'strict'",
        "escrow_verified INTEGER DEFAULT 1",
        "is_dead_repo INTEGER DEFAULT 0",
        "vibe_score INTEGER",
        "vibe_reason TEXT",
        "vibe_checked_at REAL",
    ]:
        try:
            await conn.execute(f"ALTER TABLE issue_stats ADD COLUMN {col_def};")
        except aiosqlite.OperationalError:
            pass  # Column already exists.

    await conn.commit()


# ─── Upsert helpers (fixed: no longer reset counters to 0) ──────────
async def upsert_repo_stats(
    conn: aiosqlite.Connection,
    repo_name: str,
    *,
    last_merged_pr_at: float,
    merges_last_45d: int,
    escrow_increment: int = 0,
    rug_increment: int = 0,
    snipe_increment: int = 0,
    bounty_amount: float = 0.0,
) -> None:
    """Insert or update repo_stats, preserving cumulative counters."""
    now = time.time()
    await conn.execute(
        """
        INSERT INTO repo_stats
            (repo_name, last_checked_at, last_merged_pr_at, merges_last_45d,
             escrows_seen, rugs_seen, snipes_detected,
             first_seen_at, last_seen_at, total_escrows_seen, max_bounty_amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_name) DO UPDATE SET
            last_checked_at    = excluded.last_checked_at,
            last_merged_pr_at  = excluded.last_merged_pr_at,
            merges_last_45d    = excluded.merges_last_45d,
            escrows_seen       = repo_stats.escrows_seen + excluded.escrows_seen,
            rugs_seen          = repo_stats.rugs_seen + excluded.rugs_seen,
            snipes_detected    = repo_stats.snipes_detected + excluded.snipes_detected,
            first_seen_at      = COALESCE(repo_stats.first_seen_at, excluded.first_seen_at),
            last_seen_at       = excluded.last_seen_at,
            total_escrows_seen = repo_stats.total_escrows_seen + excluded.escrows_seen,
            max_bounty_amount  = MAX(repo_stats.max_bounty_amount, excluded.max_bounty_amount)
        """,
        (
            repo_name, now, last_merged_pr_at, merges_last_45d,
            escrow_increment, rug_increment, snipe_increment,
            now, now, escrow_increment, bounty_amount,
        ),
    )


async def upsert_issue_stats(
    conn: aiosqlite.Connection,
    issue_url: str,
    *,
    scraped_amount: float,
    numeric_amount: float,
    raw_display_amount: str,
    currency_symbol: str,
    score: float,
    last_updated_at: float = 0.0,
    title: str = "",
    repo_name: str = "",
    lead_mode: str = "strict",
    escrow_verified: bool = True,
    is_dead_repo: bool = False,
) -> None:
    """Insert or update issue_stats, preserving first_seen_at."""
    now = time.time()
    await conn.execute(
        """
        INSERT INTO issue_stats
            (issue_url, checked_at, scraped_amount,
             first_seen_at, last_seen_at, last_updated_at,
             numeric_amount, raw_display_amount, currency_symbol, score,
             title, repo_name, lead_mode, escrow_verified, is_dead_repo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(issue_url) DO UPDATE SET
            checked_at         = excluded.checked_at,
            scraped_amount     = excluded.scraped_amount,
            first_seen_at      = COALESCE(issue_stats.first_seen_at, excluded.first_seen_at),
            last_seen_at       = excluded.last_seen_at,
            last_updated_at    = excluded.last_updated_at,
            numeric_amount     = excluded.numeric_amount,
            raw_display_amount = excluded.raw_display_amount,
            currency_symbol    = excluded.currency_symbol,
            score              = excluded.score,
            title              = excluded.title,
            repo_name          = excluded.repo_name,
            lead_mode          = excluded.lead_mode,
            escrow_verified    = excluded.escrow_verified,
            is_dead_repo       = excluded.is_dead_repo
        """,
        (
            issue_url, now, scraped_amount,
            now, now, last_updated_at,
            numeric_amount, raw_display_amount, currency_symbol, score,
            title, repo_name, lead_mode, int(escrow_verified), int(is_dead_repo),
        ),
    )


# ─── Cache check (Section 5.3) ──────────────────────────────────────
async def should_skip_issue(
    conn: aiosqlite.Connection,
    issue_url: str,
    issue_updated_at: float,
    ttl: int,
) -> bool:
    """Return True if the issue was recently checked and hasn't been updated.

    Skipped when:
      - We've seen it before AND
      - The issue's updatedAt hasn't changed since our last check AND
      - Our last check is within *ttl* seconds of now.
    """
    async with conn.execute(
        "SELECT last_seen_at, last_updated_at FROM issue_stats WHERE issue_url = ?",
        (issue_url,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return False

    last_seen, last_updated = row
    now = time.time()

    if last_updated and issue_updated_at and last_updated >= issue_updated_at:
        if last_seen and (now - last_seen) < ttl:
            return True
    return False


# ─── Repo-level adaptive TTL check ──────────────────────────────────
async def repo_cache_check(
    conn: aiosqlite.Connection,
    repo_name: str,
    cfg_dead: int,
    cfg_low: int,
    cfg_active: int,
) -> bool:
    """Return True if the repo was recently checked and should be skipped.

    Adaptive TTL based on merge activity:
      - 0 merges (dead)   → skip for ``cfg_dead`` seconds (default 3 days).
      - 1-2 merges (low)  → skip for ``cfg_low`` seconds  (default 12 hours).
      - 3+ merges (active)→ skip for ``cfg_active`` seconds (default 2 hours).
    """
    async with conn.execute(
        "SELECT merges_last_45d, last_checked_at FROM repo_stats WHERE repo_name = ?",
        (repo_name,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return False

    merges, last_checked = row
    elapsed = time.time() - (last_checked or 0)

    if merges == 0 and elapsed < cfg_dead:
        return True
    if merges in (1, 2) and elapsed < cfg_low:
        return True
    if merges >= 3 and elapsed < cfg_active:
        return True
    return False


# ─── Batch commit helper ────────────────────────────────────────────
class BatchCommitter:
    """Accumulates DB operations and commits every *batch_size* calls.

    Usage::

        committer = BatchCommitter(conn, batch_size=25)
        # ... do many inserts/updates ...
        await committer.tick()   # commits if threshold reached
        # at the end:
        await committer.flush()  # final commit
    """

    def __init__(self, conn: aiosqlite.Connection, batch_size: int = 25):
        self.conn = conn
        self.batch_size = batch_size
        self._count = 0

    async def tick(self) -> None:
        self._count += 1
        if self._count >= self.batch_size:
            await self.conn.commit()
            self._count = 0

    async def flush(self) -> None:
        if self._count > 0:
            await self.conn.commit()
            self._count = 0


async def mark_issue_checked(
    conn: aiosqlite.Connection, issue_url: str, checked_at: float
) -> None:
    """Update or insert the check timestamp to refresh the cache TTL."""
    await conn.execute(
        """
        INSERT INTO issue_stats (issue_url, checked_at, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(issue_url) DO UPDATE SET
            checked_at   = excluded.checked_at,
            last_seen_at = excluded.last_seen_at
        """,
        (issue_url, checked_at, checked_at, checked_at),
    )


async def get_recent_leads(db_path: str, mode: str, limit: int) -> list[dict]:
    import os
    if not os.path.exists(db_path):
        return []
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        query = "SELECT score, numeric_amount, lead_mode, escrow_verified, is_dead_repo, repo_name, issue_url, vibe_score FROM issue_stats"
        params: list[Any] = []
        if mode != "all":
            query += " WHERE lead_mode = ?"
            params.append(mode)
        query += " ORDER BY checked_at DESC LIMIT ?"
        params.append(limit)

        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def set_issue_vibe(
    db_path: str,
    issue_url: str,
    vibe_score: int,
    vibe_reason: str,
    checked_at: float,
) -> None:
    """
    Upsert vibe-check metadata for an issue.

    If the issue already exists in issue_stats, update only the vibe_* fields.
    If it does not exist, insert a minimal row with these fields populated.
    """
    import os

    if not os.path.exists(db_path):
        # If the DB does not exist yet, nothing to update.
        return

    async with aiosqlite.connect(db_path) as conn:
        # Ensure schema is initialized/migrated
        await init_db(conn)

        # Try to update existing row first
        cursor = await conn.execute(
            """
            UPDATE issue_stats
            SET
                vibe_score     = ?,
                vibe_reason    = ?,
                vibe_checked_at = ?
            WHERE issue_url = ?
            """,
            (vibe_score, vibe_reason, checked_at, issue_url),
        )
        if cursor.rowcount == 0:
            log.debug(
                "vibe-check: no issue_stats row for %s — skipping orphan insert.",
                issue_url,
            )

        await conn.commit()
