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
    """Aggregated soft signal strengths for an issue."""

    positive_escrow_count: int = 0  # Number of unique positive escrow signals matched
    negative_filter_count: int = 0  # Number of negative filter hits (usually 0 if not disqualified)
    stale_signal_count: int = 0     # Number of stale-work indicators matched
    active_signal_count: int = 0    # Number of active-work indicators matched
    kill_label_hit: bool = False    # True if a 'kill' label (e.g., 'invalid') was matched
    lane_blocked: bool = False      # True if an active claim is newer than any stale signal
    ghost_squatter: bool = False    # True if the issue has a fresh, non-stale assignee
    is_closed: bool = False         # True if the issue state is CLOSED
    has_negative_soft: bool = False # True if non-critical negative signals were found
    has_positive_escrow: bool = False # True if at least one escrow signal matched


# ─── Helper: parse GitHub timestamp ──────────────────────────────────
def _parse_gh_ts(raw: str | None) -> datetime.datetime | None:
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
    labels_nodes: list[dict],
    body: str,
    comments: list[dict],
    signals: dict[str, list[str] | list[dict[str, Any]]],
) -> tuple[bool, str]:
    """Return ``(disqualified, reason)`` for hard-filter checks.

    Hard disqualifiers:
      - Issue is CLOSED.
      - Kill labels present.
      - Negative filter signals present.
    """
    # Safety net: core.py fast-paths CLOSED before calling here,
    # but guard in case future callers skip that check.
    if issue_state and issue_state.upper() == "CLOSED":
        return True, "issue is CLOSED"

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
    comments: list[dict],
    labels_nodes: list[dict],
    timeline_nodes: list[dict],
    issue: dict,
    signals: dict[str, list[str] | list[dict[str, Any]]],
    allow_assigned_if_stale: bool = True,
    active_signal_max_age_days: int = 90,
) -> SignalResult:
    """Compute soft signal strengths without filtering.

    Populates counts for positive escrow hits, stale/active signals,
    lane-blocked status, and ghost-squatter status.
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
    result.has_positive_escrow = result.positive_escrow_count > 0

    # ── Soft negative signals ──
    soft_neg = cast(list[str], signals.get("soft_negative_signals", []))
    if soft_neg:
        all_text = body_lower
        for c in comments:
            all_text += "\n" + c.get("body", "").lower()
        if any(s in all_text for s in soft_neg):
            result.has_negative_soft = True

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
    comments: list[dict], signals: dict[str, list[str] | list[dict[str, Any]]],
    active_signal_max_age_days: int = 90,
    labels_nodes: list[dict] | None = None,
) -> bool:
    """Return True if an active claim is more recent than any stale signal.

    ``True`` means the lane is **blocked** — someone is actively working.
    Claims older than *active_signal_max_age_days* are treated as stale.
    Also checks issue labels for active claim indicators.
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
    comments: list[dict],
    timeline_nodes: list[dict],
    signals: dict[str, list[str] | list[dict[str, Any]]],
) -> bool:
    """Return True if the most recent assignment looks stale."""
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
    issue: dict,
    comments: list[dict],
    timeline_nodes: list[dict],
    signals: dict[str, list[str] | list[dict[str, Any]]],
    allow_assigned_if_stale: bool,
) -> bool:
    """Return True if the issue has a **fresh** (non-stale) assignee.

    When *allow_assigned_if_stale* is True, stale/re-opened assignments
    are allowed through.
    """
    # Safety net: Verify assignees exist before checking for staleness.
    if issue.get("assignees", {}).get("totalCount", 0) > 0:
        if allow_assigned_if_stale and _is_assignment_stale(comments, timeline_nodes, signals):
            return False  # Stale assignment — let it through.
        return True  # Fresh assignment — skip.
    return False
