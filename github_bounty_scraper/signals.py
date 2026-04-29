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

from .log import get_logger

log = get_logger()


@dataclass
class SignalResult:
    """Aggregated soft signal strengths for an issue."""

    positive_escrow_count: int = 0
    negative_filter_count: int = 0
    stale_signal_count: int = 0
    active_signal_count: int = 0
    kill_label_hit: bool = False
    lane_blocked: bool = False
    ghost_squatter: bool = False
    is_closed: bool = False


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
    signals: dict[str, list[str]],
) -> tuple[bool, str]:
    """Return ``(disqualified, reason)`` for hard-filter checks.

    Hard disqualifiers:
      - Issue is CLOSED.
      - Kill labels present.
      - Negative filter signals present.
    """
    # Issue state
    if issue_state and issue_state.upper() == "CLOSED":
        return True, "issue is CLOSED"

    # Kill labels
    kill_switches = signals.get("kill_labels", [])
    for label in labels_nodes:
        l_name = label.get("name", "").lower()
        if any(k in l_name for k in kill_switches):
            return True, f"kill label '{l_name}'"

    # Negative filters
    neg_signals = signals.get("negative_filters", [])
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
    signals: dict[str, list[str]],
    allow_assigned_if_stale: bool = True,
) -> SignalResult:
    """Compute soft signal strengths without filtering.

    Populates counts for positive escrow hits, stale/active signals,
    lane-blocked status, and ghost-squatter status.
    """
    result = SignalResult()
    body_lower = body.lower()

    # ── Positive escrow count ──
    pos_signals = signals.get("positive_escrow", [])
    for s in pos_signals:
        if s in body_lower:
            result.positive_escrow_count += 1
    for c in comments:
        c_lower = c.get("body", "").lower()
        for s in pos_signals:
            if s in c_lower:
                result.positive_escrow_count += 1

    # ── Lane status (True = lane is blocked by an active claim) ──
    result.lane_blocked = _is_lane_blocked(comments, signals)

    # ── Ghost squatter (True = fresh non-stale assignee exists) ──
    result.ghost_squatter = _check_ghost_squatter(
        issue, comments, timeline_nodes, signals, allow_assigned_if_stale
    )

    return result


# ─── Positive escrow check (boolean) ────────────────────────────────
def check_positive_escrow(
    body: str, comments: list[dict], signals: dict[str, list[str]]
) -> bool:
    """Return True if at least one positive escrow signal is present."""
    pos_signals = signals.get("positive_escrow", [])
    body_lower = body.lower()
    if any(s in body_lower for s in pos_signals):
        return True
    for c in comments:
        if any(s in c.get("body", "").lower() for s in pos_signals):
            return True
    return False


# ─── Lane blocked (renamed from evaluate_lane_status for clarity) ───
def _is_lane_blocked(
    comments: list[dict], signals: dict[str, list[str]]
) -> bool:
    """Return True if an active claim is more recent than any stale signal.

    ``True`` means the lane is **blocked** — someone is actively working.
    """
    stale_signals = signals.get("stale_signals", [])
    active_signals = signals.get("active_signals", [])

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

    if max_active_ts is not None and (
        max_stale_ts is None or max_active_ts > max_stale_ts
    ):
        return True
    return False


# ─── Assignment staleness ───────────────────────────────────────────
def _is_assignment_stale(
    comments: list[dict],
    timeline_nodes: list[dict],
    signals: dict[str, list[str]],
) -> bool:
    """Return True if the most recent assignment looks stale."""
    stale_signals = signals.get("stale_signals", [])

    last_assigned_ts: datetime.datetime | None = None
    for node in timeline_nodes:
        # Use __typename for reliable event type detection (requires
        # the GraphQL query to request __typename on timelineItems).
        if node.get("__typename") == "AssignedEvent":
            dt = _parse_gh_ts(node.get("createdAt"))
            if dt and (last_assigned_ts is None or dt > last_assigned_ts):
                last_assigned_ts = dt

    if last_assigned_ts is None:
        return False

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
    signals: dict[str, list[str]],
    allow_assigned_if_stale: bool,
) -> bool:
    """Return True if the issue has a **fresh** (non-stale) assignee.

    When *allow_assigned_if_stale* is True, stale/re-opened assignments
    are allowed through.
    """
    if issue.get("assignees", {}).get("totalCount", 0) > 0:
        if allow_assigned_if_stale and _is_assignment_stale(comments, timeline_nodes, signals):
            return False  # Stale assignment — let it through.
        return True  # Fresh assignment — skip.
    return False


# ─── Snipe detection ────────────────────────────────────────────────
def detect_snipe(timeline_nodes: list[dict]) -> bool:
    """Return True if a non-draft open PR that will auto-close the issue exists."""
    for node in timeline_nodes:
        typename = node.get("__typename", "")
        if typename in ("CrossReferencedEvent", "ConnectedEvent"):
            source = node.get("source")
            if (
                source
                and source.get("state") == "OPEN"
                and source.get("isDraft") is False
                and node.get("willCloseTarget") is True
            ):
                return True
    return False
