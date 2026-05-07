import re

from github_bounty_scraper.signals import compute_soft_signals


def _make_issue(body="", labels=[], comments=[], author="user"):
    return {
        "body": body,
        "labels": {"nodes": labels},
        "comments": {"nodes": comments},
        "author": {"login": author},
        "title": "title",
        "createdAt": "2024-01-01T00:00:00Z",
    }


def test_signals_blocked_author():
    signals = {"blocked_authors": ["malice"]}
    issue = _make_issue(author="malice")
    res = compute_soft_signals(
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        issue=issue,
        signals=signals,
    )
    assert res.is_blocked is True
    assert "blocked author" in res.block_reason


def test_signals_active_labels():
    signals = {"active_label_signals_re": re.compile("active")}
    issue = _make_issue(labels=[{"name": "active"}])
    # Active label is treated as now - 1 day, so it should block the lane if no stale signals exist.
    res = compute_soft_signals(
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        issue=issue,
        signals=signals,
    )
    assert res.lane_blocked is True


def test_signals_escrow_weights():
    signals = {"positive_escrow": ["safe"], "positive_escrow_re": re.compile("safe")}
    issue = _make_issue(body="this is safe")
    res = compute_soft_signals(
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        issue=issue,
        signals=signals,
    )
    assert res.has_positive_escrow is True
    assert res.positive_escrow_count == 1
