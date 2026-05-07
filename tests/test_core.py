from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


# === Section 1: _check_repo_health ===
class TestCheckRepoHealth:
    def test_low_stars_rejected(self, cfg):
        cfg.min_stars = 10
        repo = {"stargazerCount": 5}
        assert _check_repo_health(repo, cfg) is False

    def test_archived_repo_rejected(self, cfg):
        cfg.min_stars = 10
        repo = {"stargazerCount": 100, "isArchived": True}
        assert _check_repo_health(repo, cfg) is False

    def test_repo_is_fork_rejected(self, cfg):
        cfg.min_stars = 10
        repo = {"stargazerCount": 100, "isFork": True}
        assert _check_repo_health(repo, cfg) is False

    def test_user_repo_with_low_mentions_rejected(self, cfg):
        cfg.min_stars = 10
        repo = {"stargazerCount": 100, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 1}}
        assert _check_repo_health(repo, cfg) is False

    def test_organization_repo_accepted(self, cfg):
        cfg.min_stars = 10
        repo = {"stargazerCount": 100, "owner": {"__typename": "Organization"}}
        assert _check_repo_health(repo, cfg) is True

    def test_user_repo_with_high_mentions_accepted(self, cfg):
        cfg.min_stars = 10
        repo = {"stargazerCount": 100, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 10}}
        assert _check_repo_health(repo, cfg) is True


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
    def test_dollar_amount_extracted(self, cfg):
        issue = {"title": "$500 bounty", "body": "Fix this."}
        amount, disp, cur = _resolve_numeric_amount(issue, cfg)
        assert amount == pytest.approx(500.0)
        assert disp == "$500"
        assert cur == "USD"

    def test_no_amount_no_cue_returns_zero(self, cfg):
        issue = {"title": "Bug report", "body": "It crashes."}
        amount, _, _ = _resolve_numeric_amount(issue, cfg)
        assert amount == 0.0

    def test_no_amount_with_cue_returns_negative_one(self, cfg):
        issue = {"title": "Bounty for fix", "body": "It crashes."}
        amount, _, _ = _resolve_numeric_amount(issue, cfg)
        assert amount == -1.0

    def test_amount_in_labels_extracted(self, cfg):
        issue = {"title": "Fix", "body": "Help", "labels": {"nodes": [{"name": "bounty: $200"}]}}
        amount, _, _ = _resolve_numeric_amount(issue, cfg)
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
async def test_process_issue_disqualified_repo(cfg, mock_token_bucket):
    cfg.min_stars = 100
    issue = {"repository": {"stargazerCount": 10}, "html_url": "http://test"}
    # Should return None if repo health check fails
    res = await process_issue(None, mock_token_bucket, issue, None, asyncio.Semaphore(1), cfg, {}, None, set())
    assert res is None


@pytest.mark.asyncio
async def test_run_pipeline_empty(cfg):

    # Mocking discover_issues_stream to return nothing
    async def mock_discover(*args, **kwargs):
        if False:
            yield {}

    import github_bounty_scraper.core as core

    original = core.discover_issues_stream
    core.discover_issues_stream = mock_discover
    try:
        await run_pipeline(cfg)
    finally:
        core.discover_issues_stream = original


# === Section 5: process_issue() integration (fully mocked external I/O) ===

