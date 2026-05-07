import pytest

from github_bounty_scraper.graphql import TokenBucket, fetch_graphql


@pytest.mark.asyncio
async def test_token_bucket():
    bucket = TokenBucket(10, 1.0)
    # Mocking wait since we don't want to actually sleep
    bucket.tokens = 5
    await bucket.consume(1)
    assert bucket.tokens <= 4.1  # account for a tiny bit of fill during the lock


@pytest.mark.asyncio
async def test_fetch_graphql_failure():
    # Mock session to fail
    class MockSession:
        def post(self, *args, **kwargs):
            class MockResponse:
                status = 500
                ok = False

                async def text(self):
                    return "Error"

                async def json(self):
                    return {"errors": ["Error"]}

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            return MockResponse()

    bucket = TokenBucket(10, 1.0)
    res = await fetch_graphql(MockSession(), bucket, "token", "query")
    assert res is None
