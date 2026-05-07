import pytest

from github_bounty_scraper.vibe import call_gemini


@pytest.mark.asyncio
async def test_call_gemini_missing_key():
    with pytest.raises(RuntimeError):
        await call_gemini(None, "", "title", "body")


@pytest.mark.asyncio
async def test_call_gemini_success():
    class MockSession:
        def post(self, *args, **kwargs):
            class MockResponse:
                status = 200

                async def json(self):
                    return {"candidates": [{"content": {"parts": [{"text": "SCORE: 90\nREASON: Good"}]}}]}

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            return MockResponse()

    score, reason = await call_gemini(MockSession(), "key", "title", "body")
    assert score == 90
