import pytest
import os
import csv
import aiosqlite
from github_bounty_scraper.db import dump_dataset
from github_bounty_scraper.config import ScraperConfig

@pytest.mark.asyncio
async def test_is_bounty_label_threshold(tmp_path):
    """Mock the DB rows and call dump_dataset with label_threshold=50.0; assert a $40 row gets is_bounty=0 and a $60 row gets is_bounty=1."""
    db_path = str(tmp_path / "test_bounty.db")
    out_path = str(tmp_path / "dataset.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT, has_onchain_escrow INTEGER, mentions_no_kyc INTEGER, mentions_wallet_payout INTEGER, positive_escrow_count INTEGER, escrow_weight_sum REAL)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        
        # Row 1: $40 bounty, no escrow. With threshold 50, this should be is_bounty=0.
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, positive_escrow_count, score) VALUES (?, ?, ?, ?)", ("url1", 40.0, 0, 50.0))
        
        # Row 2: $60 bounty, no escrow. With threshold 50, this should be is_bounty=1.
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, positive_escrow_count, score) VALUES (?, ?, ?, ?)", ("url2", 60.0, 0, 70.0))
        
        # Row 3: $10 bounty, WITH escrow. This should be is_bounty=1.
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, positive_escrow_count, score) VALUES (?, ?, ?, ?)", ("url3", 10.0, 1, 30.0))
        
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=50.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {r["issue_url"]: r for r in reader}
        
    assert rows["url1"]["is_bounty"] == "0"
    assert rows["url2"]["is_bounty"] == "1"
    assert rows["url3"]["is_bounty"] == "1"

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
    """Insert a row with numeric_amount=5.0 (below threshold).
    Call dump_dataset with label_threshold=25.0. Assert is_bounty == '0' (decoupled from closed status).
    """
    db_path = str(tmp_path / "test_closed_pos.db")
    out_path = str(tmp_path / "dataset_closed_pos.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT, has_onchain_escrow INTEGER, mentions_no_kyc INTEGER, mentions_wallet_payout INTEGER, positive_escrow_count INTEGER, escrow_weight_sum REAL)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, lead_mode, numeric_amount, positive_escrow_count, score) VALUES (?, ?, ?, ?, ?)", 
                           ("url_closed", "closed_historical", 5.0, 0, 50.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == "0"

@pytest.mark.asyncio
async def test_high_amount_is_positive_even_if_unscored(tmp_path):
    """Insert a row: numeric_amount=500.0.
    Assert is_bounty == '1' (decoupled from vibe_score).
    """
    db_path = str(tmp_path / "test_high.db")
    out_path = str(tmp_path / "dataset_high.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT, has_onchain_escrow INTEGER, mentions_no_kyc INTEGER, mentions_wallet_payout INTEGER, positive_escrow_count INTEGER, escrow_weight_sum REAL)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, score) VALUES (?, ?, ?)", 
                           ("url_high", 500.0, 80.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == "1"

@pytest.mark.asyncio
async def test_sentinel_amount_requires_escrow_to_be_positive(tmp_path):
    """Insert a row: numeric_amount=-1.0, no escrow. Assert is_bounty == '0'."""
    db_path = str(tmp_path / "test_sentinel.db")
    out_path = str(tmp_path / "dataset_sentinel.csv")
    
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE issue_stats (issue_url TEXT PRIMARY KEY, title TEXT, lead_mode TEXT, numeric_amount REAL, score REAL, prev_score REAL, escrow_verified INTEGER, is_dead_repo INTEGER, checked_at REAL, vibe_score INTEGER, vibe_reason TEXT, repo_name TEXT, has_onchain_escrow INTEGER, mentions_no_kyc INTEGER, mentions_wallet_payout INTEGER, positive_escrow_count INTEGER, escrow_weight_sum REAL)")
        await conn.execute("CREATE TABLE repo_stats (repo_name TEXT PRIMARY KEY, merges_last_45d INTEGER, escrows_seen INTEGER, rugs_seen INTEGER, total_escrows_seen INTEGER)")
        await conn.execute("INSERT INTO issue_stats (issue_url, numeric_amount, positive_escrow_count, score) VALUES (?, ?, ?, ?)", 
                           ("url_sentinel", -1.0, 0, 90.0))
        await conn.commit()

    await dump_dataset(db_path, out_path, label_threshold=25.0)
    
    with open(out_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        
    assert row["is_bounty"] == "0"
async def test_set_issue_vibe_signal_extraction(tmp_path):
    """Verify that set_issue_vibe extracts boolean signals from the reason text."""
    db_path = str(tmp_path / "test_vibe_signals.db")
    import time
    from github_bounty_scraper.db import set_issue_vibe
    
    # 1. Wallet + No KYC
    reason_1 = "Payout is direct wallet payout in ETH. No KYC."
    await set_issue_vibe(db_path, "url1", 80, reason_1, time.time())
    
    # 2. Escrow
    reason_2 = "On-chain escrow via Gnosis Safe vault."
    await set_issue_vibe(db_path, "url2", 90, reason_2, time.time())
    
    # 3. Centralized
    reason_3 = "Centralized platform with KYC. Payout method unspecified."
    await set_issue_vibe(db_path, "url3", 30, reason_3, time.time())
    
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM issue_stats ORDER BY issue_url") as cur:
            rows = {r["issue_url"]: dict(r) for r in await cur.fetchall()}
            
            assert rows["url1"]["mentions_wallet_payout"] == 1
            assert rows["url1"]["mentions_no_kyc"] == 1
            assert rows["url1"]["has_onchain_escrow"] == 0
            
            assert rows["url2"]["has_onchain_escrow"] == 1
            assert rows["url2"]["mentions_wallet_payout"] == 0
            
            assert rows["url3"]["has_onchain_escrow"] == 0
            assert rows["url3"]["mentions_no_kyc"] == 0
            assert rows["url3"]["mentions_wallet_payout"] == 0

def test_model_feature_count_matches_json():
    import joblib, json, os, pytest
    if not os.path.exists("bounty_model.pkl"):
        pytest.skip("bounty_model.pkl not found")
    model = joblib.load("bounty_model.pkl")
    with open("best_threshold.json") as f:
        meta = json.load(f)
    saved_feats = meta.get("features", [])
    assert model.n_features_in_ == len(saved_feats), (
        f"Model expects {model.n_features_in_} features "
        f"but best_threshold.json lists {len(saved_feats)}: {saved_feats}"
    )
    assert meta.get("leakage_free") is True, \
        "best_threshold.json missing leakage_free:true"
