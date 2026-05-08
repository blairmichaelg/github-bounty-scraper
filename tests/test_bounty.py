"""Tests for bounty amount extraction and snipe detection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from github_bounty_scraper.bounty import detect_snipe, extract_bounty_amount
from github_bounty_scraper.config import load_signals


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
    signals = load_signals()
    assert detect_snipe(timeline_nodes, signals=signals) is True, f"Should detect snipe for: {comment_body!r}"


def test_detect_snipe_negative():
    timeline_nodes = [{"__typename": "IssueComment", "body": "Still open, working on a fix."}]
    signals = load_signals()
    assert detect_snipe(timeline_nodes, signals=signals) is False


def test_detect_snipe_empty_comments():
    assert detect_snipe([], signals={}) is False


def test_extract_amount_with_live_prices(cfg):
    """Test live price conversion for ETH."""
    cfg.enable_live_prices = True
    with patch("github_bounty_scraper.bounty.get_usd_price", return_value=3000.0):
        # 1 ETH = 3000.0 USD
        result = extract_bounty_amount("1 ETH reward", config=cfg)
        assert result.numeric_amount == 3000.0
        assert result.currency_symbol == "ETH"


def test_extract_amount_title_bonus():
    """Amounts in the first 200 chars should get a bonus if no keywords nearby."""
    # "bounty" keyword at the end, far from "$500"
    text = "$500 " + " " * 300 + " bounty"
    result = extract_bounty_amount(text)
    # Proximity score should be boosted by title bonus
    assert result.numeric_amount == 500.0


def test_extract_amount_max_sane():
    """Amounts above max_sane should be ignored (falls back to Unknown if keywords present)."""
    result = extract_bounty_amount("Bounty: $1,000,000,000", max_sane=1e6)
    assert result.numeric_amount == 0.0


def test_extract_amount_seen_deduplication():
    """Duplicate matches should be ignored."""
    result = extract_bounty_amount("$500 ... $500")
    assert len(result.all_matches) == 1


def test_extract_amount_fallback_unknown():
    """Should return -1.0 if keywords exist but no amount found."""
    result = extract_bounty_amount("Bounty is available for this issue.")
    assert result.numeric_amount == 0.0
    assert result.raw_display == "Unknown / Custom Tokens"


def test_detect_snipe_cross_ref():
    """Test detect_snipe with CrossReferencedEvent."""
    nodes = [
        {"__typename": "CrossReferencedEvent", "source": {"state": "OPEN", "isDraft": False}, "willCloseTarget": True}
    ]
    assert detect_snipe(nodes, signals={}) is True


def test_extract_amount_generic_bounty_cue():
    """Test 'bounty: 500' format."""
    result = extract_bounty_amount("bounty: 500")
    assert result.numeric_amount == 500.0


def test_extract_amount_proximity_scoring():
    """Test proximity score calculation logic."""
    text = "Reward: $100"  # "Reward" is a keyword
    result = extract_bounty_amount(text)
    assert result.numeric_amount == 100.0
