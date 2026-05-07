import os
import tempfile

import aiosqlite
import pytest

from github_bounty_scraper.db import init_db
from github_bounty_scraper.vibe import run_vibe_check


@pytest.mark.asyncio
async def test_run_vibe_check_empty():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as raw_f:
        raw_f.close()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as db_f:
            db_path = db_f.name
            db_f.close()
            try:
                os.environ["GEMINI_API_KEY"] = "fake"
                async with aiosqlite.connect(db_path) as db:
                    await init_db(db)
                await run_vibe_check(raw_f.name, db_path, 10, "unscored")
            finally:
                if os.path.exists(raw_f.name):
                    os.remove(raw_f.name)
                if os.path.exists(db_path):
                    os.remove(db_path)
