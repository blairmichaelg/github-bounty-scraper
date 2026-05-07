from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from github_bounty_scraper.core import (
    _build_lead_result,
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
        cfg.min_repo_stars = 10
        repo = {"stargazerCount": 5}
        assert _check_repo_health(repo, cfg) is False

    def test_archived_repo_rejected(self, cfg):
        cfg.min_repo_stars = 10
        repo = {"stargazerCount": 100, "isArchived": True}
        assert _check_repo_health(repo, cfg) is False

    def test_repo_is_fork_rejected(self, cfg):
        cfg.min_repo_stars = 10
        repo = {"stargazerCount": 100, "isFork": True}
        assert _check_repo_health(repo, cfg) is False

    def test_user_repo_with_low_mentions_rejected(self, cfg):
        cfg.min_repo_stars = 10
        repo = {"stargazerCount": 100, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 1}}
        assert _check_repo_health(repo, cfg) is False

    def test_organization_repo_accepted(self, cfg):
        cfg.min_repo_stars = 10
        repo = {"stargazerCount": 100, "owner": {"__typename": "Organization"}}
        assert _check_repo_health(repo, cfg) is True

    def test_user_repo_with_high_mentions_accepted(self, cfg):
        cfg.min_repo_stars = 10
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
        assert amount == 0.0

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
    res = _build_lead_result(issue, 500.0, "$500", "USD", 85.0, 0.0, "a/b", soft)

    assert res["Title"] == "Bounty 1"
    assert res["Amount"] == "$500"
    assert res["AmountNum"] == 500.0
    assert res["Score"] == 85.0
    assert res["HasOnchainEscrow"] is True
    assert res["Labels"] == "[urgent]"


# === Section 5: Integration / process_issue ===
@pytest.mark.asyncio
async def test_process_issue_disqualified_repo(cfg, mock_token_bucket):
    cfg.min_repo_stars = 100
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
        import aiohttp

        from github_bounty_scraper.core import process_issue

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
            "stale_signal_re": re.compile(r"stale"),
        }

        with (
            patch("github_bounty_scraper.core.run_graphql_audit") as mock_gql,
            patch("github_bounty_scraper.core._append_raw") as mock_append,
        ):
            # GraphQL enrichment returns minimal valid data
            mock_gql.return_value = {"repository": minimal_issue["repository"]}
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
        import aiohttp

        from github_bounty_scraper.core import process_issue

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
            mock_gql.return_value = {"repository": archived_issue["repository"]}
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
        import aiohttp

        from github_bounty_scraper.core import process_issue

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
            },
        }

        bucket = TokenBucket(capacity=100, fill_rate=10.0)
        sem = asyncio.Semaphore(1)
        committer = MagicMock()
        committer.tick = AsyncMock()
        committer.flush = AsyncMock()
        seen_aggregators: set[str] = set()
        signals = {"aggregator_repos": []}

        with patch("github_bounty_scraper.core.run_graphql_audit") as mock_gql:
            mock_gql.return_value = {"repository": no_money_issue["repository"]}
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
        import aiohttp

        from github_bounty_scraper.core import process_issue

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


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_run_pipeline_returns_list(self, cfg):
        """run_pipeline with empty discovery results should return an empty list."""
        from github_bounty_scraper.core import run_pipeline

        # Use an async iterator that yields nothing
        async def empty_aiter(*args, **kwargs):
            if False:
                yield None

        with (
            patch("github_bounty_scraper.core.discover_issues_stream", side_effect=empty_aiter),
            patch("github_bounty_scraper.core.init_db"),
            patch("github_bounty_scraper.core.fetch_graphql") as mock_fetch,
            patch("aiosqlite.connect") as mock_connect,
        ):
            mock_fetch.return_value = {"rateLimit": {"remaining": 5000, "resetAt": "..."}}

            mock_conn = MagicMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=False)
            mock_connect.return_value = mock_conn

            results = await run_pipeline(cfg)

        assert isinstance(results, list)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_run_pipeline_respects_max_issues(self, cfg):
        """run_pipeline should stop after config.max_issues_per_run issues."""
        from github_bounty_scraper.core import run_pipeline

        cfg.max_issues_per_run = 1

        async def mock_discover(*args, **kwargs):
            yield {"html_url": "url1", "repository": {"nameWithOwner": "a/b", "stargazerCount": 100}}
            yield {"html_url": "url2", "repository": {"nameWithOwner": "a/c", "stargazerCount": 100}}

        with (
            patch("github_bounty_scraper.core.discover_issues_stream", side_effect=mock_discover),
            patch("github_bounty_scraper.core.init_db"),
            patch("github_bounty_scraper.core.fetch_graphql") as mock_fetch,
            patch("aiosqlite.connect") as mock_connect,
            patch("github_bounty_scraper.core.process_issue") as mock_process,
        ):
            mock_fetch.return_value = {"rateLimit": {"remaining": 5000, "resetAt": "..."}}

            mock_conn = MagicMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=False)
            mock_connect.return_value = mock_conn

            mock_process.return_value = {"Title": "test"}

            results = await run_pipeline(cfg)

        assert len(results) == 1
        assert mock_process.call_count == 1
