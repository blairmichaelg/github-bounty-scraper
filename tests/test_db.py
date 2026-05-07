import aiosqlite
import pytest

from github_bounty_scraper.db import (
    BatchCommitter,
    dump_dataset,
    get_repo_reputation,
    init_db,
    mark_issue_checked,
    should_skip_issue,
    upsert_issue_stats,
    upsert_repo_stats,
)


@pytest.mark.asyncio
async def test_db_operations():
    async with aiosqlite.connect(":memory:") as db:
        import time

        await init_db(db)
        await mark_issue_checked(db, "http://1", time.time())
        assert await should_skip_issue(db, "http://1", 50, 200) is True

        await upsert_repo_stats(
            db,
            "owner/repo",
            last_merged_pr_at=0,
            merges_last_45d=1,
            escrow_increment=1,
            rug_increment=0,
            snipe_increment=0,
            bounty_amount=0,
        )
        assert await get_repo_reputation(db, "owner/repo") == 1.0

        await upsert_issue_stats(
            db,
            "http://2",
            scraped_amount=100.0,
            numeric_amount=100.0,
            raw_display_amount="$100",
            currency_symbol="USD",
            score=1.0,
            last_updated_at=0,
            title="Title",
            repo_name="owner/repo",
            lead_mode="strict",
            escrow_verified=True,
            is_dead_repo=False,
            has_onchain_escrow=True,
            mentions_no_kyc=True,
            mentions_wallet_payout=True,
            positive_escrow_count=1,
            escrow_weight_sum=1.0,
            body_snippet="snippet",
        )

        committer = BatchCommitter(db, 1)
        await committer.tick()
        await committer.flush()

    import os
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as db_file:
        db_path = db_file.name
        db_file.close()
        try:
            async with aiosqlite.connect(db_path) as db:
                await init_db(db)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
                f.close()
                await dump_dataset(db_path, f.name)
                os.remove(f.name)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
