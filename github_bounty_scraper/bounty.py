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

from .config import ScraperConfig
from .price_cache import get_usd_price

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

_BOUNTY_VALUE_RE = re.compile(
    r"\b(?:bounty|reward|price|pays?|paid):\s?\$?([\d,]+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# ─── Currency patterns ──────────────────────────────────────────────
# Dollar amounts: $1,000  $500.50  $10,000.00
_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")

# Generic fallback regexes if signals are missing
_DEFAULT_CRYPTO_SUFFIXES = ("USDC", "ETH", "SOL", "DAI", "USD")
_DEFAULT_CRYPTO_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(" + "|".join(re.escape(s) for s in _DEFAULT_CRYPTO_SUFFIXES) + r")\b",
    re.IGNORECASE,
)
_DEFAULT_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _DEFAULT_CRYPTO_SUFFIXES if s != "USD") + r")\b",
    re.IGNORECASE,
)


def _parse_number(s: str) -> float:
    """Parse a numeric string that may contain thousand separators."""
    return float(s.replace(",", ""))


def _proximity_score(text: str, match_start: int, window: int = 300) -> float:
    """Return a 0–1 score based on how close *match_start* is to a bounty keyword."""
    region = text[max(0, match_start - window) : match_start + window]
    hits = _BOUNTY_PROXIMITY_KEYWORDS.findall(region)
    if not hits:
        return 0.0
    return min(len(hits) / 3.0, 1.0)  # cap at 1.0


def parse_numeric_amount(
    text: str | None,
    max_sane: float = 1e7,
    proximity_window: int = 300,
    config: ScraperConfig | None = None,
    signals: dict[str, Any] | None = None,
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
        >>> res = extract_bounty_amount("Bounty available, amount TBD")
        >>> res.numeric_amount
        0.0
    """
    if text is None:
        return BountyResult(0.0, "None", "USD")
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

    # ── K-multiplier matches ──
    for m in re.finditer(r"(?:^|\s|\$)\s*([\d,]+(?:\.\d+)?)\s*k\b", text, re.IGNORECASE):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        try:
            val = _parse_number(m.group(1)) * 1000.0
        except ValueError:
            continue
        if val <= 0 or val > max_sane:
            continue
        prox = _proximity_score(text, m.start(), proximity_window)
        if m.start() < 200 and prox == 0.0:
            prox = 0.5
        candidates.append((val, raw.strip(), "USD", prox))

    # ── Crypto matches ──
    crypto_re = (signals or {}).get("crypto_amounts_re") or _DEFAULT_CRYPTO_RE
    stablecoins = set((signals or {}).get("stablecoin_symbols") or ["USDC", "DAI"])

    for m in crypto_re.finditer(text):
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
        if symbol in stablecoins or symbol == "USD":
            currency = "USD"
            norm_val = val
        else:
            currency = symbol
            norm_val = val
            if config and config.enable_live_prices:
                price = get_usd_price(symbol)
                if price > 0:
                    norm_val = val * price

        prox = _proximity_score(text, m.start(), proximity_window)
        # Title-region bonus: first 200 chars are usually the issue title.
        if m.start() < 200 and prox == 0.0:
            prox = 0.5  # Conservative bonus — title amounts are high signal.
        candidates.append((norm_val, raw.strip(), currency, prox))

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
        keyword_re = (signals or {}).get("crypto_keywords_re") or _DEFAULT_KEYWORD_RE
        if keyword_re.search(text) or _proximity_score(text, 0, len(text)) > 0:
            result.numeric_amount = 0.0
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


def extract_bounty_amount(
    text: str | None,
    max_sane: float = 1e7,
    proximity_window: int = 300,
    config: ScraperConfig | None = None,
    signals: dict[str, Any] | None = None,
) -> BountyResult:
    """Backward-compatible alias for ``parse_numeric_amount``."""
    return parse_numeric_amount(text, max_sane=max_sane, proximity_window=proximity_window, config=config, signals=signals)


_SNIPE_PHRASES: frozenset[str] = frozenset(
    {
        # Original 3
        "bounty paid",
        "reward sent",
        "bounty claimed",
        # Payment completion
        "payout complete",
        "payment sent",
        "payment complete",
        "payment processed",
        "reward delivered",
        "funds sent",
        "funds transferred",
        "tokens sent",
        "tokens transferred",
        # Claim/assignment
        "i am working on this",
        "i'm working on this",
        "taking this",
        "claiming this",
        "assigned to me",
        "already claimed",
        "pr submitted",
        "pr merged",
        "fix merged",
        "patch merged",
        # Resolution
        "marked as resolved",
        "marked resolved",
        "closing as completed",
        "closing as fixed",
        "this has been fixed",
        "this is fixed",
        "resolved in",
        "fixed in",
    }
)


def detect_snipe(issue: dict | list[dict], timeline_nodes: list[dict] | None = None) -> bool:
    """Return ``True`` if the timeline shows the bounty has been claimed.

    Checks for claim-completion language (e.g. "bounty paid", "reward
    sent") in comment bodies.  A ``True`` result hard-disqualifies the
    issue regardless of mode.
    """
    if timeline_nodes is None:
        timeline_nodes = issue if isinstance(issue, list) else []
        issue_obj: dict = {}
    else:
        issue_obj = issue if isinstance(issue, dict) else {}

    if str(issue_obj.get("state", "")).lower() == "closed":
        return True

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
            if any(s in body for s in _SNIPE_PHRASES):
                return True
    return False
