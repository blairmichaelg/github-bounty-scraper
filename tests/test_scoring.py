import datetime
import random

import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.scoring import compute_score





@pytest.mark.parametrize("seed", range(20))
def test_score_ceiling_never_exceeded(cfg, seed):
    """Parametrize over 20 random combos of maxed inputs — assert score <= 100.0 every time."""
    random.seed(seed)
    score = compute_score(
        numeric_amount=random.uniform(0, 1000000),
        issue_updated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        merges_last_45d=random.randint(0, 100),
        positive_escrow_count=random.randint(0, 20),
        positive_escrow_weight_sum=random.uniform(0, 50),
        repo_reputation=random.uniform(0, 1),
        vibe_score_int=random.choice([None, random.randint(0, 100)]),
        has_negative_soft=False,
        config=cfg,
        has_onchain_escrow=random.choice([True, False]),
        mentions_no_kyc=random.choice([True, False]),
        mentions_wallet_payout=random.choice([True, False]),
    )
    assert score <= 100.0, f"Score {score} exceeded 100.0 with seed {seed}"


def test_score_floor_never_negative(cfg):
    """Assert score >= 0.0 for all-zero inputs."""
    score = compute_score(
        numeric_amount=0.0,
        issue_updated_at=None,
        merges_last_45d=0,
        positive_escrow_count=0,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.0,
        vibe_score_int=0,
        has_negative_soft=True,
        config=cfg,
    )
    assert score >= 0.0


def test_weight_sum_exactly_one(cfg):
    """Assert sum of all weight fields in ScraperConfig() == 1.0 (catches future drift immediately)."""
    total = (
        cfg.weight_amount
        + cfg.weight_recency
        + cfg.weight_activity
        + cfg.weight_escrow_strength
        + cfg.w_repo_reputation
        + cfg.weight_vibe
    )
    assert abs(total - 1.0) < 1e-9


def test_vibe_zero_and_none(cfg):
    """
    Test that vibe_score_int=None redistributes weight (higher score)
    while vibe_score_int=0 actively penalizes (lower score).
    """
    base_args = {
        "numeric_amount": 1000.0,
        "issue_updated_at": "2026-05-01T12:00:00Z",
        "merges_last_45d": 10,
        "positive_escrow_count": 2,
        "positive_escrow_weight_sum": 2.0,
        "repo_reputation": 0.5,
        "has_negative_soft": False,
        "config": cfg,
    }
    score_none = compute_score(vibe_score_int=None, **base_args)
    score_zero = compute_score(vibe_score_int=0, **base_args)
    assert score_none > score_zero


def test_negative_soft_reduces_score(cfg):
    """has_negative_soft=True should produce a lower score than False, all else equal."""
    base_args = {
        "numeric_amount": 1000.0,
        "issue_updated_at": "2026-05-01T12:00:00Z",
        "merges_last_45d": 10,
        "positive_escrow_count": 2,
        "positive_escrow_weight_sum": 2.0,
        "repo_reputation": 0.5,
        "vibe_score_int": 50,
        "config": cfg,
    }
    score_clean = compute_score(has_negative_soft=False, **base_args)
    score_soft_neg = compute_score(has_negative_soft=True, **base_args)
    assert score_soft_neg < score_clean
