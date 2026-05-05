import datetime
from github_bounty_scraper.signals import apply_hard_disqualifiers, compute_soft_signals

def test_hard_disqualify_closed():
    """CLOSED issue always returns True."""
    dq, reason = apply_hard_disqualifiers(
        issue_state="CLOSED",
        labels_nodes=[],
        body="legit bounty",
        comments=[],
        signals={"kill_labels": [], "negative_filters": []}
    )
    assert dq is True
    assert "CLOSED" in reason.upper()

def test_hard_disqualify_kill_label():
    """Issue with kill label returns True."""
    dq, reason = apply_hard_disqualifiers(
        issue_state="OPEN",
        labels_nodes=[{"name": "invalid"}],
        body="legit bounty",
        comments=[],
        signals={"kill_labels": ["invalid"], "negative_filters": []}
    )
    assert dq is True
    assert "kill label" in reason

def test_lane_blocked_fresh_claim():
    """Active claim comment with recent date returns lane_blocked=True."""
    recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = compute_soft_signals(
        body="hello",
        comments=[{"body": "i am working on this", "createdAt": recent}],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals={
            "active_signals": ["working on this"],
            "stale_signals": [],
            "positive_escrow": []
        }
    )
    assert res.lane_blocked is True

def test_lane_blocked_stale_claim():
    """Active claim older than active_signal_max_age_days returns lane_blocked=False."""
    # Default is 90 days.
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = compute_soft_signals(
        body="hello",
        comments=[{"body": "i am working on this", "createdAt": old}],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals={
            "active_signals": ["working on this"],
            "stale_signals": [],
            "positive_escrow": []
        },
        active_signal_max_age_days=90
    )
    assert res.lane_blocked is False

def test_ghost_squatter_fresh():
    """Issue with assignee and no stale signal returns ghost_squatter=True."""
    res = compute_soft_signals(
        body="hello",
        comments=[],
        labels_nodes=[],
        timeline_nodes=[
            {"__typename": "AssignedEvent", "createdAt": "2026-05-01T12:00:00Z"}
        ],
        issue={"assignees": {"totalCount": 1}},
        signals={
            "stale_signals": ["abandoned"],
            "active_signals": [],
            "positive_escrow": []
        }
    )
    assert res.ghost_squatter is True

def test_ghost_squatter_stale():
    """Assignee exists but stale signal in comment after assignment returns ghost_squatter=False."""
    assigned_at = "2026-05-01T12:00:00Z"
    stale_at = "2026-05-02T12:00:00Z"
    res = compute_soft_signals(
        body="hello",
        comments=[{"body": "abandoned", "createdAt": stale_at}],
        labels_nodes=[],
        timeline_nodes=[
            {"__typename": "AssignedEvent", "createdAt": assigned_at}
        ],
        issue={"assignees": {"totalCount": 1}},
        signals={
            "stale_signals": ["abandoned"],
            "active_signals": [],
            "positive_escrow": []
        },
        allow_assigned_if_stale=True
    )
    assert res.ghost_squatter is False
