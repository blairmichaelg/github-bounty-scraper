import time
from unittest.mock import MagicMock

import aiosqlite
import pytest

from github_bounty_scraper.core import _get_issue_meta
from github_bounty_scraper.db import init_db


@pytest.mark.asyncio
async def test_get_issue_meta_ttl_expiry():
    """Verify that vibe score is treated as None if it has expired based on TTL."""
    db_path = ":memory:"
    async with aiosqlite.connect(db_path) as db:
        await init_db(db)

        url = "http://test-ttl"
        vibe_score = 85
        now = time.time()

        # 1. Scored 10 hours ago, TTL is 20 hours -> should NOT be expired
        vibe_at = now - (10 * 3600)
        await db.execute(
            "INSERT INTO issue_stats (issue_url, vibe_score, vibe_scored_at) VALUES (?, ?, ?)",
            (url, vibe_score, vibe_at),
        )
        await db.commit()

        config = MagicMock()
        config.dry_run = False
        config.vibe_ttl_hours = 20

        _, vibe, _ = await _get_issue_meta(db, url, config)
        assert vibe == vibe_score

        # 2. Scored 30 hours ago, TTL is 20 hours -> should BE expired (None)
        vibe_at_expired = now - (30 * 3600)
        await db.execute("UPDATE issue_stats SET vibe_scored_at = ? WHERE issue_url = ?", (vibe_at_expired, url))
        await db.commit()

        _, vibe_expired, _ = await _get_issue_meta(db, url, config)
        assert vibe_expired is None


@pytest.mark.asyncio
async def test_get_issue_meta_dry_run():
    config = MagicMock()
    config.dry_run = True
    score, vibe, vibe_at = await _get_issue_meta(None, "url", config)
    assert score is None
    assert vibe is None
    assert vibe_at == 0.0
