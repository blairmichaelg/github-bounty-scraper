"""
Database initialisation, schema migration, and caching helpers.
"""

from __future__ import annotations
 
from typing import Any

import csv
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
        "prev_score REAL",
    ]:
        try:
            await conn.execute(f"ALTER TABLE issue_stats ADD COLUMN {col_def};")
        except aiosqlite.OperationalError:
            pass  # Column already exists.

    # ── Indexes ──
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_issue_stats_checked_at "
        "ON issue_stats(checked_at DESC);"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_issue_stats_lead_mode "
        "ON issue_stats(lead_mode);"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_issue_stats_score "
        "ON issue_stats(score DESC);"
    )

    # ── checked_cache ──
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS checked_cache (
            issue_url   TEXT PRIMARY KEY,
            checked_at  REAL NOT NULL
        );
    """)

    # ── Migration: Ghost row cleanup ──
    # Ghost-row cleanup — runs once only (guarded by user_version migration flag)
    async with conn.execute("PRAGMA user_version") as _uv_cur:
        _uv = (await _uv_cur.fetchone())[0]
    if _uv < 1:
        async with conn.execute(
            "SELECT COUNT(*) FROM issue_stats WHERE score = 0 AND numeric_amount IS NULL"
        ) as c2:
            ghost_count = (await c2.fetchone())[0]
            if ghost_count > 0:
                log.info("Migration v1: Purging %d zero-score ghost rows …", ghost_count)
                await conn.execute(
                    "DELETE FROM issue_stats WHERE score = 0 AND numeric_amount IS NULL"
                )
        await conn.execute("PRAGMA user_version = 1")

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
            title, repo_name, lead_mode, escrow_verified, is_dead_repo, prev_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(issue_url) DO UPDATE SET
            prev_score         = issue_stats.score,
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
            title, repo_name, lead_mode, int(escrow_verified), int(is_dead_repo), score,
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
    now = time.time()

    async with conn.execute(
        "SELECT checked_at, last_updated_at FROM issue_stats WHERE issue_url = ?",
        (issue_url,),
    ) as cursor:
        row = await cursor.fetchone()
    if row:
        last_seen, last_updated = row
        if last_updated and issue_updated_at and last_updated >= issue_updated_at:
            if last_seen and (now - last_seen) < ttl:
                return True

    async with conn.execute(
        "SELECT checked_at FROM checked_cache WHERE issue_url = ?",
        (issue_url,),
    ) as cursor:
        row = await cursor.fetchone()
    if row and row[0] and (now - row[0]) < ttl:
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
    """Insert or update a check timestamp in the tombstone cache."""
    await conn.execute(
        "INSERT OR REPLACE INTO checked_cache (issue_url, checked_at) VALUES (?, ?)",
        (issue_url, checked_at),
    )
    # We no longer need to commit here if using BatchCommitter, but for safety:
    # await conn.commit()


async def get_recent_leads(db_path: str, mode: str, limit: int) -> list[dict]:
    import os
    if not os.path.exists(db_path):
        return []
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        query = "SELECT score, prev_score, numeric_amount, lead_mode, escrow_verified, is_dead_repo, repo_name, issue_url, vibe_score FROM issue_stats"
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
            log.warning(
                "vibe-check: no issue_stats row for %s — score discarded. "
                "Run the main pipeline first to populate issue_stats.",
                issue_url,
            )

        await conn.commit()


async def get_repo_reputation(conn: aiosqlite.Connection, repo_name: str) -> float:
    """Return a reputation score in [0, 1] based on historical escrows vs rugs.
    
    If no history is available, returns 0.5 (neutral prior).
    """
    async with conn.execute(
        "SELECT total_escrows_seen, rugs_seen FROM repo_stats WHERE repo_name = ?",
        (repo_name,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return 0.5

    total_escrows, rugs = row
    if total_escrows == 0 and rugs == 0:
        return 0.5

    return total_escrows / (total_escrows + rugs)


async def dump_dataset(db_path: str, out_path: str, raw_file: str = "exploration_raw.jsonl") -> None:
    """Export the issue_stats table joined with repo_stats and raw body text to a CSV file."""
    import os
    import json
    import asyncio

    # Load bodies from exploration_raw.jsonl to enrich the dataset
    bodies = {}
    if os.path.exists(raw_file):
        def _read():
            with open(raw_file, "r", encoding="utf-8") as f:
                return f.read().splitlines()
        lines = await asyncio.to_thread(_read)
        for line in lines:
            if not line.strip(): continue
            try:
                obj = json.loads(line)
                key = obj.get("issue_url") or obj.get("url") or ""
                bodies[key] = obj.get("body_snippet") or obj.get("body") or ""
            except: pass

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        # Ensure schema is at least initialized (if DB was empty/new)
        await init_db(conn)

        query = """
            SELECT 
                i.issue_url,
                i.title,
                i.lead_mode,
                i.numeric_amount,
                i.score,
                i.prev_score,
                i.escrow_verified,
                i.is_dead_repo,
                i.checked_at,
                i.vibe_score,
                i.vibe_reason,
                r.merges_last_45d,
                r.escrows_seen,
                r.rugs_seen,
                r.total_escrows_seen
            FROM issue_stats i
            LEFT JOIN repo_stats r ON i.repo_name = r.repo_name
            WHERE i.score > 0
               OR (i.numeric_amount IS NOT NULL AND i.numeric_amount != 0)
            ORDER BY i.checked_at DESC
        """
        async with conn.execute(query) as cursor:
            rows = await cursor.fetchall()

        with open(out_path, "w", encoding="utf-8", newline="") as f:
            headers = [
                "issue_url", "title", "body_snippet", "lead_mode", "numeric_amount", 
                "score", "prev_score", "escrow_verified", "is_dead_repo", 
                "checked_at", "vibe_score", "vibe_reason", 
                "merges_last_45d", "escrows_seen", "rugs_seen", "total_escrows_seen"
            ]
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            
            if not rows:
                log.info("dump-dataset: no rows found, wrote headers to %s", out_path)
                return

            for row in rows:
                d = dict(row)
                # Join body text from jsonl
                d["body_snippet"] = bodies.get(d["issue_url"], "")
                writer.writerow(d)
        
        log.info("dump-dataset: exported %d rows to %s (enriched with %d bodies)", len(rows), out_path, len(bodies))
