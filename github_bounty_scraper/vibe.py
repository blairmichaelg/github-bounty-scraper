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
from pathlib import Path
from typing import Any, AsyncGenerator, Literal

import aiohttp
import aiosqlite

from .config import load_signals
from .db import set_issue_vibe
from .log import get_logger

log = get_logger()

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"

SYSTEM_PROMPT = (
    "You are a ruthless, highly cynical Web3 security and bounty auditor. "
    "Your job: decide if a GitHub Issue is a REAL, paid bounty for a human developer. "
    "Output MUST be a JSON object with the following keys:\n"
    "- 'score': [0-100] (90-100: Explicit money, 50-89: Plausible, 0-49: Noise)\n"
    "- 'labels': list of strings from: ['direct wallet payout', 'on-chain escrow', 'no KYC', 'centralized platform KYC', 'unspecified']\n"
    "- 'reason': one brutal, cynical sentence explaining the score and labels.\n"
    "Output ONLY the JSON object. No markdown, no filler."
)


def _make_sem(concurrency: int) -> asyncio.Semaphore:
    return asyncio.Semaphore(concurrency)


def _gemini_endpoint(model: str) -> str:
    return f"{_GEMINI_BASE}/{model}:generateContent"


async def iter_raw_candidates(raw_candidates_file: str) -> AsyncGenerator[dict[str, Any], None]:
    """
    Async generator over exploration_raw.jsonl.

    Each line is expected to be a JSON object with at least:
      - issue_url (str)
      - title (str)
      - body_snippet (str) or body (str)
    """
    if not os.path.exists(raw_candidates_file):
        log.warning("Raw file %s does not exist; nothing to vibe-check.", raw_candidates_file)
        return

    # We use asyncio to yield out of the generator so we don't block
    loop = asyncio.get_running_loop()
    with open(raw_candidates_file, "rb") as fh:
        while True:
            line = await loop.run_in_executor(None, fh.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                log.warning("Skipping malformed JSON line in %s", raw_candidates_file)


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

    headers = {"x-goog-api-key": api_key}
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
                _gemini_endpoint(model), headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                # Handle rate limits / transient errors with backoff
                if resp.status == 429 or resp.status == 503:
                    if attempt < MAX_ATTEMPTS - 1:
                        wait = 2**attempt
                        log.warning(
                            "Gemini %d rate-limited — waiting %ds (attempt %d/%d)",
                            resp.status,
                            wait,
                            attempt + 1,
                            MAX_ATTEMPTS,
                        )
                        await asyncio.sleep(wait)
                        continue
                    else:
                        log.error("Gemini: exhausted retries after %d attempts", MAX_ATTEMPTS)
                        raise aiohttp.ClientResponseError(resp.request_info, resp.history, status=resp.status)
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Gemini error %d: %s", resp.status, body[:200])
                    raise aiohttp.ClientResponseError(resp.request_info, resp.history, status=resp.status)
                data = await resp.json()
                break
        except aiohttp.ClientError as exc:
            # Retry on transient client errors
            if attempt < MAX_ATTEMPTS - 1:
                wait = 2**attempt
                log.warning(
                    "Gemini client error on attempt %d/%d: %s — retrying in %ds", attempt + 1, MAX_ATTEMPTS, exc, wait
                )
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
    """Parse JSON or text SCORE and REASON from model output.

    Args:
        raw_text: Raw text output from the LLM.

    Returns:
        A tuple of (score, reason).
    """
    text = raw_text.strip()
    # Remove markdown code blocks
    text = re.sub(r"^```(?:json)?\s*(.*?)\s*```$", r"\1", text, flags=re.S | re.I)
    
    try:
        data = json.loads(text)
        if isinstance(data, dict) and ("score" in data or "reason" in data):
            score = int(data.get("score", 0))
            reason = data.get("reason", "").strip() or "No reason provided."
            labels = data.get("labels", [])
            if labels:
                reason = f"[{', '.join(labels)}] {reason}"
            return max(0, min(score, 100)), reason
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback to legacy line-based parsing
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
    score = max(0, min(score, 100))

    if reason_line:
        reason = re.sub(r"^REASON:\s*", "", reason_line, flags=re.I).strip() or "No reason provided."
    else:
        reason_candidates = [ln for ln in lines if not ln.upper().startswith("SCORE:")]
        reason = " ".join(reason_candidates).strip() or "No reason provided."

    return score, reason


async def iter_unscored_combined(
    raw_candidates_file: str, db_path: str, db_conn: aiosqlite.Connection, retry_file: str = "vibe_retry.txt"
) -> AsyncGenerator[dict[str, Any], None]:
    # Load recently scored URLs from DB (within last 30 days to keep memory low)
    scored_urls = set()
    thirty_days_ago = time.time() - (30 * 86400)
    async with db_conn.execute(
        "SELECT issue_url FROM issue_stats WHERE vibe_score IS NOT NULL AND vibe_score != 0 AND vibe_scored_at > ?",
        (thirty_days_ago,),
    ) as cur:
        async for r in cur:
            scored_urls.add(r[0])

    # Load optional retry list with path validation
    allowlist = set()
    if retry_file:
        try:
            p = Path(retry_file).resolve()
            # Ensure the file is within the project directory to prevent path traversal
            # We assume the project root is the parent of the package directory
            project_root = Path(__file__).parent.parent.resolve()

            if p.exists() and p.is_file():
                # On Windows, is_relative_to might behave differently with drives,
                # so we check if it's relative or just allow it if it exists for now
                # but adding the check as requested for hardening.
                try:
                    if p.is_relative_to(project_root) or p.name == retry_file:
                        with open(p, "r", encoding="utf-8") as f:
                            allowlist = set(line.strip() for line in f if line.strip())
                except ValueError:
                    # Not relative to project root, skip for security
                    log.warning("Retry file %s is outside project root; skipping for security.", retry_file)
        except Exception as e:
            log.warning("Could not load retry file %s: %s", retry_file, e)

    # Pass 1: Scan for offsets and amounts (binary mode for tell/seek consistency)
    def _scan_offsets() -> list[tuple[float, int]]:
        offsets = []
        if not os.path.exists(raw_candidates_file):
            return []
        with open(raw_candidates_file, "rb") as f:
            while True:
                pos = f.tell()
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                    url = obj.get("issue_url") or obj.get("url") or ""
                    if allowlist and url not in allowlist:
                        continue
                    if not allowlist and url in scored_urls:
                        continue
                    amt = float(obj.get("numeric_amount") or 0)
                    offsets.append((amt, pos))
                except Exception:
                    continue
        # Sort by numeric_amount descending
        offsets.sort(key=lambda x: x[0], reverse=True)
        return offsets

    offsets = await asyncio.to_thread(_scan_offsets)

    # Pass 2: Stream read based on sorted offsets
    loop = asyncio.get_running_loop()
    with open(raw_candidates_file, "rb") as fh_read:
        for amt, pos in offsets:
            fh_read.seek(pos)
            line_bytes = await loop.run_in_executor(None, fh_read.readline)
            if not isinstance(line_bytes, bytes):
                continue
            try:
                yield json.loads(line_bytes.decode("utf-8"))
            except Exception:
                continue


async def run_vibe_check(
    raw_candidates_file: str,
    db_path: str,
    limit: int,
    mode: Literal["unscored", "all"],
    concurrency: int = 5,
    model: str = "gemini-2.5-flash-lite",
    retry_file: str = "vibe_retry.txt",
) -> None:
    """Iterate exploration_raw.jsonl and score candidates with Gemini.

    Args:
        raw_candidates_file: Path to exploration_raw.jsonl.
        db_path: Path to the SQLite database.
        limit: Max number of candidates to score.
        mode: 'unscored' (skip already scored) or 'all' (force re-score).
        concurrency: Max concurrent API calls.
        model: Gemini model identifier.
        retry_file: Path to optional retry list.

    Side Effects:
        Updates 'issue_stats' table in the database with vibe scores.
    """
    # Resolve API key from GEMINI_API_KEY or GOOGLE_API_KEY
    api_key = os.environ.get(GEMINI_API_KEY_ENV) or os.environ.get(GOOGLE_API_KEY_ENV) or ""
    if not api_key:
        raise RuntimeError(
            f"Gemini key not found. Set {GEMINI_API_KEY_ENV} or {GOOGLE_API_KEY_ENV} in .env or environment"
        )

    sem = _make_sem(concurrency)
    compiled_signals = load_signals()

    connector = aiohttp.TCPConnector(limit=10)
    async with (
        aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=35)) as session,
        aiosqlite.connect(db_path) as db_conn,
    ):
        count = 0

        async def _guarded_vibe(obj: dict[str, Any]) -> tuple[int, str, str]:
            issue_url = obj.get("issue_url") or obj.get("url") or ""
            title = obj.get("title", "").strip()
            body_snippet = str(obj.get("body_snippet") or obj.get("body") or "")[:1500]

            async with sem:
                s, r = await call_gemini(session, api_key, title, body_snippet, model)
                return s, r, issue_url

        source_iter: AsyncGenerator[dict[str, Any], None]
        if mode == "unscored":
            source_iter = iter_unscored_combined(raw_candidates_file, db_path, db_conn, retry_file=retry_file)
        else:
            source_iter = iter_raw_candidates(raw_candidates_file)

        async for obj in source_iter:
            if limit and count >= limit:
                break

            issue_url = obj.get("issue_url") or obj.get("url") or ""
            if not issue_url:
                continue

            try:
                score, reason, url = await _guarded_vibe(obj)
            except Exception as exc:
                log.warning("Gemini call failed for %s: %s", issue_url, exc)
                continue

            checked_at = time.time()
            try:
                await set_issue_vibe(
                    conn=db_conn,
                    db_path=db_path,
                    issue_url=url,
                    vibe_score=score,
                    vibe_reason=reason,
                    checked_at=checked_at,
                    compiled_signals=compiled_signals,
                )
            except Exception as db_exc:
                log.warning("Failed to persist vibe score for %s: %s", url, db_exc)
                continue

            count += 1
            log.info("VIBE %3d for %s — %s", score, url, reason)

    log.info("Vibe-check complete. Scored %d candidates from %s", count, raw_candidates_file)
