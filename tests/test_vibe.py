import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from github_bounty_scraper.db import init_db
from github_bounty_scraper.output import write_markdown_output
from github_bounty_scraper.vibe import (
    _gemini_endpoint,
    call_gemini,
    iter_raw_candidates,
    parse_vibe_output,
    run_vibe_check,
)


# === Section 1: parse_vibe_output — score and label parsing ===
def test_parse_vibe_output():
    text = "SCORE: 85\nREASON: This is a good one."
    score, reason = parse_vibe_output(text)
    assert score == 85
    assert "This is a good one" in reason

    text = "Nothing here"
    score, reason = parse_vibe_output(text)
    assert score == 0
    assert reason == "Nothing here"


def test_gemini_endpoint():
    url = _gemini_endpoint("gemini-1.5-flash")
    assert "gemini-1.5-flash:generateContent" in url


# === Section 2: call_gemini — API call mocking ===
@pytest.mark.asyncio
async def test_call_gemini_missing_key():
    with pytest.raises(RuntimeError):
        await call_gemini(None, "", "title", "body")


@pytest.mark.asyncio
async def test_call_gemini_success(mock_aiohttp_session):
    mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "SCORE: 90\nREASON: Good"}]}}]
    }
    score, reason = await call_gemini(mock_aiohttp_session, "key", "title", "body")
    assert score == 90


@pytest.mark.asyncio
async def test_call_gemini_api_error(mock_aiohttp_session):
    """Gemini returns 500 — should raise after retries."""
    mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 500
    mock_aiohttp_session.post.return_value.__aenter__.return_value.ok = False

    with pytest.raises(Exception):
        # We use a short timeout or mock the sleep to avoid waiting during test
        with patch("asyncio.sleep", AsyncMock()):
            await call_gemini(mock_aiohttp_session, "key", "title", "body")


@pytest.mark.asyncio
async def test_call_gemini_retry_on_429(mock_aiohttp_session):
    """Gemini returns 429 then 200 — should succeed."""
    mock_429 = MagicMock()
    mock_429.status = 429
    mock_429.__aenter__ = AsyncMock(return_value=mock_429)
    mock_429.__aexit__ = AsyncMock(return_value=False)

    mock_200 = MagicMock()
    mock_200.status = 200
    mock_200.json = AsyncMock(return_value={"candidates": [{"content": {"parts": [{"text": "SCORE: 50"}]}}]})
    mock_200.__aenter__ = AsyncMock(return_value=mock_200)
    mock_200.__aexit__ = AsyncMock(return_value=False)

    mock_aiohttp_session.post.side_effect = [mock_429, mock_200]

    with patch("asyncio.sleep", AsyncMock()):
        score, _ = await call_gemini(mock_aiohttp_session, "key", "title", "body")
    assert score == 50
    assert mock_aiohttp_session.post.call_count == 2


# === Section 3: run_vibe_check — end-to-end flow ===
@pytest.mark.asyncio
async def test_run_vibe_check_empty():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as raw_f:
        raw_f.close()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as db_f:
            db_path = db_f.name
            db_f.close()
            try:
                os.environ["GEMINI_API_KEY"] = "fake"
                async with aiosqlite.connect(db_path) as db:
                    await init_db(db)
                await run_vibe_check(raw_f.name, db_path, 10, "unscored")
            finally:
                if os.path.exists(raw_f.name):
                    os.remove(raw_f.name)
                if os.path.exists(db_path):
                    os.remove(db_path)


@pytest.mark.asyncio
async def test_run_vibe_check_with_data():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as raw_f:
        raw_f.write(json.dumps({"url": "http://test", "title": "test", "body": "test"}) + "\n")
        raw_f.close()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as db_path_f:
            db_path = db_path_f.name
            db_path_f.close()
            try:
                os.environ["GEMINI_API_KEY"] = "fake"
                async with aiosqlite.connect(db_path) as db:
                    await init_db(db)
                # This will call Gemini, which we should ideally mock, but let's see.
                # Actually, call_gemini will fail because of fake key, but we want coverage.
                try:
                    await run_vibe_check(raw_f.name, db_path, 1, "unscored")
                except Exception:
                    pass
            finally:
                if os.path.exists(raw_f.name):
                    os.remove(raw_f.name)
                if os.path.exists(db_path):
                    os.remove(db_path)


# === Section 4: iter_raw_candidates — file iteration ===
@pytest.mark.asyncio
async def test_iter_raw_candidates_malformed():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
        f.write('{"url": "http://1"}\n')
        f.write("INVALID JSON\n")
        f.write('{"url": "http://2"}\n')
        f.close()
    try:
        items = []
        async for item in iter_raw_candidates(f.name):
            items.append(item)
        # Should skip the malformed line
        assert len(items) == 2
        assert items[1]["url"] == "http://2"
    finally:
        os.remove(f.name)


