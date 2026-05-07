from __future__ import annotations

import csv
import os
import tempfile
import time

import aiosqlite
import joblib
import pytest

from github_bounty_scraper.db import (
    BatchCommitter,
    dump_dataset,
    get_recent_leads,
    get_repo_reputation,
    init_db,
    mark_issue_checked,
    repo_cache_check,
    set_issue_vibe,
    should_skip_issue,
    upsert_issue_stats,
    upsert_repo_stats,
)


@pytest.mark.asyncio
async def test_db_operations(mock_db_conn):
    db = mock_db_conn
    now = time.time()
    await mark_issue_checked(db, "http://1", now)
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


@pytest.mark.asyncio
async def test_dump_dataset():
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


@pytest.mark.asyncio
async def test_repo_cache_check(mock_db_conn):
    db = mock_db_conn
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as db_file:
        db_path = db_file.name
        db_file.close()
        try:
            async with aiosqlite.connect(db_path) as db:
                await init_db(db)
                await db.execute(
                    """
                    INSERT INTO issue_stats
                        (issue_url, lead_mode, checked_at, title, repo_name, numeric_amount)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("http://1", "strict", time.time(), "Bounty", "owner/repo", 100.0),
                )
                await db.commit()
            leads = await get_recent_leads(db_path, "strict", 10)
            assert len(leads) == 1
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


@pytest.mark.asyncio
async def test_get_recent_leads_excludes_vibe_only_rows(tmp_path):
    db_path = str(tmp_path / "test_recent_vibe_only.db")

    await set_issue_vibe(db_path, "url_vibe_only", 90, "On-chain escrow vault.", time.time(), compiled_signals=None)
    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        await conn.execute(
            """
            INSERT INTO issue_stats
                (issue_url, lead_mode, checked_at, title, repo_name, numeric_amount, score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("url_enriched", "strict", time.time(), "Real bounty", "owner/repo", 100.0, 42.0),
        )
        await conn.commit()

    leads = await get_recent_leads(db_path, "all", 10)
    assert [lead["issue_url"] for lead in leads] == ["url_enriched"]


@pytest.mark.asyncio
async def test_is_bounty_label_threshold(tmp_path):
    """Verify dump_dataset labeling logic with a custom threshold."""
    db_path = str(tmp_path / "test_bounty.db")
    out_path = str(tmp_path / "dataset.csv")

    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        # url1: ambiguous
        await conn.execute(
            "INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score) VALUES (?, ?, ?)", ("url1", 40.0, None)
        )
        # url2: positive
        await conn.execute(
            "INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score) VALUES (?, ?, ?)", ("url2", 60.0, 70)
        )
        # url3: negative
        await conn.execute(
            "INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score) VALUES (?, ?, ?)", ("url3", 10.0, 20)
        )
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=50.0)

    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {r["issue_url"]: r for r in reader}

    assert rows["url1"]["is_bounty"] == ""
    assert rows["url2"]["is_bounty"] == "1"
    assert rows["url3"]["is_bounty"] == "0"


def test_label_threshold_default_matches_config(cfg):
    """Default label_threshold in dump_dataset() should match ScraperConfig default."""
    import inspect

    from github_bounty_scraper.db import dump_dataset

    sig = inspect.signature(dump_dataset)
    default_val = sig.parameters["label_threshold"].default
    assert default_val == cfg.min_bounty_amount


@pytest.mark.asyncio
async def test_closed_issue_labeled_positive(tmp_path):
    """Historical/closed issues with high vibe should be labeled positive."""
    db_path = str(tmp_path / "test_closed_pos.db")
    out_path = str(tmp_path / "dataset_closed_pos.csv")

    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        await conn.execute(
            "INSERT INTO issue_stats (issue_url, lead_mode, numeric_amount, vibe_score) VALUES (?, ?, ?, ?)",
            ("url_closed", "closed_historical", 5.0, 70),
        )
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)

    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["is_bounty"] == "1"


@pytest.mark.asyncio
async def test_high_amount_is_positive_even_if_unscored(tmp_path):
    """High amount issues without vibe checks are ambiguous."""
    db_path = str(tmp_path / "test_high.db")
    out_path = str(tmp_path / "dataset_high.csv")

    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        await conn.execute(
            "INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score) VALUES (?, ?, ?)",
            ("url_high", 500.0, None),
        )
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)

    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["is_bounty"] == ""


@pytest.mark.asyncio
async def test_sentinel_amount_requires_escrow_to_be_positive(tmp_path):
    """Zero amount issues without vibe/escrow are labeled negative."""
    db_path = str(tmp_path / "test_sentinel.db")
    out_path = str(tmp_path / "dataset_sentinel.csv")

    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        await conn.execute(
            "INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score) VALUES (?, ?, ?)",
            ("url_sentinel", 0.0, None),
        )
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)

    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["is_bounty"] == "0"


@pytest.mark.asyncio
async def test_set_issue_vibe_signal_extraction(tmp_path):
    """set_issue_vibe should extract signals from the reason text."""
    from github_bounty_scraper.config import load_signals

    db_path = str(tmp_path / "test_vibe_signals.db")
    now = time.time()
    compiled_signals = load_signals()

    await set_issue_vibe(db_path, "url1", 80, "payout in eth. No KYC.", now, compiled_signals=compiled_signals)
    await set_issue_vibe(db_path, "url2", 90, "On-chain escrow vault.", now, compiled_signals=compiled_signals)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM issue_stats ORDER BY issue_url") as cur:
            rows = {r["issue_url"]: dict(r) for r in await cur.fetchall()}
            assert rows["url1"]["lead_mode"] == "vibe_only"
            assert rows["url1"]["escrow_verified"] == 0
            assert rows["url1"]["mentions_wallet_payout"] == 1
            assert rows["url1"]["mentions_no_kyc"] == 1
            assert rows["url2"]["has_onchain_escrow"] == 1


def test_model_feature_count_matches_json():
    """Sanity check that the model file matches its metadata features."""
    import json

    if not os.path.exists("bounty_model.pkl") or not os.path.exists("best_threshold.json"):
        pytest.skip("Model files not found")

    model = joblib.load("bounty_model.pkl")
    with open("best_threshold.json") as f:
        meta = json.load(f)

    saved_feats = meta.get("features", [])
    from github_bounty_scraper.__main__ import PROD_MODEL_FEATURES

    assert saved_feats == PROD_MODEL_FEATURES
    assert model.n_features_in_ == len(saved_feats)
    assert meta.get("leakage_free") is True
