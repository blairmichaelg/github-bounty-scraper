"""Tests for bounty amount extraction and snipe detection."""

from __future__ import annotations

import pytest

from github_bounty_scraper.bounty import detect_snipe, extract_bounty_amount


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$500 bounty for fixing this bug", 500.0),
        ("Reward: $1,500 USDC", 1500.0),
        ("10,000 USD prize", 10000.0),
        ("No amount mentioned here", 0.0),
        ("$0 bounty", 0.0),
    ],
)
def test_extract_amount(text: str, expected: float):
    result = extract_bounty_amount(text)
    assert result.numeric_amount == pytest.approx(expected, abs=1.0), f"Failed for: {text!r}"


@pytest.mark.parametrize(
    "comment_body",
    [
        "bounty paid, thanks!",
        "reward sent to your wallet",
        "payout complete",
        "payment sent",
        "funds transferred successfully",
        "already claimed by another user",
    ],
)
def test_detect_snipe_positive(comment_body: str):
    timeline_nodes = [{"__typename": "IssueComment", "body": comment_body}]
    assert detect_snipe(timeline_nodes) is True, f"Should detect snipe for: {comment_body!r}"


def test_detect_snipe_negative():
    timeline_nodes = [{"__typename": "IssueComment", "body": "Still open, working on a fix."}]
    assert detect_snipe(timeline_nodes) is False


def test_detect_snipe_empty_comments():
    assert detect_snipe([]) is False
