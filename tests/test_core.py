from __future__ import annotations

import asyncio

import pytest

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.core import (
    _assemble_lead_result,
    _build_text_context,
    _check_repo_health,
    _resolve_numeric_amount,
    process_issue,
    run_pipeline,
)
from github_bounty_scraper.graphql import TokenBucket
from github_bounty_scraper.signals import SignalResult

CFG = ScraperConfig(min_stars=10)


# === Section 1: _check_repo_health ===
class TestCheckRepoHealth:
    def test_low_stars_rejected(self):
        repo = {"stargazerCount": 5}
        assert _check_repo_health(repo, CFG) is False

    def test_archived_repo_rejected(self):
        repo = {"stargazerCount": 100, "isArchived": True}
        assert _check_repo_health(repo, CFG) is False

    def test_repo_is_fork_rejected(self):
        repo = {"stargazerCount": 100, "isFork": True}
        assert _check_repo_health(repo, CFG) is False

    def test_user_repo_with_low_mentions_rejected(self):
        repo = {"stargazerCount": 100, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 1}}
        assert _check_repo_health(repo, CFG) is False

    def test_organization_repo_accepted(self):
        repo = {"stargazerCount": 100, "owner": {"__typename": "Organization"}}
        assert _check_repo_health(repo, CFG) is True

    def test_user_repo_with_high_mentions_accepted(self):
        repo = {"stargazerCount": 100, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 10}}
        assert _check_repo_health(repo, CFG) is True


# === Section 2: _build_text_context ===
def test_build_text_context_concatenation():
    issue = {"title": "Slow Query", "body": "Optimization needed.", "labels": {"nodes": [{"name": "urgent"}]}}
    comments = [{"body": "I see it too."}, {"body": "Fixed in #1."}]
    context = _build_text_context(issue, comments)
    assert "slow query" in context.lower()
    assert "optimization needed" in context.lower()
    assert "urgent" in context.lower()
    assert "i see it too" in context.lower()
    assert "fixed in #1" in context.lower()


# === Section 3: _resolve_numeric_amount ===
class TestResolveNumericAmount:
    def test_dollar_amount_extracted(self):
        issue = {"title": "$500 bounty", "body": "Fix this."}
        amount, disp, cur = _resolve_numeric_amount(issue, CFG)
        assert amount == pytest.approx(500.0)
        assert disp == "$500"
        assert cur == "USD"

    def test_no_amount_no_cue_returns_zero(self):
        issue = {"title": "Bug report", "body": "It crashes."}
        amount, _, _ = _resolve_numeric_amount(issue, CFG)
        assert amount == 0.0

    def test_no_amount_with_cue_returns_negative_one(self):
        issue = {"title": "Bounty for fix", "body": "It crashes."}
        amount, _, _ = _resolve_numeric_amount(issue, CFG)
        assert amount == -1.0

    def test_amount_in_labels_extracted(self):
        issue = {"title": "Fix", "body": "Help", "labels": {"nodes": [{"name": "bounty: $200"}]}}
        amount, _, _ = _resolve_numeric_amount(issue, CFG)
        assert amount == 200.0


# === Section 4: _assemble_lead_result ===
def test_assemble_lead_result_mapping():
    issue = {
        "title": "Bounty 1",
        "html_url": "https://github.com/a/b/issues/1",
        "labels": {"nodes": [{"name": "urgent"}]},
    }
    soft = SignalResult(has_onchain_escrow=True, mentions_wallet_payout=True)
    res = _assemble_lead_result(issue, 500.0, "$500", "USD", 85.0, 0.0, "a/b", soft)

    assert res["Title"] == "Bounty 1"
    assert res["Amount"] == "$500"
    assert res["AmountNum"] == 500.0
    assert res["Score"] == 85.0
    assert res["HasOnchainEscrow"] is True
    assert res["Labels"] == "[urgent]"


# === Section 5: Integration / process_issue ===
@pytest.mark.asyncio
async def test_process_issue_disqualified_repo():
    config = ScraperConfig(min_stars=100)
    issue = {"repository": {"stargazerCount": 10}, "html_url": "http://test"}
    # Should return None if repo health check fails
    res = await process_issue(None, TokenBucket(1, 1), issue, None, asyncio.Semaphore(1), config, {}, None, set())
    assert res is None


@pytest.mark.asyncio
async def test_run_pipeline_empty():
    config = ScraperConfig()

    # Mocking discover_issues_stream to return nothing
    async def mock_discover(*args, **kwargs):
        if False:
            yield {}

    import github_bounty_scraper.core as core

    original = core.discover_issues_stream
    core.discover_issues_stream = mock_discover
    try:
        await run_pipeline(config)
    finally:
        core.discover_issues_stream = original