class TestProcessIssueIntegration:
    """Test the full process_issue() pipeline with all external I/O mocked out."""

    @pytest.mark.asyncio
    async def test_healthy_issue_returns_lead_result(self, cfg, minimal_issue, mock_db_conn):
        """A valid issue with a bounty amount should produce a LeadResult."""
        from github_bounty_scraper.core import process_issue
        from github_bounty_scraper.graphql import TokenBucket
        import aiohttp

        seen_aggregators: set[str] = set()
        bucket = TokenBucket(capacity=100, fill_rate=10.0)
        sem = asyncio.Semaphore(1)
        committer = MagicMock()
        committer.tick = AsyncMock()
        committer.flush = AsyncMock()

        # signals needs to be a dict as per core.py signature
        import re
        signals = {
            "aggregator_repos": [],
            "positive_escrow_re": re.compile(r"escrow|immunefi"),
            "kill_labels": [],
            "active_label_signals_re": re.compile(r"active"),
            "assigned_signal_re": re.compile(r"assigned"),
            "stale_signal_re": re.compile(r"stale")
        }

        with patch("github_bounty_scraper.core.run_graphql_audit") as mock_gql, \
             patch("github_bounty_scraper.core._append_raw") as mock_append:

            # GraphQL enrichment returns minimal valid data
            mock_gql.return_value = {
                "repository": minimal_issue["repository"]
            }
            # Add issue to repository
            mock_gql.return_value["repository"]["issue"] = minimal_issue
            
            mock_append.return_value = None

            async with aiohttp.ClientSession() as session:
                result = await process_issue(
                    session=session,
                    bucket=bucket,
                    issue_item=minimal_issue,
                    db_conn=mock_db_conn,
                    sem=sem,
                    config=cfg,
                    signals=signals,
                    committer=committer,
                    seen_aggregators=seen_aggregators,
                )

        assert result is not None
        assert result["AmountNum"] == 500.0

    @pytest.mark.asyncio
    async def test_archived_repo_returns_none(self, cfg, minimal_issue, mock_db_conn):
        """An issue from an archived repo should be filtered out (return None)."""
        from github_bounty_scraper.core import process_issue
        from github_bounty_scraper.graphql import TokenBucket
        import aiohttp

        archived_issue = dict(minimal_issue)
        archived_issue["repository"] = dict(minimal_issue["repository"])
        archived_issue["repository"]["isArchived"] = True

        bucket = TokenBucket(capacity=100, fill_rate=10.0)
        sem = asyncio.Semaphore(1)
        committer = MagicMock()
        committer.tick = AsyncMock()
        committer.flush = AsyncMock()
        seen_aggregators: set[str] = set()
        signals = {"aggregator_repos": []}

        with patch("github_bounty_scraper.core.run_graphql_audit") as mock_gql:
            mock_gql.return_value = {
                "repository": archived_issue["repository"]
            }
            mock_gql.return_value["repository"]["issue"] = archived_issue

            async with aiohttp.ClientSession() as session:
                result = await process_issue(
                    session=session,
                    bucket=bucket,
                    issue_item=archived_issue,
                    db_conn=mock_db_conn,
                    sem=sem,
                    config=cfg,
                    signals=signals,
                    committer=committer,
                    seen_aggregators=seen_aggregators,
                )

        assert result is None

    @pytest.mark.asyncio
    async def test_zero_amount_issue_returns_none_or_low_score(self, cfg, mock_db_conn):
        """An issue with no detectable bounty amount should be filtered or score near 0."""
        from github_bounty_scraper.core import process_issue
        from github_bounty_scraper.graphql import TokenBucket
        import aiohttp

        no_money_issue = {
            "html_url": "https://github.com/test/repo/issues/99",
            "number": 99,
            "title": "Documentation update needed",
            "body": "Please update the README.",
            "state": "OPEN",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "comments": {"totalCount": 0, "nodes": []},
            "labels": {"nodes": []},
            "repository": {
                "nameWithOwner": "test/repo",
                "url": "https://github.com/test/repo",
                "stargazerCount": 10,
                "forkCount": 1,
                "isArchived": False,
                "isDisabled": False,
                "isFork": False,
                "primaryLanguage": {"name": "Python"},
                "owner": {"__typename": "Organization"},
                "mentionableUsers": {"totalCount": 10},
            }
        }

        bucket = TokenBucket(capacity=100, fill_rate=10.0)
        sem = asyncio.Semaphore(1)
        committer = MagicMock()
        committer.tick = AsyncMock()
        committer.flush = AsyncMock()
        seen_aggregators: set[str] = set()
        signals = {"aggregator_repos": []}

        with patch("github_bounty_scraper.core.run_graphql_audit") as mock_gql:
            mock_gql.return_value = {
                "repository": no_money_issue["repository"]
            }
            mock_gql.return_value["repository"]["issue"] = no_money_issue

            async with aiohttp.ClientSession() as session:
                result = await process_issue(
                    session=session,
                    bucket=bucket,
                    issue_item=no_money_issue,
                    db_conn=mock_db_conn,
                    sem=sem,
                    config=cfg,
                    signals=signals,
                    committer=committer,
                    seen_aggregators=seen_aggregators,
                )

        if result is not None:
            assert result["Score"] < 20

    @pytest.mark.asyncio
    async def test_graphql_error_does_not_crash(self, cfg, minimal_issue, mock_db_conn):
        """If GraphQL raises an exception, process_issue should return None gracefully."""
        from github_bounty_scraper.core import process_issue
        from github_bounty_scraper.graphql import TokenBucket
        import aiohttp

        bucket = TokenBucket(capacity=100, fill_rate=10.0)
        sem = asyncio.Semaphore(1)
        committer = MagicMock()
        committer.tick = AsyncMock()
        committer.flush = AsyncMock()
        seen_aggregators: set[str] = set()
        signals = {"aggregator_repos": []}

        with patch("github_bounty_scraper.core.run_graphql_audit", side_effect=Exception("Network error")):
            async with aiohttp.ClientSession() as session:
                result = await process_issue(
                    session=session,
                    bucket=bucket,
                    issue_item=minimal_issue,
                    db_conn=mock_db_conn,
                    sem=sem,
                    config=cfg,
                    signals=signals,
                    committer=committer,
                    seen_aggregators=seen_aggregators,
                )

        assert result is None
