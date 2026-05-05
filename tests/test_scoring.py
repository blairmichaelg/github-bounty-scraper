import pytest
import datetime
from github_bounty_scraper.scoring import compute_score
from github_bounty_scraper.config import ScraperConfig

def test_perfect_score(minimal_config):
    """Large amount, very recent, active repo, strong escrow, no soft negative."""
    score = compute_score(
        numeric_amount=50000.0,
        issue_updated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        merges_last_45d=20,
        positive_escrow_count=5,
        positive_escrow_weight_sum=5.0,
        repo_reputation=1.0,
        vibe_score_int=100,
        has_negative_soft=False,
        config=minimal_config,
    )
    assert score >= 85.0

def test_zero_amount(minimal_config):
    """numeric_amount=0, everything else maxed."""
    score = compute_score(
        numeric_amount=0.0,
        issue_updated_at="2026-05-05T12:00:00Z",
        merges_last_45d=10,
        positive_escrow_count=3,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=minimal_config,
    )
    # amount component is 0, total should be lower.
    assert score < 60.0

def test_negative_soft_penalty(minimal_config):
    """Test penalty for has_negative_soft=True."""
    # Baseline
    score1 = compute_score(
        numeric_amount=500.0,
        issue_updated_at="2026-05-05T12:00:00Z",
        merges_last_45d=5,
        positive_escrow_count=1,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=minimal_config,
    )
    # With penalty
    score2 = compute_score(
        numeric_amount=500.0,
        issue_updated_at="2026-05-05T12:00:00Z",
        merges_last_45d=5,
        positive_escrow_count=1,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=True,
        config=minimal_config,
    )
    assert score2 == max(0.0, score1 - 10.0)

def test_unknown_age(minimal_config):
    """issue_updated_at=None."""
    score_recent = compute_score(
        numeric_amount=500.0,
        issue_updated_at="2026-05-05T12:00:00Z",
        merges_last_45d=5,
        positive_escrow_count=1,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=minimal_config,
    )
    score_none = compute_score(
        numeric_amount=500.0,
        issue_updated_at=None,
        merges_last_45d=5,
        positive_escrow_count=1,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=minimal_config,
    )
    assert score_none < score_recent

@pytest.mark.parametrize("seed", range(10))
def test_score_range(minimal_config, seed):
    """Assert score is always in [0, 100]."""
    import random
    random.seed(seed)
    score = compute_score(
        numeric_amount=random.uniform(0, 200000),
        issue_updated_at="2026-01-01T00:00:00Z" if random.random() > 0.1 else None,
        merges_last_45d=random.randint(0, 50),
        positive_escrow_count=random.randint(0, 5),
        positive_escrow_weight_sum=random.uniform(0, 10),
        repo_reputation=random.uniform(0, 1),
        vibe_score_int=random.choice([None, random.randint(0, 100)]),
        has_negative_soft=random.choice([True, False]),
        config=minimal_config,
    )
    assert 0.0 <= score <= 100.0

def test_zero_sane_amount_guard(minimal_config):
    """max_sane_amount=0 should not crash."""
    minimal_config.max_sane_amount = 0
    score = compute_score(
        numeric_amount=500.0,
        issue_updated_at="2026-05-05T12:00:00Z",
        merges_last_45d=5,
        positive_escrow_count=1,
        positive_escrow_weight_sum=0.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=minimal_config,
    )
    assert isinstance(score, float)

def test_reputation_impact(minimal_config):
    """Higher reputation should increase score."""
    base_args = {
        "numeric_amount": 1000.0,
        "issue_updated_at": "2026-05-05T12:00:00Z",
        "merges_last_45d": 5,
        "positive_escrow_count": 1,
        "positive_escrow_weight_sum": 0.0,
        "vibe_score_int": None,
        "has_negative_soft": False,
        "config": minimal_config,
    }
    score_low = compute_score(repo_reputation=0.0, **base_args)
    score_high = compute_score(repo_reputation=1.0, **base_args)
    assert score_high > score_low

def test_vibe_impact(minimal_config):
    """Higher vibe score should increase score."""
    base_args = {
        "numeric_amount": 1000.0,
        "issue_updated_at": "2026-05-05T12:00:00Z",
        "merges_last_45d": 5,
        "positive_escrow_count": 1,
        "positive_escrow_weight_sum": 0.0,
        "repo_reputation": 0.5,
        "has_negative_soft": False,
        "config": minimal_config,
    }
    score_low = compute_score(vibe_score_int=0, **base_args)
    score_high = compute_score(vibe_score_int=100, **base_args)
    assert score_high > score_low
