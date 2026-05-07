"""Tests for compute_soft_signals — verifies signal extraction logic."""

from __future__ import annotations

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.signals import compute_soft_signals


def _make_issue(title: str = "", body: str = "", comments: list[dict] | None = None) -> dict:
    return {
        "title": title,
        "body": body,
        "comments": {"nodes": comments or []},
        "author": {"login": "user"},
        "labels": {"nodes": []},
    }


def _cfg() -> ScraperConfig:
    return ScraperConfig()


def test_no_kyc_detected():
    issue = _make_issue(title="Bounty: fix the bug", body="No KYC required. Payout in USDC.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"no_kyc_phrases_re": __import__("re").compile("no kyc")},
    )
    assert result.mentions_no_kyc is True


def test_wallet_payout_detected():
    issue = _make_issue(body="Reward sent directly to your wallet address.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"wallet_payout_phrases_re": __import__("re").compile("wallet address")},
    )
    assert result.mentions_wallet_payout is True


def test_blocked_domain_flags_negative():
    issue = _make_issue(body="See details at grabify.link for more info.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"soft_negative_signals_re": __import__("re").compile(r"grabify\.link")},
    )
    assert result.has_negative_soft is True


def test_hardware_requirement_detected():
    issue = _make_issue(body="Requires a hardware security key (YubiKey) for testing.")
    result = compute_soft_signals(
        issue=issue,
        body=issue["body"],
        comments=issue["comments"]["nodes"],
        labels_nodes=issue["labels"]["nodes"],
        timeline_nodes=[],
        signals={"hardware_dependency_phrases_re": __import__("re").compile("hardware")},
    )
    assert result.requires_hardware is True


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
        signals={"no_kyc_phrases_re": __import__("re").compile("no kyc")},
    )
    assert result.mentions_no_kyc is True
