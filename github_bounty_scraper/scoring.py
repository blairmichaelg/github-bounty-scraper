"""
Scoring model — computes a composite score for each issue based on
bounty amount, recency, repo activity, and escrow signal strength.

Formula
-------
Score = (W_amt * AmountNorm + W_rec * RecencyNorm + W_act * ActivityNorm +
         W_esc * EscrowNorm + W_repo * RepoRepNorm + W_vibe * VibeNorm) * 100.0

Ceiling Guarantee
-----------------
The score is guaranteed to be in [0, 100] as long as the weights sum to 1.0.

Weights used from ScraperConfig:
- weight_amount
- weight_recency
- weight_activity
- weight_escrow_strength
- w_repo_reputation
- weight_vibe

Example
-------
>>> from github_bounty_scraper.config import ScraperConfig
>>> cfg = ScraperConfig(weight_amount=0.2, weight_recency=0.2, weight_activity=0.2, weight_escrow_strength=0.15, w_repo_reputation=0.1, weight_vibe=0.15)
>>> score = compute_score(numeric_amount=500.0, issue_updated_at="2026-05-01T12:00:00Z", merges_last_45d=10, positive_escrow_count=3, positive_escrow_weight_sum=2.5, repo_reputation=0.8, vibe_score_int=85, has_negative_soft=False, config=cfg)
"""

from __future__ import annotations

import datetime
import math

from .config import ScraperConfig
from .log import get_logger

log = get_logger()


def compute_score(
    *,
    numeric_amount: float,
    issue_updated_at: str | None,
    merges_last_45d: int,
    positive_escrow_count: int,
    positive_escrow_weight_sum: float,
    repo_reputation: float,
    vibe_score_int: int | None,
    has_negative_soft: bool,
    config: ScraperConfig,
) -> float:
    """Compute a composite score in [0, 100] for an issue.

    Args:
        numeric_amount: Parsed USD value of the bounty.
        issue_updated_at: GitHub ISO-8601 timestamp of last issue update.
        merges_last_45d: Number of merged PRs in the repo in the last 45 days.
        positive_escrow_count: Number of unique positive escrow signals found.
        positive_escrow_weight_sum: Heuristic sum of weights for matched signals.
        repo_reputation: Historical reliability score [0, 1] for the repo.
        vibe_score_int: Optional [0, 100] score from Gemini vibe-check.
        has_negative_soft: True if minor negative signals were detected.
        config: ScraperConfig containing weights and thresholds.

    Returns:
        A composite score rounded to 2 decimal places.
    """
    # Composite Score Formula:
    # Score = (W_amt * AmountNorm + W_rec * RecencyNorm + W_act * ActivityNorm + W_esc * EscrowNorm) * 100
    # where all Norm values are [0, 1] and Weights sum to 1.0.

    # ── Amount component ──
    if numeric_amount <= 0:
        amount_norm = 0.0
    else:
        # log10(100_001) ≈ 5.0 → max normalised value = 1.0
        amount_norm = min(math.log10(numeric_amount + 1) / 5.0, 1.0)

    # ── Recency component ──
    recency_norm = 0.0  # unknown age → no recency bonus
    if issue_updated_at:
        try:
            updated_dt = datetime.datetime.strptime(
                issue_updated_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
            days_ago = (
                datetime.datetime.now(datetime.timezone.utc) - updated_dt
            ).total_seconds() / 86400.0
            # Exponential decay, half-life = 30 days.
            recency_norm = math.exp(-math.log(2) * days_ago / 30.0)
        except ValueError:
            pass

    # ── Activity component ──
    activity_norm = min(merges_last_45d, 20) / 20.0

    # 5+ distinct positive escrow signal hits = full escrow score.
    # (Using the total signal list length as divisor made this nearly
    # always ~0, since the config list has 25+ entries.)
    count_norm = min(positive_escrow_count / 5.0, 1.0)
    
    # New weighted norm; cap at a reasonable max (e.g. 5.0)
    weighted_norm = min(positive_escrow_weight_sum / 5.0, 1.0)
    
    escrow_norm = max(count_norm, weighted_norm)

    # ── Vibe component — normalised to [0, 1] ──
    vibe_norm = (vibe_score_int or 0) / 100.0

    # ── Weighted composite — all components stay in [0, 1] before * 100 ──
    raw_score = (
        amount_norm    * config.weight_amount
        + recency_norm * config.weight_recency
        + activity_norm * config.weight_activity
        + escrow_norm  * config.weight_escrow_strength
        + repo_reputation * config.w_repo_reputation
        + vibe_norm    * config.weight_vibe
    ) * 100.0

    # Soft negative penalty.
    if has_negative_soft:
        raw_score = max(raw_score - 10.0, 0.0)

    return round(raw_score, 2)
