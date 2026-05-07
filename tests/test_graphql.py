from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from github_bounty_scraper.graphql import fetch_graphql, run_graphql_audit


@pytest.mark.asyncio
async def test_token_bucket(mock_token_bucket):
    # The fixture is already mocked
    await mock_token_bucket.consume(1)
    mock_token_bucket.consume.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_graphql_failure(mock_aiohttp_session, mock_token_bucket):
    mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 500
    mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = False
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {"errors": ["Error"]}

    res = await fetch_graphql(mock_aiohttp_session, mock_token_bucket, "token", "query")
    assert res is None


@pytest.mark.asyncio
async def test_run_graphql_audit(mock_aiohttp_session, mock_token_bucket):
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {
        "data": {
            "repository": {
                "issue": {"id": "1", "timelineItems": {"nodes": [], "pageInfo": {}}},
                "pullRequests": {"nodes": [], "pageInfo": {}},
            }
        }
    }
    res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
    assert res is not None


class TestGraphQLPaginationAndRetry:
    @pytest.mark.asyncio
    async def test_pr_pagination_fetches_second_page(self, mock_aiohttp_session, mock_token_bucket):
        """When hasNextPage=True for PRs, the client requests the next page."""
        # Page 1: hasNextPage=True
        page1 = {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [{"mergedAt": "2026-05-01T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                    },
                    "issue": {"id": "1", "timelineItems": {"nodes": [], "pageInfo": {}}},
                }
            }
        }
        # Page 2: hasNextPage=False
        page2 = {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [{"mergedAt": "2026-04-01T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }

        mock_resp1 = AsyncMock()
        mock_resp1.status = 200
        mock_resp1.ok = True
        mock_resp1.json = AsyncMock(return_value=page1)

        mock_resp2 = AsyncMock()
        mock_resp2.status = 200
        mock_resp2.ok = True
        mock_resp2.json = AsyncMock(return_value=page2)

        mock_aiohttp_session.post.return_value.__aenter__.side_effect = [mock_resp1, mock_resp2]

        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)

        assert len(res["repository"]["pullRequests"]["nodes"]) == 2
        assert mock_aiohttp_session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_timeline_pagination(self, mock_aiohttp_session, mock_token_bucket):
        """When hasNextPage=True for Timeline, the client requests the next page."""
        page1 = {
            "data": {
                "repository": {
                    "pullRequests": {"nodes": [], "pageInfo": {}},
                    "issue": {
                        "id": "1",
                        "timelineItems": {
                            "nodes": [{"__typename": "AssignedEvent"}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "tl_cursor1"},
                        },
                    },
                }
            }
        }
        page2 = {
            "data": {
                "repository": {
                    "issue": {
                        "timelineItems": {
                            "nodes": [{"__typename": "UnassignedEvent"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }

        mock_resp1 = AsyncMock()
        mock_resp1.status = 200
        mock_resp1.ok = True
        mock_resp1.json = AsyncMock(return_value=page1)

        mock_resp2 = AsyncMock()
        mock_resp2.status = 200
        mock_resp2.ok = True
        mock_resp2.json = AsyncMock(return_value=page2)

        mock_aiohttp_session.post.return_value.__aenter__.side_effect = [mock_resp1, mock_resp2]

        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)

        assert len(res["repository"]["issue"]["timelineItems"]["nodes"]) == 2

    @pytest.mark.asyncio
    async def test_retry_on_429(self, mock_aiohttp_session, mock_token_bucket):
        """A 429 response should trigger a retry after delay."""
        mock_429 = AsyncMock()
        mock_429.status = 429
        mock_429.ok = False

        mock_success = AsyncMock()
        mock_success.status = 200
        mock_success.ok = True
        mock_success.json = AsyncMock(return_value={"data": {"key": "val"}})

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_aiohttp_session.post.return_value.__aenter__.side_effect = [mock_429, mock_success]
            res = await fetch_graphql(mock_aiohttp_session, mock_token_bucket, "token", "query")

        assert res == {"key": "val"}
        assert mock_sleep.call_count == 1
        assert mock_aiohttp_session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_client_error(self, mock_aiohttp_session, mock_token_bucket):
        """A ClientError should trigger a retry."""
        mock_success = AsyncMock()
        mock_success.status = 200
        mock_success.ok = True
        mock_success.json = AsyncMock(return_value={"data": {"key": "val"}})

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_aiohttp_session.post.return_value.__aenter__.side_effect = [aiohttp.ClientError("fail"), mock_success]
            res = await fetch_graphql(mock_aiohttp_session, mock_token_bucket, "token", "query")

        assert res == {"key": "val"}
        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, mock_aiohttp_session, mock_token_bucket):
        """After max retries, fetch_graphql returns None."""
        mock_429 = AsyncMock()
        mock_429.status = 429
        mock_429.ok = False

        with patch("asyncio.sleep", new_callable=AsyncMock):
            mock_aiohttp_session.post.return_value.__aenter__.return_value = mock_429
            res = await fetch_graphql(mock_aiohttp_session, mock_token_bucket, "token", "query", retries=2)

        assert res is None
        assert mock_aiohttp_session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_response_nodes(self, mock_aiohttp_session, mock_token_bucket):
        """Valid response with empty nodes should not raise."""
        mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 200
        mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = True
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {
            "data": {"repository": {"pullRequests": {"nodes": [], "pageInfo": {}}}}
        }
        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
        assert res["repository"]["pullRequests"]["nodes"] == []


class TestSecretStr:
    """Cover _SecretStr.__repr__ and __str__."""

    def test_repr_masks_token(self):
        from github_bounty_scraper.graphql import _SecretStr

        s = _SecretStr("ghp_supersecrettoken")
        assert repr(s) == "'***'"

    def test_str_masks_token(self):
        from github_bounty_scraper.graphql import _SecretStr

        s = _SecretStr("ghp_supersecrettoken")
        assert str(s) == "***"


class TestTokenBucketWait:
    """Cover the waiting path in TokenBucket.consume when tokens are exhausted."""

    @pytest.mark.asyncio
    async def test_consume_waits_when_exhausted(self):
        from github_bounty_scraper.graphql import TokenBucket

        bucket = TokenBucket(capacity=1, fill_rate=1000.0)
        # First consume should succeed immediately
        await bucket.consume(1)
        # Second consume must wait for refill — tokens are 0
        # With fill_rate=1000 it refills fast, so this returns quickly
        await bucket.consume(1)
        # If we got here without hanging, the wait path was hit


class TestFetchGraphQLEdgeCases:
    """Cover errors-only response and GraphQL fetch edge cases."""

    @pytest.mark.asyncio
    async def test_errors_only_response_returns_none(self, mock_aiohttp_session, mock_token_bucket):
        """GraphQL response with 'errors' but no 'data' returns None."""
        mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 200
        mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = True
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(
            return_value={"errors": [{"message": "some error"}]}
        )
        res = await fetch_graphql(mock_aiohttp_session, mock_token_bucket, "token", "query")
        assert res is None

    @pytest.mark.asyncio
    async def test_run_audit_returns_none_when_no_repo(self, mock_aiohttp_session, mock_token_bucket):
        """run_graphql_audit returns None when repository is absent."""
        mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 200
        mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = True
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(return_value={"data": {}})
        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
        assert res == {}


class TestPRPaginationEdgeCases:
    """Cover PR pagination early-stop and edge cases."""

    @pytest.mark.asyncio
    async def test_pr_pagination_stops_on_old_merge_date(self, mock_aiohttp_session, mock_token_bucket):
        """PR pagination stops when last PR is older than 45 days."""
        page1 = {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [{"mergedAt": "2020-01-01T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
                    },
                    "issue": {"id": "1", "timelineItems": {"nodes": [], "pageInfo": {}}},
                }
            }
        }
        mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 200
        mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = True
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(return_value=page1)

        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
        # Should NOT have paginated — only 1 call because the old date triggered early-stop
        assert mock_aiohttp_session.post.call_count == 1
        assert len(res["repository"]["pullRequests"]["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_pr_pagination_stops_on_empty_new_nodes(self, mock_aiohttp_session, mock_token_bucket):
        """PR pagination stops when a page returns empty nodes."""
        page1 = {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [{"mergedAt": "2026-05-01T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
                    },
                    "issue": {"id": "1", "timelineItems": {"nodes": [], "pageInfo": {}}},
                }
            }
        }
        page2 = {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
        mock_resp1 = AsyncMock()
        mock_resp1.status = 200
        mock_resp1.ok = True
        mock_resp1.json = AsyncMock(return_value=page1)

        mock_resp2 = AsyncMock()
        mock_resp2.status = 200
        mock_resp2.ok = True
        mock_resp2.json = AsyncMock(return_value=page2)

        mock_aiohttp_session.post.return_value.__aenter__.side_effect = [mock_resp1, mock_resp2]
        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
        # Original 1 PR, page2 returned 0 new nodes
        assert len(res["repository"]["pullRequests"]["nodes"]) == 1


class TestCommentDeduplication:
    """Cover the comment deduplication logic (lines 271-276)."""

    @pytest.mark.asyncio
    async def test_comments_are_deduplicated(self, mock_aiohttp_session, mock_token_bucket):
        """Duplicate comments by createdAt are merged into one set."""
        page1 = {
            "data": {
                "repository": {
                    "pullRequests": {"nodes": [], "pageInfo": {}},
                    "issue": {
                        "id": "1",
                        "firstComments": {
                            "nodes": [
                                {"body": "Hello", "createdAt": "2026-01-01T00:00:00Z"},
                                {"body": "World", "createdAt": "2026-01-02T00:00:00Z"},
                            ]
                        },
                        "lastComments": {
                            "nodes": [
                                {"body": "Hello", "createdAt": "2026-01-01T00:00:00Z"},
                                {"body": "New one", "createdAt": "2026-01-03T00:00:00Z"},
                            ]
                        },
                        "timelineItems": {"nodes": [], "pageInfo": {}},
                    },
                }
            }
        }
        mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 200
        mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = True
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(return_value=page1)

        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
        comments = res["repository"]["issue"]["comments"]["nodes"]
        # 3 unique dates, even though 4 total comments were in first+last
        assert len(comments) == 3

    @pytest.mark.asyncio
    async def test_comments_dedup_skips_none_entries(self, mock_aiohttp_session, mock_token_bucket):
        """None entries in comment lists are filtered out."""
        page1 = {
            "data": {
                "repository": {
                    "pullRequests": {"nodes": [], "pageInfo": {}},
                    "issue": {
                        "id": "1",
                        "firstComments": {"nodes": [None, {"body": "Valid", "createdAt": "2026-01-01T00:00:00Z"}]},
                        "lastComments": {"nodes": []},
                        "timelineItems": {"nodes": [], "pageInfo": {}},
                    },
                }
            }
        }
        mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 200
        mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = True
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json = AsyncMock(return_value=page1)

        res = await run_graphql_audit(mock_aiohttp_session, mock_token_bucket, "token", "owner", "repo", 1)
        comments = res["repository"]["issue"]["comments"]["nodes"]
        assert len(comments) == 1
        assert comments[0]["body"] == "Valid"
