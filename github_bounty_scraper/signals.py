"""
Signal detection and filtering — hard disqualifiers and soft signal
analysis for the scoring model.

All matching is case-insensitive: signal strings are lowercased at load
time (see config.load_signals), and input text is lowercased before
comparison.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, cast

from .log import get_logger

log = get_logger()

__all__ = [
    "SignalResult",
    "apply_hard_disqualifiers",
    "compute_soft_signals",
]


@dataclass
class SignalResult:
    """Aggregated soft signal strengths for an issue.

    Attributes:
        positive_escrow_count: Number of unique positive escrow signals matched.
        escrow_weight_sum: Heuristic weight sum of matched escrow signals.
        negative_filter_count: Number of negative filter hits (usually 0 if not disqualified).
        stale_signal_count: Number of stale-work indicators matched.
        active_signal_count: Number of active-work indicators matched.
        kill_label_hit: True if a 'kill' label (e.g., 'invalid') was matched.
        lane_blocked: True if an active claim is newer than any stale signal.
        ghost_squatter: True if the issue has a fresh, non-stale assignee.
        is_closed: True if the issue state is CLOSED.
        has_negative_soft: True if non-critical negative signals were found.
        has_positive_escrow: True if at least one escrow signal matched.
    """

    positive_escrow_count: int = 0
    escrow_weight_sum: float = 0.0
    negative_filter_count: int = 0
    stale_signal_count: int = 0
    active_signal_count: int = 0
    kill_label_hit: bool = False
    lane_blocked: bool = False
    ghost_squatter: bool = False
    is_closed: bool = False
    has_negative_soft: bool = False
    has_positive_escrow: bool = False
    has_onchain_escrow: bool = False
    mentions_no_kyc: bool = False
    mentions_wallet_payout: bool = False


# ─── Helper: parse GitHub timestamp ──────────────────────────────────
def _parse_gh_ts(raw: str | None) -> datetime.datetime | None:
    """Parse a GitHub ISO-8601 timestamp string into a UTC datetime.

    Args:
        raw: The raw timestamp string from the GitHub API.

    Returns:
        A timezone-aware datetime object, or None if the input is empty or malformed.
    """
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(
            raw, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


# ─── Hard disqualifiers ─────────────────────────────────────────────
def apply_hard_disqualifiers(
    *,
    issue_state: str,
    labels_nodes: list[dict[str, Any]],
    body: str,
    comments: list[dict[str, Any]],
    signals: dict[str, list[str] | list[dict[str, Any]]],
) -> tuple[bool, str]:
    """Return ``(disqualified, reason)`` for hard-filter checks.

    Args:
        issue_state: The state of the issue (e.g., 'OPEN', 'CLOSED').
        labels_nodes: List of label objects associated with the issue.
        body: The main text content of the issue.
        comments: List of comment objects associated with the issue.
        signals: Configuration dictionary containing signal keyword lists.

    Returns:
        A tuple of (is_disqualified, reason_string).
    """
    # Kill labels
    kill_switches = cast(list[str], signals.get("kill_labels", []))
    for label in labels_nodes:
        l_name = label.get("name", "").lower()
        if any(k in l_name for k in kill_switches):
            return True, f"kill label '{l_name}'"

    # Negative filters
    neg_signals = cast(list[str], signals.get("negative_filters", []))
    body_lower = body.lower()
    if any(s in body_lower for s in neg_signals):
        return True, "negative filter in body"
    for c in comments:
        if any(s in c.get("body", "").lower() for s in neg_signals):
            return True, "negative filter in comment"

    return False, ""


# ─── Soft signal computation ────────────────────────────────────────
def compute_soft_signals(
    *,
    body: str,
    comments: list[dict[str, Any]],
    labels_nodes: list[dict[str, Any]],
    timeline_nodes: list[dict[str, Any]],
    issue: dict[str, Any],
    signals: dict[str, list[str] | list[dict[str, Any]]],
    allow_assigned_if_stale: bool = True,
    active_signal_max_age_days: int = 90,
) -> SignalResult:
    """Compute soft signal strengths without filtering.

    Args:
        body: The main text content of the issue.
        comments: List of comment objects associated with the issue.
        labels_nodes: List of label objects associated with the issue.
        timeline_nodes: List of timeline events (assigned, unassigned, etc.).
        issue: The raw issue object from the GraphQL API.
        signals: Configuration dictionary containing signal keyword lists.
        allow_assigned_if_stale: Whether to treat assigned issues as open if assignment is stale.
        active_signal_max_age_days: Age threshold for active work signals.

    Returns:
        A SignalResult object containing aggregated signal strengths.
    """
    result = SignalResult()
    body_lower = body.lower()

    # ── Positive escrow count (set-based: count unique signal types) ──
    pos_signals = cast(list[str], signals.get("positive_escrow", []))
    escrow_hits: set[str] = set()
    for s in pos_signals:
        if s in body_lower:
            escrow_hits.add(s)
    for c in comments:
        c_lower = c.get("body", "").lower()
        for s in pos_signals:
            if s in c_lower:
                escrow_hits.add(s)

    result.positive_escrow_count = len(escrow_hits)
    
    # Calculate weighted sum for the unique hits
    all_text = body_lower
    for c in comments:
        all_text += "\n" + c.get("body", "").lower()
        
    for s in escrow_hits:
        base = 1.0
        if "escrow" in s:
            base += 1.0
        if "address" in s or "0x" in s:
            base += 1.0
        if "paid on merge" in s or "reward" in s:
            base += 0.5
            
        # Extra bonuses for clear on-chain / no-KYC preference
        if any(k in s for k in ["vault", "safe multisig", "gnosis safe", "multisig"]):
            base += 0.5
        if "no kyc" in all_text and ("payout" in all_text or "reward" in all_text or "paid" in all_text):
            # Add a small bonus once if no-KYC is mentioned in context
            base += 0.1  # Distributed across hits, or just add 0.5 to total
            
        result.escrow_weight_sum += base

    # Explicit global bonuses to escrow_weight_sum
    if "no kyc" in all_text:
        result.escrow_weight_sum += 0.5
    if any(k in all_text for k in ["vault", "safe multisig", "gnosis safe"]):
        result.escrow_weight_sum += 0.5

    result.has_positive_escrow = result.positive_escrow_count > 0

    # ── Soft negative signals ──
    soft_neg = cast(list[str], signals.get("soft_negative_signals", []))
    if soft_neg:
        all_text = body_lower
        for c in comments:
            all_text += "\n" + c.get("body", "").lower()
        if any(s in all_text for s in soft_neg):
            result.has_negative_soft = True

    # ── Derived booleans for payout structure ──
    no_kyc_phrases = cast(list[str], signals.get("no_kyc_phrases", []))
    wallet_phrases = cast(list[str], signals.get("wallet_payout_phrases", []))
    
    all_text = body_lower
    for c in comments:
        all_text += "\n" + c.get("body", "").lower()
        
    if any(s in all_text for s in no_kyc_phrases):
        result.mentions_no_kyc = True
    if any(s in all_text for s in wallet_phrases):
        result.mentions_wallet_payout = True
        
    onchain_keywords = ["vault", "safe multisig", "gnosis safe", "hats vault", "immunefi vault"]
    if any(k in all_text for k in onchain_keywords):
        result.has_onchain_escrow = True
    elif any(s in escrow_hits for s in ["escrow", "multisig", "on-chain escrow", "escrowed funds"]):
        # Also check explicit escrow hits
        result.has_onchain_escrow = True

    # ── Lane status (True = lane is blocked by an active claim) ──
    result.lane_blocked = _is_lane_blocked(
        comments, signals, active_signal_max_age_days,
        labels_nodes=labels_nodes,
    )

    # ── Ghost squatter (True = fresh non-stale assignee exists) ──
    result.ghost_squatter = _check_ghost_squatter(
        issue, comments, timeline_nodes, signals, allow_assigned_if_stale
    )

    return result


# ─── Lane blocked (renamed from evaluate_lane_status for clarity) ───
def _is_lane_blocked(
    comments: list[dict[str, Any]],
    signals: dict[str, list[str] | list[dict[str, Any]]],
    active_signal_max_age_days: int = 90,
    labels_nodes: list[dict[str, Any]] | None = None,
) -> bool:
    """Return True if an active claim is more recent than any stale signal.

    Args:
        comments: List of comment objects.
        signals: Configuration dictionary.
        active_signal_max_age_days: Threshold for signal staleness.
        labels_nodes: Optional list of labels to check for active signals.

    Returns:
        True if the issue "lane" is actively occupied by a contributor.
    """
    stale_signals = cast(list[str], signals.get("stale_signals", []))
    active_signals = cast(list[str], signals.get("active_signals", []))

    max_stale_ts: datetime.datetime | None = None
    max_active_ts: datetime.datetime | None = None

    for c in comments:
        c_body = c.get("body", "").lower()
        dt = _parse_gh_ts(c.get("createdAt"))
        if dt is None:
            continue

        if any(s in c_body for s in stale_signals):
            if max_stale_ts is None or dt > max_stale_ts:
                max_stale_ts = dt
        if any(s in c_body for s in active_signals):
            if max_active_ts is None or dt > max_active_ts:
                max_active_ts = dt

    now = datetime.datetime.now(datetime.timezone.utc)

    # Label-based active signal: treat matching labels as a recent claim.
    if labels_nodes:
        active_label_signals = cast(list[str], signals.get("active_label_signals", []))
        for label in labels_nodes:
            l_name = label.get("name", "").lower()
            if any(s in l_name for s in active_label_signals):
                # GitHub doesn't expose label timestamps easily,
                # so treat as max_active_ts = now − 1 day conservatively.
                candidate = now - datetime.timedelta(days=1)
                if max_active_ts is None or candidate > max_active_ts:
                    max_active_ts = candidate

    if max_active_ts is not None and (
        max_stale_ts is None or max_active_ts > max_stale_ts
    ):
        # Age cap: if the active claim is too old, treat as stale.
        age_days = (now - max_active_ts).days
        if age_days > active_signal_max_age_days:
            return False  # Active claim is too old — treat as stale.
        return True
    return False


# ─── Assignment staleness ───────────────────────────────────────────
def _is_assignment_stale(
    comments: list[dict[str, Any]],
    timeline_nodes: list[dict[str, Any]],
    signals: dict[str, list[str] | list[dict[str, Any]]],
) -> bool:
    """Return True if the most recent assignment looks stale.

    Args:
        comments: List of comment objects.
        timeline_nodes: List of timeline events (AssignedEvent, etc.).
        signals: Configuration dictionary.

    Returns:
        True if there is evidence that the current assignee has abandoned the task.
    """
    stale_signals = cast(list[str], signals.get("stale_signals", []))

    last_assigned_ts: datetime.datetime | None = None
    last_unassigned_ts: datetime.datetime | None = None
    for node in timeline_nodes:
        # Use __typename for reliable event type detection (requires
        # the GraphQL query to request __typename on timelineItems).
        if node.get("__typename") == "AssignedEvent":
            dt = _parse_gh_ts(node.get("createdAt"))
            if dt and (last_assigned_ts is None or dt > last_assigned_ts):
                last_assigned_ts = dt
        elif node.get("__typename") == "UnassignedEvent":
            dt = _parse_gh_ts(node.get("createdAt"))
            if dt and (last_unassigned_ts is None or dt > last_unassigned_ts):
                last_unassigned_ts = dt

    if last_assigned_ts is None:
        return False

    # If the most recent event is an unassignment, treat as stale.
    if last_unassigned_ts and last_unassigned_ts > last_assigned_ts:
        return True

    for c in comments:
        c_body = c.get("body", "").lower()
        dt = _parse_gh_ts(c.get("createdAt"))
        if dt is None:
            continue
        if any(s in c_body for s in stale_signals):
            if dt > last_assigned_ts:
                return True
    return False


# ─── Ghost squatter ─────────────────────────────────────────────────
def _check_ghost_squatter(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    timeline_nodes: list[dict[str, Any]],
    signals: dict[str, list[str] | list[dict[str, Any]]],
    allow_assigned_if_stale: bool,
) -> bool:
    """Return True if the issue has a **fresh** (non-stale) assignee.

    Args:
        issue: Raw issue object.
        comments: List of comments.
        timeline_nodes: List of timeline events.
        signals: Configuration dictionary.
        allow_assigned_if_stale: Whether to bypass filtering for stale assignments.

    Returns:
        True if the issue is considered "squatted" by an active assignee.
    """
    # Safety net: Verify assignees exist before checking for staleness.
    if issue.get("assignees", {}).get("totalCount", 0) > 0:
        if allow_assigned_if_stale and _is_assignment_stale(comments, timeline_nodes, signals):
            return False  # Stale assignment — let it through.
        return True  # Fresh assignment — skip.
    return False
