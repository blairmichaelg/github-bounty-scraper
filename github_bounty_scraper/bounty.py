"""
Bounty amount extraction and currency normalisation.

Replaces the single regex from the original scraper with a multi-pattern
approach that handles thousand separators, crypto denominations, and
proximity-based prioritisation.
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

# ─── Currency patterns ──────────────────────────────────────────────
# Dollar amounts: $1,000  $500.50  $10,000.00
_DOLLAR_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)

# Crypto amounts: 1000 USDC, 0.5 ETH, 10,000 DAI
_CRYPTO_SUFFIXES = (
    "USDC", "USDT", "ETH", "SOL", "OP", "ARB", "MATIC",
    "ROXN", "XDC", "DAI", "WETH", "STRK", "BUSD",
)
_CRYPTO_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*"
    r"(" + "|".join(re.escape(s) for s in _CRYPTO_SUFFIXES) + r")\b",
    re.IGNORECASE,
)

# Crypto keywords for the "Unknown / Custom Tokens" fallback.
_CRYPTO_KEYWORD_SET = {s.upper() for s in _CRYPTO_SUFFIXES}


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
) -> BountyResult:
    """Extract the most relevant bounty amount from free-form text.

    Strategy
    --------
    1.  Find all dollar and crypto-denominated amounts.
    2.  Deduplicate identical matches.
    3.  Discard amounts above ``max_sane``.
    4.  Prefer amounts near bounty keywords (proximity scoring).
    5.  Among top-proximity matches, pick the maximum value.
    6.  If no numeric match but a crypto keyword is present, return
        ``(-1.0, "Unknown / Custom Tokens")``.
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
        prox = _proximity_score(text, m.start())
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
        currency = "USD" if symbol in STABLECOIN_SYMBOLS else symbol
        prox = _proximity_score(text, m.start())
        candidates.append((val, raw.strip(), currency, prox))

    if not candidates:
        # Fallback: crypto keyword detected but no parseable amount.
        text_upper = text.upper()
        if any(kw in text_upper for kw in _CRYPTO_KEYWORD_SET):
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