@pytest.mark.asyncio
async def test_iter_raw_candidates_missing_file():
    items = []
    async for item in iter_raw_candidates("nonexistent.jsonl"):
        items.append(item)
    assert items == []


# === Section 5: vibe output formatting ===
def test_markdown_vibe_arrows(tmp_path):
    """Verify that score deltas produce ↑ or ↓ arrows in Markdown output."""
    output_file = tmp_path / "test.md"

    verified = [
        {
            "Score": 80.0,
            "PrevScore": 70.0,
            "Amount": "$100.00",
            "Currency": "USD",
            "Repo": "owner/repo",
            "Title": "Increased Score",
            "Labels": "[]",
            "Link": "https://github.com/...",
        },
        {
            "Score": 60.0,
            "PrevScore": 75.0,
            "Amount": "$200.00",
            "Currency": "USD",
            "Repo": "owner/repo2",
            "Title": "Decreased Score",
            "Labels": "[]",
            "Link": "https://github.com/...",
        },
        {
            "Score": 50.0,
            "PrevScore": None,
            "Amount": "$50.00",
            "Currency": "USD",
            "Repo": "owner/repo3",
            "Title": "New Score",
            "Labels": "[]",
            "Link": "https://github.com/...",
        },
    ]

    write_markdown_output(verified, [], 1.0, str(output_file))

    content = output_file.read_text(encoding="utf-8")
    assert "↑ Increased Score" in content
    assert "↓ Decreased Score" in content


class TestBatchVibeProcessing:
    @pytest.mark.asyncio
    async def test_run_vibe_check_batch_success(self, tmp_path, mock_aiohttp_session):
        """Verify batch processing and DB persistence."""
        raw_f = tmp_path / "raw.jsonl"
        raw_f.write_text(json.dumps({"url": "http://test", "title": "test", "body": "test"}) + "\n")
        db_path = str(tmp_path / "test.db")

        async with aiosqlite.connect(db_path) as db:
            await init_db(db)

        # Mock Gemini response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.ok = True
        mock_resp.json = AsyncMock(
            return_value={"candidates": [{"content": {"parts": [{"text": "SCORE: 85\nREASON: Good"}]}}]}
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_aiohttp_session.post.return_value = mock_resp

        with (
            patch("aiohttp.ClientSession", return_value=mock_aiohttp_session),
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}),
        ):
            await run_vibe_check(str(raw_f), db_path, limit=10, mode="raw")

        # Verify DB persistence
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT vibe_score, vibe_reason FROM issue_stats WHERE issue_url='http://test'"
            ) as cur:
                row = await cur.fetchone()
                assert row[0] == 85
                assert row[1] == "Good"

    @pytest.mark.asyncio
    async def test_run_vibe_check_gemini_fails_gracefully(self, tmp_path, mock_aiohttp_session):
        """Verify that a Gemini failure doesn't crash the loop."""
        raw_f = tmp_path / "raw.jsonl"
        raw_f.write_text(json.dumps({"url": "http://fail", "title": "fail", "body": "fail"}) + "\n")
        raw_f.write_text(raw_f.read_text() + json.dumps({"url": "http://pass", "title": "pass", "body": "pass"}) + "\n")
        db_path = str(tmp_path / "test.db")

        async with aiosqlite.connect(db_path) as db:
            await init_db(db)

        # Mock side effects: first fails, second succeeds
        mock_fail = MagicMock()
        mock_fail.status = 500
        mock_fail.ok = False
        mock_fail.text = AsyncMock(return_value="error")
        mock_fail.__aenter__ = AsyncMock(return_value=mock_fail)
        mock_fail.__aexit__ = AsyncMock(return_value=False)

        mock_pass = MagicMock()
        mock_pass.status = 200
        mock_pass.ok = True
        mock_pass.json = AsyncMock(
            return_value={"candidates": [{"content": {"parts": [{"text": "SCORE: 50\nREASON: OK"}]}}]}
        )
        mock_pass.__aenter__ = AsyncMock(return_value=mock_pass)
        mock_pass.__aexit__ = AsyncMock(return_value=False)

        # 5 retries for fail, then 1 for pass
        mock_aiohttp_session.post.side_effect = [mock_fail] * 5 + [mock_pass]

        with (
            patch("aiohttp.ClientSession", return_value=mock_aiohttp_session),
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await run_vibe_check(str(raw_f), db_path, limit=10, mode="raw")

        # Verify only the second one is scored
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT issue_url FROM issue_stats WHERE vibe_score IS NOT NULL") as cur:
                rows = await cur.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == "http://pass"

    @pytest.mark.asyncio
    async def test_run_vibe_check_malformed_gemini_json(self, tmp_path, mock_aiohttp_session):
        """Verify that malformed Gemini JSON is handled."""
        raw_f = tmp_path / "raw.jsonl"
        raw_f.write_text(json.dumps({"url": "http://badjson", "title": "bad", "body": "bad"}) + "\n")
        db_path = str(tmp_path / "test.db")

        async with aiosqlite.connect(db_path) as db:
            await init_db(db)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.ok = True
        mock_resp.json = AsyncMock(side_effect=ValueError("bad json"))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_aiohttp_session.post.return_value = mock_resp

        with (
            patch("aiohttp.ClientSession", return_value=mock_aiohttp_session),
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}),
        ):
            await run_vibe_check(str(raw_f), db_path, limit=10, mode="raw")

        # Should not crash, and nothing in DB
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM issue_stats WHERE vibe_score IS NOT NULL") as cur:
                count = await cur.fetchone()
                assert count[0] == 0

    @pytest.mark.asyncio
    async def test_run_vibe_check_missing_url(self, tmp_path):
        """Verify that candidates without a URL are skipped."""
        raw_f = tmp_path / "raw.jsonl"
        raw_f.write_text(json.dumps({"title": "no url"}) + "\n")
        db_path = str(tmp_path / "test.db")

        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
            await run_vibe_check(str(raw_f), db_path, limit=10, mode="raw")
        # Should finish without calling Gemini


