"""
Bounty amount extraction and snipe detection.

``extract_bounty_amount`` parses freeform issue text for explicit
USD/token amounts using regex heuristics. ``detect_snipe`` checks
whether a comment timeline shows that a funded bounty has already
been claimed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import STABLECOIN_SYMBOLS

# ─── Result container ────────────────────────────────────────────────

@dataclass
class BountyResult:
    """Parsed bounty amount with currency metadata."""

    numeric_amount: float = 0.0
    raw_display: str = "Unknown Amount"
    currency_symbol: str = "USD"
    all_matches: list[tuple[float, str, str]] = field(default_factory=list)
    # Each match: (value, raw_text, currency)


# ─── Bounty keywords for proximity scoring ──────────────────────────
_BOUNTY_PROXIMITY_KEYWORDS = re.compile(
    r"\b(bounty|reward|pays?|paid|compensation|payout|budget|funded|"
    r"prize|incentive|grant|allocation)\b",
    re.IGNORECASE,
)

# Generic bounty value: bounty: 500, reward: 1000
_BOUNTY_VALUE_RE = re.compile(
    r"\b(?:bounty|reward|price|pays?|paid):\s?\$?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)

# ─── Currency patterns ──────────────────────────────────────────────
# Dollar amounts: $1,000  $500.50  $10,000.00
_DOLLAR_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)

# Crypto/Fiat amounts: 1000 USDC, 0.5 ETH, 10,000 USD
_CRYPTO_SUFFIXES = (
    "USDC", "USDT", "ETH", "SOL", "OP", "ARB", "MATIC",
    "ROXN", "XDC", "DAI", "WETH", "STRK", "BUSD", "USD",
)
_CRYPTO_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*"
    r"(" + "|".join(re.escape(s) for s in _CRYPTO_SUFFIXES) + r")\b",
    re.IGNORECASE,
)

# Crypto keywords for the "Unknown / Custom Tokens" fallback.
_CRYPTO_KEYWORD_SET = {s.upper() for s in _CRYPTO_SUFFIXES if s != "USD"}
_CRYPTO_KEYWORD_RE = re.compile(
    r'\b(' + '|'.join(re.escape(s) for s in _CRYPTO_KEYWORD_SET) + r')\b',
    re.IGNORECASE,
)


def _parse_number(s: str) -> float:
    """Parse a numeric string that may contain thousand separators."""
    return float(s.replace(",", ""))


def _proximity_score(text: str, match_start: int, window: int = 300) -> float:
    """Return a 0–1 score based on how close *match_start* is to a bounty keyword."""
    region = text[max(0, match_start - window):match_start + window]
    hits = _BOUNTY_PROXIMITY_KEYWORDS.findall(region)
    if not hits:
        return 0.0
    return min(len(hits) / 3.0, 1.0)  # cap at 1.0


def extract_bounty_amount(
    text: str,
    max_sane: float = 1e7,
    proximity_window: int = 300,
) -> BountyResult:
    """Parse ``text`` for an explicit bounty dollar or token amount.

    Scanning order: ``$NNN``, ``NNN USD``, ``NNN USDC/USDT``,
    ``NNN ETH/SOL/BTC`` (converted at approximate market value),
    ``bounty: NNN``.

    Returns
    -------
    BountyResult
        Parsed result object containing numeric amount and display metadata.

    Examples:
        >>> res = extract_bounty_amount("Bounty: $500 for fixing this bug")
        >>> res.numeric_amount
        500.0
        >>> res = extract_bounty_amount("Reward: 1 ETH")
        >>> res.currency_symbol
        'ETH'
        >>> res = extract_bounty_amount("No money here, just a thank you")
        >>> res.numeric_amount
        0.0
    """
    result = BountyResult()
    seen: set[str] = set()
    candidates: list[tuple[float, str, str, float]] = []  # (val, raw, currency, prox)

    # ── Dollar matches ──
    for m in _DOLLAR_RE.finditer(text):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        try:
            val = _parse_number(m.group(1))
        except ValueError:
            continue
        if val <= 0 or val > max_sane:
            continue
        prox = _proximity_score(text, m.start(), proximity_window)
        # Title-region bonus: first 200 chars are usually the issue title.
        if m.start() < 200 and prox == 0.0:
            prox = 0.5  # Conservative bonus — title amounts are high signal.
        candidates.append((val, raw.strip(), "USD", prox))

    # ── Crypto matches ──
    for m in _CRYPTO_RE.finditer(text):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        try:
            val = _parse_number(m.group(1))
        except ValueError:
            continue
        if val <= 0 or val > max_sane:
            continue
        symbol = m.group(2).upper()
        # Normalise stablecoins to USD value.
        currency = "USD" if symbol in STABLECOIN_SYMBOLS or symbol == "USD" else symbol
        prox = _proximity_score(text, m.start(), proximity_window)
        # Title-region bonus: first 200 chars are usually the issue title.
        if m.start() < 200 and prox == 0.0:
            prox = 0.5  # Conservative bonus — title amounts are high signal.
        candidates.append((val, raw.strip(), currency, prox))

    # ── Generic matches ──
    for m in _BOUNTY_VALUE_RE.finditer(text):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        try:
            val = _parse_number(m.group(1))
        except ValueError:
            continue
        if val <= 0 or val > max_sane:
            continue
        prox = _proximity_score(text, m.start(), proximity_window)
        candidates.append((val, raw.strip(), "USD", max(prox, 0.8)))

    if not candidates:
        # Fallback: bounty cue detected but no parseable amount.
        if _CRYPTO_KEYWORD_RE.search(text) or _proximity_score(text, 0, len(text)) > 0:
            result.numeric_amount = -1.0
            result.raw_display = "Unknown / Custom Tokens"
            result.currency_symbol = ""
        return result

    result.all_matches = [(v, r, c) for v, r, c, _ in candidates]

    # Pick best: highest proximity, then highest value as tie-breaker.
    candidates.sort(key=lambda x: (x[3], x[0]), reverse=True)
    best_val, best_raw, best_currency, _ = candidates[0]

    result.numeric_amount = best_val
    result.raw_display = best_raw
    result.currency_symbol = best_currency
    return result


def detect_snipe(timeline_nodes: list[dict]) -> bool:
    """Return ``True`` if the timeline shows the bounty has been claimed.

    Checks for claim-completion language (e.g. "bounty paid", "reward
    sent") in comment bodies.  A ``True`` result hard-disqualifies the
    issue regardless of mode.
    """
    for node in timeline_nodes:
        typename = node.get("__typename", "")
        if typename in ("CrossReferencedEvent", "ConnectedEvent"):
            source = node.get("source")
            if (
                source
                and source.get("state") == "OPEN"
                and source.get("isDraft") is False
                and node.get("willCloseTarget") is True
            ):
                return True
        elif typename == "IssueComment":
            body = node.get("body", "").lower()
            if any(s in body for s in ("bounty paid", "reward sent", "bounty claimed")):
                return True
    return False
