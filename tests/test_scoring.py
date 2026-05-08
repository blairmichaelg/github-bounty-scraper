import datetime
import random

import pytest

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


def test_score_no_vibe_excludes_weight(cfg):
    """
    Test that vibe_score_int=None correctly renormalizes weights,
    unlike vibe_score_int=0 which actively penalizes by keeping the denominator the same.
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
    score_middle = compute_score(vibe_score_int=50, **base_args)

    # When vibe is None, remaining weights sum to 0.8 (since vibe=0.2),
    # so the score gets a 1/0.8 = 1.25x boost compared to vibe=0.
    # Therefore score_none should be higher than score_zero, and it
    # should be correctly renormalized.
    assert score_none > score_zero
    # With a middle vibe score (e.g. 50%), the score_none could be higher or lower
    # depending on whether the existing signals are stronger or weaker than 50%.
    # But it proves renormalization happens rather than treating None as 0 or 100.
    assert score_none != score_middle


@pytest.mark.parametrize(
    "weight_dist",
    [
        {
            "weight_amount": 1.0,
            "weight_recency": 0.0,
            "weight_activity": 0.0,
            "weight_escrow_strength": 0.0,
            "w_repo_reputation": 0.0,
            "weight_vibe": 0.0,
        },
        {
            "weight_amount": 0.0,
            "weight_recency": 1.0,
            "weight_activity": 0.0,
            "weight_escrow_strength": 0.0,
            "w_repo_reputation": 0.0,
            "weight_vibe": 0.0,
        },
        {
            "weight_amount": 0.0,
            "weight_recency": 0.0,
            "weight_activity": 0.0,
            "weight_escrow_strength": 0.0,
            "w_repo_reputation": 0.0,
            "weight_vibe": 1.0,
        },
        {
            "weight_amount": 0.2,
            "weight_recency": 0.2,
            "weight_activity": 0.2,
            "weight_escrow_strength": 0.2,
            "w_repo_reputation": 0.2,
            "weight_vibe": 0.0,
        },
    ],
)
def test_score_renormalization_edge_weights(cfg, weight_dist):
    """Test that score stays in [0, 100] even with extreme weight distributions and vibe=None."""
    for k, v in weight_dist.items():
        setattr(cfg, k, v)

    score = compute_score(
        numeric_amount=1000.0,
        issue_updated_at="2026-05-01T12:00:00Z",
        merges_last_45d=10,
        positive_escrow_count=2,
        positive_escrow_weight_sum=2.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=cfg,
    )
    assert 0.0 <= score <= 100.0


def test_score_all_weights_zero_except_vibe(cfg):
    """Test the edge case where all non-vibe weights are zero and vibe is None."""
    cfg.weight_amount = 0.0
    cfg.weight_recency = 0.0
    cfg.weight_activity = 0.0
    cfg.weight_escrow_strength = 0.0
    cfg.w_repo_reputation = 0.0
    cfg.weight_vibe = 0.2

    score = compute_score(
        numeric_amount=1000.0,
        issue_updated_at="2026-05-01T12:00:00Z",
        merges_last_45d=10,
        positive_escrow_count=2,
        positive_escrow_weight_sum=2.0,
        repo_reputation=0.5,
        vibe_score_int=None,
        has_negative_soft=False,
        config=cfg,
    )
    # total_w will be 0.0, scale becomes 1.0, raw_score becomes 0.0
    assert score == 0.0


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


class TestScoringEdgeCases:
    def test_invalid_timestamp_handled(self, cfg):
        """Invalid updated_at timestamp should be ignored (no recency bonus)."""
        score = compute_score(
            numeric_amount=100.0,
            issue_updated_at="invalid-date",
            merges_last_45d=10,
            positive_escrow_count=1,
            positive_escrow_weight_sum=1.0,
            repo_reputation=0.5,
            vibe_score_int=None,
            has_negative_soft=False,
            config=cfg,
        )
        assert score > 0

    def test_requires_hardware_penalty(self, cfg):
        """requires_hardware=True should halve the final score."""
        base_args = {
            "numeric_amount": 1000.0,
            "issue_updated_at": "2026-05-01T12:00:00Z",
            "merges_last_45d": 10,
            "positive_escrow_count": 2,
            "positive_escrow_weight_sum": 2.0,
            "repo_reputation": 0.5,
            "vibe_score_int": 50,
            "has_negative_soft": False,
            "config": cfg,
        }
        score_normal = compute_score(requires_hardware=False, **base_args)
        score_hardware = compute_score(requires_hardware=True, **base_args)
        assert score_hardware == pytest.approx(score_normal * cfg.hardware_penalty_factor, abs=0.02)

    def test_escrow_cap_applied(self, cfg):
        """escrow_weight_sum above ESCROW_WEIGHT_CAP should produce same score as at cap."""
        from github_bounty_scraper.config import ESCROW_WEIGHT_CAP

        base_args = {
            "numeric_amount": 1000.0,
            "issue_updated_at": "2026-05-01T12:00:00Z",
            "merges_last_45d": 10,
            "positive_escrow_count": 1,
            "repo_reputation": 0.5,
            "vibe_score_int": None,
            "has_negative_soft": False,
            "config": cfg,
        }
        score_at_cap = compute_score(positive_escrow_weight_sum=ESCROW_WEIGHT_CAP, **base_args)
        score_above_cap = compute_score(positive_escrow_weight_sum=ESCROW_WEIGHT_CAP + 10.0, **base_args)
        assert score_at_cap == score_above_cap

    def test_amount_norm_edge_cases(self, cfg):
        """Test normalization with amount=0, 1, and very large values."""
        base_args = {
            "issue_updated_at": "2026-05-01T12:00:00Z",
            "merges_last_45d": 10,
            "positive_escrow_count": 1,
            "positive_escrow_weight_sum": 1.0,
            "repo_reputation": 0.5,
            "vibe_score_int": None,
            "has_negative_soft": False,
            "config": cfg,
        }
        score_0 = compute_score(numeric_amount=0.0, **base_args)
        score_1 = compute_score(numeric_amount=1.0, **base_args)
        score_max = compute_score(numeric_amount=cfg.amount_norm_cap * 10, **base_args)

        assert score_0 < score_1
        # Maxed amount should match cap score
        score_at_cap = compute_score(numeric_amount=cfg.amount_norm_cap, **base_args)
        assert score_max == score_at_cap

    def test_all_zero_inputs(self, cfg):
        """An issue with no signals, no amount, and no vibe should produce exactly 0.0."""
        score = compute_score(
            numeric_amount=0.0,
            issue_updated_at=None,
            merges_last_45d=0,
            positive_escrow_count=0,
            positive_escrow_weight_sum=0.0,
            repo_reputation=0.0,
            vibe_score_int=None,
            has_negative_soft=False,
            config=cfg,
        )
        assert score == 0.0