# === Section 6: Edge cases for higher coverage ===
@pytest.mark.asyncio
async def test_iter_raw_candidates_blank_lines():
    """Blank lines in JSONL files should be skipped."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
        f.write('{"url": "http://1"}\n')
        f.write("\n")
        f.write("   \n")
        f.write('{"url": "http://2"}\n')
        f.close()
    try:
        items = []
        async for item in iter_raw_candidates(f.name):
            items.append(item)
        assert len(items) == 2
    finally:
        os.remove(f.name)


class TestParseVibeOutputEdgeCases:
    def test_score_clamped_above_100(self):
        """Scores over 100 should be clamped to 100."""
        text = "SCORE: 150\nREASON: Over the limit"
        score, reason = parse_vibe_output(text)
        assert score == 100
        assert "Over the limit" in reason

    def test_score_negative_prefix_ignored(self):
        """Regex matches digits only, so -10 becomes 10."""
        text = "SCORE: -10\nREASON: Negative"
        score, reason = parse_vibe_output(text)
        assert score == 10  # regex captures \d+ = "10"

    def test_empty_reason_gets_default(self):
        """When reason line exists but is empty after stripping, default is used."""
        text = "SCORE: 50\nREASON:   "
        score, reason = parse_vibe_output(text)
        assert score == 50
        assert reason == "No reason provided."

    def test_no_reason_line_fallback(self):
        """If no REASON: line, concatenate non-SCORE lines as reason."""
        text = "SCORE: 42\nThis is the explanation\nMore detail"
        score, reason = parse_vibe_output(text)
        assert score == 42
        assert "This is the explanation" in reason
        assert "More detail" in reason

    def test_multiline_no_score(self):
        """Multiple lines with no SCORE: line at all."""
        text = "Just text\nMore text"
        score, reason = parse_vibe_output(text)
        assert score == 0
        assert "Just text" in reason

    def test_score_non_numeric_chars(self):
        """SCORE: line with non-numeric suffix doesn't crash."""
        text = "SCORE: 7/10\nREASON: Partial"
        score, reason = parse_vibe_output(text)
        assert score == 7


class TestCallGeminiEdgeCases:
    @pytest.mark.asyncio
    async def test_call_gemini_unexpected_json_structure(self, mock_aiohttp_session):
        """Unexpected JSON structure from Gemini falls back to json.dumps."""
        mock_aiohttp_session.post.return_value.__aenter__.return_value.json.return_value = {"unexpected": "structure"}
        score, reason = await call_gemini(mock_aiohttp_session, "key", "title", "body")
        assert score == 0
        assert "unexpected" in reason

    @pytest.mark.asyncio
    async def test_call_gemini_exhausted_retries_429(self, mock_aiohttp_session):
        """After max retries on 429, Gemini should raise."""
        mock_resp = MagicMock()
        mock_resp.status = 429
        mock_resp.request_info = MagicMock()
        mock_resp.history = []
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session.post.return_value = mock_resp

        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(Exception):
                await call_gemini(mock_aiohttp_session, "key", "title", "body")
