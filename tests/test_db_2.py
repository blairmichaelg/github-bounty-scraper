import time

import aiosqlite
import pytest

from github_bounty_scraper.db import get_recent_leads, init_db, repo_cache_check


@pytest.mark.asyncio
async def test_repo_cache_check():
    async with aiosqlite.connect(":memory:") as db:
        await init_db(db)
        # Empty case
        assert await repo_cache_check(db, "test", 100, 100, 100) is False

        # Dead repo
        await db.execute(
            "INSERT INTO repo_stats (repo_name, merges_last_45d, last_checked_at) VALUES (?, ?, ?)",
            ("dead", 0, time.time()),
        )
        assert await repo_cache_check(db, "dead", 100, 10, 10) is True


@pytest.mark.asyncio
async def test_get_recent_leads():
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as db_file:
        db_path = db_file.name
        db_file.close()
        try:
            async with aiosqlite.connect(db_path) as db:
                await init_db(db)
                await db.execute(
                    "INSERT INTO issue_stats (issue_url, lead_mode, checked_at) VALUES (?, ?, ?)",
                    ("http://1", "strict", time.time()),
                )
                await db.commit()

            leads = await get_recent_leads(db_path, "strict", 10)
            assert len(leads) == 1
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
