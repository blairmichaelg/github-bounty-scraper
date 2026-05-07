from __future__ import annotations

import re

from github_bounty_scraper.signals import _parse_gh_ts, apply_hard_disqualifiers, compute_soft_signals


def _make_issue(
    title: str = "",
    body: str = "",
    comments: list[dict] | None = None,
    labels: list[dict] | None = None,
    author: str = "user",
) -> dict:
    return {
        "title": title,
        "body": body,
        "comments": {"nodes": comments or []},
        "author": {"login": author},
        "labels": {"nodes": labels or []},
        "createdAt": "2024-01-01T00:00:00Z",
    }


# === Section 1: no_kyc detection ===
def test_no_kyc_detected():
    issue = _make_issue(title="Bounty: fix the bug", body="No KYC required. Payout in USDC.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"no_kyc_phrases_re": re.compile("no kyc")},
    )
    assert result.mentions_no_kyc is True


# === Section 2: wallet_payout detection ===
def test_wallet_payout_detected():
    issue = _make_issue(body="Reward sent directly to your wallet address.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"wallet_payout_phrases_re": re.compile("wallet address")},
    )
    assert result.mentions_wallet_payout is True


# === Section 3: hardware_requirement detection ===
def test_hardware_requirement_detected():
    issue = _make_issue(body="Requires a hardware security key (YubiKey) for testing.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"hardware_dependency_phrases_re": re.compile("hardware")},
    )
    assert result.requires_hardware is True


# === Section 4: blocked_domain / negative signal detection ===
def test_blocked_domain_flags_negative():
    issue = _make_issue(body="See details at grabify.link for more info.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"soft_negative_signals_re": re.compile(r"grabify\.link")},
    )
    assert result.has_negative_soft is True


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

def test_apply_hard_disqualifiers_malformed_labels():
    signals = {"kill_labels": ["spam"]}
    # Label without a name
    disq, reason = apply_hard_disqualifiers(
        issue_state="OPEN", labels_nodes=[{"not_name": "spam"}], body="hello", comments=[], signals=signals
    )
    assert disq is False
    # Label as None
    disq, reason = apply_hard_disqualifiers(
        issue_state="OPEN", labels_nodes=[None], body="hello", comments=[], signals=signals
    )
    assert disq is False


# === Section 5: escrow signal detection ===
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


# === Section 6: comment body inclusion in scan ===
def test_comment_text_included_in_signal_scan():
    """Signal in a comment (not the body) should still be detected."""
    issue = _make_issue(body="Bounty available.")
    comments = [{"body": "No KYC required, payout to your wallet."}]
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=comments,
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"no_kyc_phrases_re": re.compile("no kyc")},
    )
    assert result.mentions_no_kyc is True


# Utility tests
def test_parse_gh_ts():
    ts = _parse_gh_ts("2024-05-06T20:00:00Z")
    assert ts.year == 2024
    assert _parse_gh_ts("") is None
    assert _parse_gh_ts("invalid") is None


def test_clean_issue_has_no_negatives():
    issue = _make_issue(title="Fix memory leak in parser", body="Standard bounty. $500 reward.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={},
    )
    assert result.has_negative_soft is False
    assert result.requires_hardware is False
