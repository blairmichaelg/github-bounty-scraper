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

from .config import ESCROW_WEIGHT_CAP, ScraperConfig
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
    has_onchain_escrow: bool = False,
    mentions_no_kyc: bool = False,
    mentions_wallet_payout: bool = False,
    requires_hardware: bool = False,
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
        has_onchain_escrow: True if explicit on-chain escrow was detected.
        mentions_no_kyc: True if explicit "no KYC" was mentioned.
        mentions_wallet_payout: True if direct wallet payout was mentioned.

    Returns:
        A composite score rounded to 2 decimal places.
    """
    # Composite Score Formula:
    # Score = (W_amt * AmountNorm + W_rec * RecencyNorm + W_act * ActivityNorm + W_esc * EscrowNorm) * 100
    # where all Norm values are [0, 1] and Weights sum to 1.0.

    if numeric_amount <= 0:
        amount_norm = 0.0
    else:
        # Boosted normalization cap to 50k for better differentiation
        _norm_cap = 50000.0
        _log_cap = math.log10(_norm_cap + 1)
        amount_norm = min(math.log10(numeric_amount + 1) / _log_cap, 1.0)

    # ── Recency component ──
    recency_norm = 0.0
    if issue_updated_at:
        try:
            updated_dt = datetime.datetime.strptime(issue_updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc
            )
            days_ago = (datetime.datetime.now(datetime.timezone.utc) - updated_dt).total_seconds() / 86400.0
            recency_norm = math.exp(-math.log(2) * days_ago / 30.0)
        except ValueError:
            pass

    # ── Activity component ──
    # Cap activity at 30 merges for full bonus
    activity_norm = min(merges_last_45d, 30) / 30.0

    # ── Escrow component ──
    count_norm = min(positive_escrow_count / 5.0, 1.0)
    weighted_norm = min(positive_escrow_weight_sum / ESCROW_WEIGHT_CAP, 1.0)
    escrow_norm = max(count_norm, weighted_norm)

    # High activity trust bonus: if a repo is very active, lack of explicit escrow signals is less suspicious.
    if merges_last_45d >= 40:
        escrow_norm = max(escrow_norm, 0.4)

    if has_onchain_escrow:
        escrow_norm = min(escrow_norm + 0.25, 1.0)
    if mentions_wallet_payout:
        escrow_norm = min(escrow_norm + 0.15, 1.0)
    if mentions_no_kyc:
        escrow_norm = min(escrow_norm + 0.10, 1.0)

    # ── Vibe component ──
    if vibe_score_int is not None:
        vibe_norm = vibe_score_int / 100.0
        w_vibe = getattr(config, "weight_vibe", 0.20)
    else:
        vibe_norm = 0.0
        w_vibe = 0.0  # exclude vibe from denominator when unavailable

    w_amt = getattr(config, "weight_amount", 0.30)
    w_rec = getattr(config, "weight_recency", 0.10)
    w_act = getattr(config, "weight_activity", 0.15)
    w_esc = getattr(config, "weight_escrow_strength", 0.15)
    w_repo = getattr(config, "w_repo_reputation", 0.10)

    # Normalize so weights always sum to 1.0 regardless of vibe availability
    total_w = w_amt + w_rec + w_act + w_esc + w_repo + w_vibe
    if total_w <= 0:
        total_w = 1.0
    scale = 1.0 / total_w

    raw_score = (
        (
            amount_norm * w_amt
            + recency_norm * w_rec
            + activity_norm * w_act
            + escrow_norm * w_esc
            + repo_reputation * w_repo
            + vibe_norm * w_vibe
        )
        * scale
        * 100.0
    )

    # Soft negative penalty.
    if has_negative_soft:
        raw_score = max(raw_score - 10.0, 0.0)

    if requires_hardware:
        raw_score *= 0.5

    return round(max(0.0, min(100.0, raw_score)), 2)
