import pytest

from github_bounty_scraper.graphql import TokenBucket, run_graphql_audit


@pytest.mark.asyncio
async def test_run_graphql_audit():
    class MockSession:
        def post(self, *args, **kwargs):
            class MockResponse:
                status = 200
                ok = True

                async def json(self):
                    return {
                        "data": {
                            "repository": {
                                "issue": {"id": "1", "timelineItems": {"nodes": [], "pageInfo": {}}},
                                "pullRequests": {"nodes": [], "pageInfo": {}},
                            }
                        }
                    }

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            return MockResponse()

    bucket = TokenBucket(10, 1.0)
    res = await run_graphql_audit(MockSession(), bucket, "token", "owner", "repo", 1)
    assert res["repository"]["issue"]["id"] == "1"
