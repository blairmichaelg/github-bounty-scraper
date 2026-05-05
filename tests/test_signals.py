from github_bounty_scraper.signals import apply_hard_disqualifiers, compute_soft_signals

def test_apply_hard_disqualifiers():
    signals = {
        "kill_labels": ["wontfix", "duplicate"],
        "negative_filters": ["not a bounty"],
    }
    
    # 1. CLOSED issue -> disqualified=True
    dq, reason = apply_hard_disqualifiers(
        issue_state="CLOSED",
        labels_nodes=[],
        body="hello",
        comments=[],
        signals=signals
    )
    assert dq is True
    assert "CLOSED" in reason.upper()

    # 2. Kill label "wontfix" -> disqualified=True
    dq, reason = apply_hard_disqualifiers(
        issue_state="OPEN",
        labels_nodes=[{"name": "wontfix"}],
        body="hello",
        comments=[],
        signals=signals
    )
    assert dq is True
    assert "kill label" in reason

    # 3. Body contains "not a bounty" -> disqualified=True
    dq, reason = apply_hard_disqualifiers(
        issue_state="OPEN",
        labels_nodes=[],
        body="this is not a bounty",
        comments=[],
        signals=signals
    )
    assert dq is True
    assert "negative filter" in reason

    # 4. Clean open issue -> disqualified=False
    dq, reason = apply_hard_disqualifiers(
        issue_state="OPEN",
        labels_nodes=[],
        body="legit bounty",
        comments=[],
        signals=signals
    )
    assert dq is False
    assert reason == ""

def test_compute_soft_signals():
    signals = {
        "positive_escrow": ["gitcoin", "bountysource"],
        "soft_negative_signals": ["spam"],
        "stale_signals": ["abandoned"],
        "active_signals": ["working on it"],
        "active_label_signals": ["in-progress"],
    }
    
    # 1. Body contains "gitcoin" -> has_positive_escrow=True
    res = compute_soft_signals(
        body="use gitcoin",
        comments=[],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals=signals
    )
    assert res.has_positive_escrow is True
    assert res.positive_escrow_count >= 1

    # 2. Comment contains "bountysource" -> positive_escrow_count >= 1
    res = compute_soft_signals(
        body="hello",
        comments=[{"body": "funded on bountysource", "createdAt": "2026-05-01T12:00:00Z"}],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals=signals
    )
    assert res.has_positive_escrow is True
    assert res.positive_escrow_count >= 1

    # 3. Body contains "spam" -> has_negative_soft=True
    res = compute_soft_signals(
        body="this is spam",
        comments=[],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals=signals
    )
    assert res.has_negative_soft is True

    # 4. Comment with "working on it" timestamp < 90 days ago -> lane_blocked=True
    import datetime
    recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = compute_soft_signals(
        body="hello",
        comments=[{"body": "working on it", "createdAt": recent}],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals=signals
    )
    assert res.lane_blocked is True

    # 5. No signals anywhere -> all counts zero, all bools False
    res = compute_soft_signals(
        body="nothing here",
        comments=[],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals=signals
    )
    assert res.positive_escrow_count == 0
    assert res.has_positive_escrow is False
    assert res.has_negative_soft is False
    assert res.lane_blocked is False

def test_escrow_weight_sum():
    signals = {
        "positive_escrow": ["bounty", "escrow address", "paid on merge"],
    }
    
    # "bounty" -> base 1.0
    # "escrow address" -> base 1.0 + 1.0 (escrow) + 1.0 (address) = 3.0
    # "paid on merge" -> base 1.0 + 0.5 (paid on merge) = 1.5
    # Total = 5.5
    
    res = compute_soft_signals(
        body="here is a bounty. the escrow address is 0x123. will be paid on merge.",
        comments=[],
        labels_nodes=[],
        timeline_nodes=[],
        issue={"assignees": {"totalCount": 0}},
        signals=signals
    )
    
    assert res.positive_escrow_count == 3
    assert res.escrow_weight_sum == 5.5
