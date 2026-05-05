"""LLM-based vibe-check pipeline using the Gemini generative API.

Reads candidates from exploration_raw.jsonl, scores each with
Gemini 2.5 Flash, and persists results to the SQLite DB via
db.set_issue_vibe(). Supports concurrency limiting, retry on
rate-limit errors, and both 'unscored' and 'all' scoring modes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, AsyncIterator, Literal

import aiohttp

from .db import set_issue_vibe
from .log import get_logger

log = get_logger()

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

SYSTEM_PROMPT = (
    "You are a ruthless, highly cynical Web3 security auditor. "
    "Your task is to analyze a GitHub Issue and determine if it represents a "
    "legitimate, paid bounty for a human developer. "
    "SCORE 90-100: Explicit mentions of money/crypto. "
    "SCORE 50-89: Ambiguous requests. "
    "SCORE 0-49: Automated bot trash, Renovate, or boilerplate. "
    "Base your judgement only on the provided text. Do not assume there is a "
    "bounty unless money, crypto, or a clear reward is explicitly mentioned or "
    "strongly implied. "
    "Output EXACTLY two lines. No markdown. No conversational filler. "
    "Format: 'SCORE: [0-100]' then 'REASON: [One brutally honest, cynical sentence explaining why.]'"
)

_VIBE_SEM: asyncio.Semaphore | None = None

def _get_sem() -> asyncio.Semaphore:
    global _VIBE_SEM
    if _VIBE_SEM is None:
        _VIBE_SEM = asyncio.Semaphore(5)
    return _VIBE_SEM

def _gemini_endpoint(model: str) -> str:
    return f"{_GEMINI_BASE}/{model}:generateContent"

async def iter_raw_candidates(raw_file: str) -> AsyncIterator[dict[str, Any]]:
    """
    Async generator over exploration_raw.jsonl.

    Each line is expected to be a JSON object with at least:
      - issue_url (str)
      - title (str)
      - body_snippet (str) or body (str)
    """
    if not os.path.exists(raw_file):
        log.warning("Raw file %s does not exist; nothing to vibe-check.", raw_file)
        return

    def _read_lines(path: str) -> list[str]:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().splitlines()

    lines = await asyncio.to_thread(_read_lines, raw_file)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Skipping malformed JSON line in %s", raw_file)
            continue
        yield obj

async def call_gemini(
    session: aiohttp.ClientSession,
    api_key: str,
    title: str,
    body_snippet: str,
    model: str = "gemini-2.5-flash",
) -> tuple[int, str]:
    """Call Gemini to get a vibe score and reason.

    Args:
        session: Active aiohttp client session.
        api_key: Gemini API key.
        title: Issue title.
        body_snippet: Snippet of the issue body.
        model: Model identifier (e.g., 'gemini-2.5-flash').

    Returns:
        A tuple of (score, reason) where score is [0, 100].

    Raises:
        RuntimeError: If API key is missing.
        aiohttp.ClientResponseError: On persistent API errors.
    """
    if not api_key:
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set in environment")

    # User content to provide to the model
    user_text = f"TITLE: {title}\n\nBODY:\n{body_snippet}"

    params = {"key": api_key}
    payload: dict[str, Any] = {
        "systemInstruction": {
            "role": "system",
            "parts": [{"text": SYSTEM_PROMPT}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_text}],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "topP": 0.95,
            "topK": 40,
            "maxOutputTokens": 256,
        },
    }

    data: dict[str, Any] = {}
    for attempt in range(3):
        try:
            async with session.post(
                _gemini_endpoint(model), params=params, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
            break
        except aiohttp.ClientResponseError as exc:
            if exc.status in (429, 503) and attempt < 2:
                wait = 5 * (2 ** attempt)
                log.warning("Gemini %s on attempt %d; retrying in %ds", exc.status, attempt + 1, wait)
                await asyncio.sleep(wait)
            else:
                raise

    # Gemini's typical structure: candidates[0].content.parts[*].text
    try:
        candidates = data["candidates"]
        content = candidates[0]["content"]
        parts = content.get("parts") or []
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        raw_text = "\n".join(text_parts).strip()
    except Exception:
        # Fallback if structure is unexpected
        raw_text = json.dumps(data)

    score, reason = parse_vibe_output(raw_text)
    return score, reason

def parse_vibe_output(raw_text: str) -> tuple[int, str]:
    """Parse SCORE and REASON from model output with regex fallbacks.

    Args:
        raw_text: Raw text output from the LLM.

    Returns:
        A tuple of (score, reason).
    """
    text = raw_text.strip()
    # Remove obvious markdown artifacts if they appear
    text = re.sub(r"^```.*?```$", "", text, flags=re.S)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    score_line = next((ln for ln in lines if ln.upper().startswith("SCORE:")), None)
    reason_line = next((ln for ln in lines if ln.upper().startswith("REASON:")), None)

    score = 0
    if score_line:
        m = re.search(r"(\d+)", score_line)
        if m:
            try:
                score = int(m.group(1))
            except ValueError:
                score = 0

    # Clamp to [0, 100]
    score = max(0, min(score, 100))

    if reason_line:
        reason = re.sub(r"^REASON:\s*", "", reason_line, flags=re.I).strip()
    else:
        reason_candidates = [ln for ln in lines if not ln.upper().startswith("SCORE:")]
        reason = " ".join(reason_candidates) if reason_candidates else "No reason provided."

    if not reason:
        reason = "No reason provided."

    return score, reason

async def run_vibe_check(
    raw_file: str,
    db_path: str,
    limit: int,
    mode: Literal["unscored", "all"],
    concurrency: int = 5,
    model: str = "gemini-2.5-flash",
) -> None:
    """Iterate exploration_raw.jsonl and score candidates with Gemini.

    Args:
        raw_file: Path to exploration_raw.jsonl.
        db_path: Path to the SQLite database.
        limit: Max number of candidates to score.
        mode: 'unscored' (skip already scored) or 'all' (force re-score).
        concurrency: Max concurrent API calls.
        model: Gemini model identifier.

    Side Effects:
        Updates 'issue_stats' table in the database with vibe scores.
    """
    api_key = os.environ.get(GEMINI_API_KEY_ENV, "")
    if not api_key:
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set; cannot run vibe-check.")

    from .db import init_db
    import aiosqlite

    scored_urls: set[str] = set()
    if mode == "unscored" and os.path.exists(db_path):
        async with aiosqlite.connect(db_path) as conn:
            await init_db(conn)
            async with conn.execute(
                "SELECT issue_url FROM issue_stats WHERE vibe_score IS NOT NULL"
            ) as cursor:
                async for row in cursor:
                    scored_urls.add(row[0])

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=35)
    ) as session:
        count = 0
        
        async def _guarded_vibe(obj: dict) -> tuple[int, str, str]:
            issue_url = obj.get("issue_url") or obj.get("url") or ""
            title = obj.get("title", "").strip()
            body_snippet = str(obj.get("body_snippet") or obj.get("body") or "")[:500]
            
            async with _get_sem():
                s, r = await call_gemini(session, api_key, title, body_snippet, model)
                return s, r, issue_url

        async for obj in iter_raw_candidates(raw_file):
            if limit and count >= limit:
                break

            issue_url = obj.get("issue_url") or obj.get("url") or ""
            if not issue_url or (mode == "unscored" and issue_url in scored_urls):
                continue

            try:
                score, reason, url = await _guarded_vibe(obj)
                await asyncio.sleep(0.1)
            except Exception as exc:
                log.warning("Gemini call failed for %s: %s", issue_url, exc)
                continue

            checked_at = time.time()
            try:
                await set_issue_vibe(
                    db_path=db_path,
                    issue_url=url,
                    vibe_score=score,
                    vibe_reason=reason,
                    checked_at=checked_at,
                )
            except Exception as db_exc:
                log.warning("Failed to persist vibe score for %s: %s", url, db_exc)
                continue

            count += 1
            log.info("VIBE %3d for %s — %s", score, url, reason)

    log.info("Vibe-check complete. Scored %d candidates from %s", count, raw_file)
