from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, AsyncIterator

import aiohttp

from .db import set_issue_vibe
from .log import get_logger

log = get_logger()

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
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

    loop = asyncio.get_running_loop()

    # Read the file in a thread to avoid blocking the event loop on disk IO.
    def _read_lines() -> list[str]:
        with open(raw_file, "r", encoding="utf-8") as fh:
            return fh.readlines()

    lines = await loop.run_in_executor(None, _read_lines)
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
) -> tuple[int, str]:
    """
    Call Gemini 1.5 Flash to get a vibe score and reason.

    Returns (score, reason). On any parsing error, returns (0, fallback_reason).
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
            "maxOutputTokens": 128,
        },
    }

    async with session.post(GEMINI_ENDPOINT, params=params, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        data = await resp.json()

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
    """
    Parse SCORE and REASON from model output with regex fallbacks.

    The model *should* output exactly:
      SCORE: N
      REASON: ...

    But we defensively:
      - strip markdown
      - ignore extra whitespace
      - search for lines starting with SCORE/REASON
      - clamp score to [0, 100]
    """
    text = raw_text.strip()
    # Remove obvious markdown artifacts if they appear
    # (e.g., ``` or leading bullets).
    text = re.sub(r"^```.*?```$", "", text, flags=re.S)

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
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    if reason_line:
        reason = re.sub(r"^REASON:\s*", "", reason_line, flags=re.I).strip()
    else:
        # Fallback: try to use any remaining non-score text as reason
        # (e.g., line 2 if line 1 was SCORE).
        reason_candidates = [ln for ln in lines if not ln.upper().startswith("SCORE:")]
        reason = " ".join(reason_candidates) if reason_candidates else "No reason provided."

    if not reason:
        reason = "No reason provided."

    return score, reason

async def run_vibe_check(
    raw_file: str,
    db_path: str,
    limit: int,
    mode: str,
    concurrency: int = 5,
) -> None:
    """
    Iterate exploration_raw.jsonl and score candidates with Gemini.

    mode:
      - "unscored": only issues without a vibe_score in issue_stats.
      - "all": score everything in the raw file up to limit.
    """
    api_key = os.environ.get(GEMINI_API_KEY_ENV, "")
    if not api_key:
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set; cannot run vibe-check.")

    from .db import init_db  # avoid circulars at import time
    import aiosqlite

    # Optional pre-load of already scored issue_urls when mode == "unscored"
    scored_urls: set[str] = set()
    if mode == "unscored" and os.path.exists(db_path):
        async with aiosqlite.connect(db_path) as conn:
            await init_db(conn)
            async with conn.execute(
                "SELECT issue_url FROM issue_stats WHERE vibe_score IS NOT NULL"
            ) as cursor:
                async for row in cursor:
                    scored_urls.add(row[0])

    vibe_sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        count = 0
        async for obj in iter_raw_candidates(raw_file):
            if limit and count >= limit:
                break

            issue_url = obj.get("issue_url") or obj.get("url") or ""
            if not issue_url:
                log.debug("Skipping raw candidate without issue_url.")
                continue

            if mode == "unscored" and issue_url in scored_urls:
                continue

            title = obj.get("title", "").strip()
            body_snippet = (
                obj.get("body_snippet")
                or obj.get("body")
                or ""
            )
            body_snippet = str(body_snippet)[:500]

            try:
                async with vibe_sem:
                    score, reason = await call_gemini(session, api_key, title, body_snippet)
                    await asyncio.sleep(0.1)
            except Exception as exc:
                log.warning("Gemini call failed for %s: %s", issue_url, exc)
                continue

            checked_at = time.time()
            try:
                await set_issue_vibe(
                    db_path=db_path,
                    issue_url=issue_url,
                    vibe_score=score,
                    vibe_reason=reason,
                    checked_at=checked_at,
                )
            except Exception as db_exc:
                log.warning("Failed to persist vibe score for %s: %s", issue_url, db_exc)
                continue

            count += 1
            log.info(
                "VIBE %3d for %s — %s",
                score,
                issue_url,
                reason,
            )

    log.info("Vibe-check complete. Scored %d candidates from %s", count, raw_file)
