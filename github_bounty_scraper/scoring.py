"""
Scoring model — computes a composite score for each issue based on
bounty amount, recency, repo activity, and escrow signal strength.
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
    has_negative_soft: bool,
    config: ScraperConfig,
) -> float:
    """Compute a composite score in [0, 100] for an issue.

    Components
    ----------
    1. **Amount** — ``log10(amount + 1)`` normalised to [0, 1], capped at
       $100k.  Weight: ``config.weight_amount``.
    2. **Recency** — Exponential decay with 30-day half-life based on
       ``issue_updated_at``.  Weight: ``config.weight_recency``.
    3. **Repo activity** — ``min(merges_45d, 20) / 20``.
       Weight: ``config.weight_activity``.
    4. **Escrow strength** — ``min(positive_escrow_count / 5, 1)``.  5+ distinct
       escrow signal hits = full score.  Weight: ``config.weight_escrow_strength``.

    A soft negative penalty of −10 is applied if soft negative signals are
    present but didn't trigger a hard disqualifier.
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

    # ── Escrow strength component ──
    # 5+ distinct positive escrow signal hits = full escrow score.
    # (Using the total signal list length as divisor made this nearly
    # always ~0, since the config list has 25+ entries.)
    escrow_norm = min(positive_escrow_count / 5.0, 1.0)

    # ── Weighted composite ──
    raw_score = (
        amount_norm * config.weight_amount
        + recency_norm * config.weight_recency
        + activity_norm * config.weight_activity
        + escrow_norm * config.weight_escrow_strength
    ) * 100.0

    # Soft negative penalty.
    if has_negative_soft:
        raw_score = max(raw_score - 10.0, 0.0)

    return round(raw_score, 2)
