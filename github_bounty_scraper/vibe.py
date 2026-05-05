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
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"

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
    model: str = "gemini-2.5-flash-lite",
) -> tuple[int, str]:
    """Call Gemini to get a vibe score and reason.

    Args:
        session: Active aiohttp client session.
        api_key: Gemini API key.
        title: Issue title.
        body_snippet: Snippet of the issue body.
        model: Model identifier (e.g., 'gemini-2.5-flash-lite').

    Returns:
        A tuple of (score, reason) where score is [0, 100].

    Raises:
        RuntimeError: If API key is missing.
        aiohttp.ClientResponseError: On persistent API errors.
    """
    if not api_key:
        raise RuntimeError(f"Gemini API key not found. Set {GEMINI_API_KEY_ENV} or {GOOGLE_API_KEY_ENV} in environment")

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
    MAX_ATTEMPTS = 5
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.post(
                _gemini_endpoint(model), params=params, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                # Handle rate limits / transient errors with backoff
                if resp.status == 429 or resp.status == 503:
                    if attempt < MAX_ATTEMPTS - 1:
                        wait = 2 ** attempt
                        log.warning(
                            "Gemini %d rate-limited — waiting %ds (attempt %d/%d)",
                            resp.status, wait, attempt + 1, MAX_ATTEMPTS,
                        )
                        await asyncio.sleep(wait)
                        continue
                    else:
                        log.error("Gemini: exhausted retries after %d attempts", MAX_ATTEMPTS)
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Gemini error %d: %s", resp.status, body[:200])
                    raise aiohttp.ClientResponseError(resp.request_info, resp.history, status=resp.status)
                data = await resp.json()
                break
        except aiohttp.ClientError as exc:
            # Retry on transient client errors
            if attempt < MAX_ATTEMPTS - 1:
                wait = 2 ** attempt
                log.warning("Gemini client error on attempt %d/%d: %s — retrying in %ds", attempt + 1, MAX_ATTEMPTS, exc, wait)
                await asyncio.sleep(wait)
                continue
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
    model: str = "gemini-2.5-flash-lite",
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
    # Resolve API key from GEMINI_API_KEY or GOOGLE_API_KEY
    api_key = os.environ.get(GEMINI_API_KEY_ENV) or os.environ.get(GOOGLE_API_KEY_ENV) or ""
    if not api_key:
        raise RuntimeError(f"Gemini key not found. Set {GEMINI_API_KEY_ENV} or {GOOGLE_API_KEY_ENV} in .env or environment")

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

        # If mode == 'unscored', prefer iterating DB rows with NULL vibe_score
        async def iter_unscored_db(db_path: str) -> AsyncIterator[dict[str, Any]]:
            import aiosqlite as _aiosqlite
            # Load bodies from exploration_raw.jsonl
            bodies = {}
            if os.path.exists(raw_file):
                def _read():
                    with open(raw_file, "r", encoding="utf-8") as f:
                        return f.read().splitlines()
                lines = await asyncio.to_thread(_read)
                for line in lines:
                    if not line.strip(): continue
                    try:
                        obj = json.loads(line)
                        bodies[obj.get("url", "")] = obj.get("body_snippet", "")
                    except: pass

            async with _aiosqlite.connect(db_path) as _conn:
                await init_db(_conn)
                async with _conn.execute(
                    "SELECT issue_url, title FROM issue_stats WHERE vibe_score IS NULL ORDER BY score DESC"
                ) as cur:
                    async for r in cur:
                        yield {"issue_url": r[0], "title": r[1] or "", "body_snippet": bodies.get(r[0], "")}

        async def _guarded_vibe(obj: dict) -> tuple[int, str, str]:
            issue_url = obj.get("issue_url") or obj.get("url") or ""
            title = obj.get("title", "").strip()
            body_snippet = str(obj.get("body_snippet") or obj.get("body") or "")[:500]
            
            async with _get_sem():
                s, r = await call_gemini(session, api_key, title, body_snippet, model)
                return s, r, issue_url

        # Choose source iterator
        source_iter: AsyncIterator[dict[str, Any]]
        if mode == "unscored" and os.path.exists(db_path):
            source_iter = iter_unscored_db(db_path)
        else:
            source_iter = iter_raw_candidates(raw_file)

        async for obj in source_iter:
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




