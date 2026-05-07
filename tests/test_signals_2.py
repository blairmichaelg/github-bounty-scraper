import re

from github_bounty_scraper.signals import _parse_gh_ts, apply_hard_disqualifiers


def test_parse_gh_ts():
    ts = _parse_gh_ts("2024-05-06T20:00:00Z")
    assert ts.year == 2024
    assert _parse_gh_ts("") is None
    assert _parse_gh_ts("invalid") is None


def test_apply_hard_disqualifiers():
    signals = {
        "kill_labels": ["invalid", "spam"],
        "negative_filters_re": re.compile(r"this is spam"),
    }

    # Label hit
    disq, reason = apply_hard_disqualifiers(
        issue_state="OPEN", labels_nodes=[{"name": "spam"}], body="hello", comments=[], signals=signals
    )
    assert disq is True
    assert "kill label" in reason

    # Regex hit in body
    disq, reason = apply_hard_disqualifiers(
        issue_state="OPEN", labels_nodes=[], body="this is spam content", comments=[], signals=signals
    )
    assert disq is True
    assert "negative filter in body" in reason

    # Regex hit in comment
    disq, reason = apply_hard_disqualifiers(
        issue_state="OPEN", labels_nodes=[], body="hello", comments=[{"body": "this is spam"}], signals=signals
    )
    assert disq is True
    assert "negative filter in comment" in reason

    # Safe
    disq, reason = apply_hard_disqualifiers(
        issue_state="OPEN", labels_nodes=[], body="hello world", comments=[], signals=signals
    )
    assert disq is False
