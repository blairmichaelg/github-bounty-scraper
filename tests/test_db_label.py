import pytest
import os
import csv
import aiosqlite
from github_bounty_scraper.db import dump_dataset
from github_bounty_scraper.config import ScraperConfig

@pytest.mark.asyncio
async def test_is_bounty_label_threshold(tmp_path):
    """Mock the DB rows and call dump_dataset with label_threshold=50.0; assert a $40 row gets is_bounty="" and a $60 row gets is_bounty=1."""
    db_path = str(tmp_path / "test_bounty.db")
    out_path = str(tmp_path / "dataset.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        
        # Row 1: $40 bounty, vibe 60. With threshold 50, this should be is_bounty="".
        # Logic: if amount >= threshold AND vibe is not None AND vibe >= 50 -> 1
        # if vibe < 30 -> 0
        # if vibe is not None and 30 <= vibe < 50 and amount < label_threshold -> 0
        # if amount == 0 and vibe is None -> 0
        # else ""
        # So $40 with threshold 50 and vibe 60 -> ""
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score, score) VALUES (?, ?, ?, ?)", ("url1", 40.0, 60, 50.0))
        
        # Row 2: $60 bounty, vibe 60. With threshold 50, this should be is_bounty=1.
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score, score) VALUES (?, ?, ?, ?)", ("url2", 60.0, 60, 70.0))
        
        # Row 3: $100 bounty, vibe 20. This should be is_bounty=0 (vibe < 30).
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score, score) VALUES (?, ?, ?, ?)", ("url3", 100.0, 20, 30.0))
        
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=50.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {r["issue_url"]: r for r in reader}
        
    assert rows["url1"]["is_bounty"] == ""
    assert rows["url2"]["is_bounty"] == "1"
    assert rows["url3"]["is_bounty"] == "0"

def test_label_threshold_default_matches_config():
    """Assert that the default label_threshold in dump_dataset() equals ScraperConfig().min_bounty_amount."""
    import inspect
    from github_bounty_scraper.db import dump_dataset
    from github_bounty_scraper.config import ScraperConfig
    
    sig = inspect.signature(dump_dataset)
    default_val = sig.parameters["label_threshold"].default
    
    assert default_val == ScraperConfig().min_bounty_amount

@pytest.mark.asyncio
async def test_closed_issue_labeled_positive(tmp_path):
    """Insert a row with lead_mode='closed_historical', vibe_score=70, numeric_amount=5.0 (below threshold).
    Call dump_dataset with label_threshold=25.0. Assert is_bounty == '1'.
    """
    db_path = str(tmp_path / "test_closed_pos.db")
    out_path = str(tmp_path / "dataset_closed_pos.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, lead_mode, vibe_score, numeric_amount, score) VALUES (?, ?, ?, ?, ?)", 
                           ("url_closed", "closed_historical", 70, 5.0, 50.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == "1"

@pytest.mark.asyncio
async def test_closed_issue_low_vibe_labeled_negative(tmp_path):
    """Insert a row with lead_mode='closed_historical', vibe_score=20, numeric_amount=5.0. Assert is_bounty == '0'."""
    db_path = str(tmp_path / "test_closed_neg.db")
    out_path = str(tmp_path / "dataset_closed_neg.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, lead_mode, vibe_score, numeric_amount, score) VALUES (?, ?, ?, ?, ?)", 
                           ("url_closed_neg", "closed_historical", 20, 5.0, 10.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == "0"

@pytest.mark.asyncio
async def test_unscored_high_amount_is_ambiguous(tmp_path):
    """Insert a row: numeric_amount=500.0, vibe_score=None, score=80.0.
    Assert is_bounty == "" (NOT "1" — unscored = ambiguous).
    """
    db_path = str(tmp_path / "test_unscored.db")
    out_path = str(tmp_path / "dataset_unscored.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score, score) VALUES (?, ?, ?, ?)", 
                           ("url_unscored", 500.0, None, 80.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == ""

@pytest.mark.asyncio
async def test_mid_vibe_low_amount_is_negative(tmp_path):
    """Insert a row: numeric_amount=5.0, vibe_score=40, score=20.0. Assert is_bounty == "0"."""
    db_path = str(tmp_path / "test_midvibe.db")
    out_path = str(tmp_path / "dataset_midvibe.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, vibe_score, score) VALUES (?, ?, ?, ?)", 
                           ("url_midvibe", 5.0, 40, 20.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == "0"
