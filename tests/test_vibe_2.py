import json
import os
import tempfile

import pytest

from github_bounty_scraper.vibe import iter_raw_candidates


@pytest.mark.asyncio
async def test_iter_raw_candidates():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
        f.write(json.dumps({"url": "http://1"}) + "\n")
        f.write(json.dumps({"url": "http://2"}) + "\n")
        f.close()
        try:
            items = []
            async for item in iter_raw_candidates(f.name):
                items.append(item)
            assert len(items) == 2
            assert items[0]["url"] == "http://1"
        finally:
            os.remove(f.name)
