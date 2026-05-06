"""
Output formatters — text (console), markdown, and JSON.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from .config import ScraperConfig
from .log import get_logger

log = get_logger()


def write_output(
    leads: list[dict[str, Any]],
    elapsed: float,
    config: ScraperConfig,
) -> None:
    """Dispatch output based on ``config.output_format``.

    - ``text``:     Console output only.
    - ``markdown``: Console output + ``output.md`` file.
    - ``json``:     ``output.json`` file only (console suppressed).
    """
    verified = [lead for lead in leads if lead["AmountNum"] > 0]
    unknown = [lead for lead in leads if lead["AmountNum"] < 0]

    # Sort: score desc, then payout-preference tie-breakers, then amount.
    verified.sort(
        key=lambda x: (
            x.get("Score", 0),
            int(x.get("HasOnchainEscrow", False)),
            int(x.get("MentionsWalletPayout", False)),
            int(x.get("MentionsNoKyc", False)),
            x["AmountNum"],
        ),
        reverse=True,
    )

    suppress_console = config.output_format == "json"
    write_text_output(verified, unknown, elapsed, suppress_console=suppress_console)

    md_path = (
        f"{config.output_file}.md" if config.output_file else config.output_md_file
    )
    json_path = (
        f"{config.output_file}.json" if config.output_file else config.output_json_file
    )

    if config.output_format == "markdown":
        write_markdown_output(verified, unknown, elapsed, md_path)

    if config.output_format == "json":
        write_json_output(leads, elapsed, json_path)


# ─── Console (text) output ───────────────────────────────────────────
def write_text_output(
    verified: list[dict],
    unknown: list[dict],
    elapsed: float,
    *,
    suppress_console: bool = False,
) -> None:
    if suppress_console:
        return

    print("\n" + "=" * 60)
    print("VERIFIED BOUNTY LEADS (Sorted by Score)")
    print("=" * 60)

    if not verified:
        print("No robust verified leads survived the pipeline filtering.")
    else:
        for lead in verified:
            print(f"Score   : {lead.get('Score', 'N/A')}")
            print(f"Amount  : {lead['Amount']}")
            if lead.get("Currency") and lead["Currency"] != "USD":
                print(f"Currency: {lead['Currency']}")
            print(f"Repo    : {lead['Repo']}")
            safe_title = (
                str(lead["Title"]).encode("ascii", "ignore").decode("ascii")
            )
            print(f"Title   : {safe_title} {lead['Labels']}")
            print(f"Link    : {lead['Link']}")
            # Payout-preference tags
            tags = []
            if lead.get("HasOnchainEscrow"):
                tags.append("ON-CHAIN ESCROW")
            if lead.get("MentionsWalletPayout"):
                tags.append("WALLET PAYOUT")
            if lead.get("MentionsNoKyc"):
                tags.append("NO KYC")
            if tags:
                print(f"Payout  : {' | '.join(tags)}")
            print("-" * 60)

    if unknown:
        print("\n" + "=" * 60)
        print("UNKNOWN / CUSTOM TOKEN LEADS")
        print("=" * 60)
        for lead in unknown:
            print(f"Score   : {lead.get('Score', 'N/A')}")
            print(f"Amount  : {lead['Amount']}")
            print(f"Repo    : {lead['Repo']}")
            safe_title = (
                str(lead["Title"]).encode("ascii", "ignore").decode("ascii")
            )
            print(f"Title   : {safe_title} {lead['Labels']}")
            print(f"Link    : {lead['Link']}")
            tags = []
            if lead.get("HasOnchainEscrow"):
                tags.append("ON-CHAIN ESCROW")
            if lead.get("MentionsWalletPayout"):
                tags.append("WALLET PAYOUT")
            if lead.get("MentionsNoKyc"):
                tags.append("NO KYC")
            if tags:
                print(f"Payout  : {' | '.join(tags)}")
            print("-" * 60)

    print(f"Pipeline executed in {elapsed:.2f} seconds.")


# ─── Markdown output ─────────────────────────────────────────────────
def write_markdown_output(
    verified: list[dict],
    unknown: list[dict],
    elapsed: float,
    path: str,
) -> None:
    now_str = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        + " UTC"
    )
    lines = [
        "# GitHub Bounty Scraper — Results\n",
        f"**Generated:** {now_str}  ",
        f"**Verified leads:** {len(verified)}  ",
        f"**Unknown/Custom Token leads:** {len(unknown)}  ",
        f"**Pipeline time:** {elapsed:.2f}s\n",
        "---\n",
    ]

    if verified:
        lines.append("## Verified Bounty Leads\n")
        lines.append(
            "| Score | Amount | Currency | Repo | Title | Labels | Payout | Link |"
        )
        lines.append(
            "|-------|--------|----------|------|-------|--------|--------|------|"
        )
        for lead in verified:
            score = lead.get("Score", 0.0)
            prev = lead.get("PrevScore")
            prefix = ""
            if prev is not None:
                delta = score - prev
                if delta >= 1.0:
                    prefix = "↑ "
                elif delta <= -1.0:
                    prefix = "↓ "

            badges = []
            if lead.get("HasOnchainEscrow"):
                badges.append("🔒")
            if lead.get("MentionsWalletPayout"):
                badges.append("💳")
            if lead.get("MentionsNoKyc"):
                badges.append("🆓")
            badge_str = " ".join(badges) if badges else "—"

            safe_title = (prefix + lead["Title"]).replace("|", "\\|")[:75]
            lines.append(
                f"| {score} | {lead['Amount']} "
                f"| {lead.get('Currency', 'USD')} | {lead['Repo']} | {safe_title} "
                f"| {lead['Labels']} | {badge_str} | [link]({lead['Link']}) |"
            )
        lines.append("")

    if unknown:
        lines.append("## Unknown / Custom Token Leads\n")
        lines.append(
            "| Score | Amount | Repo | Title | Labels | Payout | Link |"
        )
        lines.append(
            "|-------|--------|------|-------|--------|--------|------|"
        )
        for lead in unknown:
            badges = []
            if lead.get("HasOnchainEscrow"):
                badges.append("🔒")
            if lead.get("MentionsWalletPayout"):
                badges.append("💳")
            if lead.get("MentionsNoKyc"):
                badges.append("🆓")
            badge_str = " ".join(badges) if badges else "—"

            safe_title = lead["Title"].replace("|", "\\|")[:80]
            lines.append(
                f"| {lead.get('Score', '')} | {lead['Amount']} | {lead['Repo']} "
                f"| {safe_title} | {lead['Labels']} | {badge_str} | [link]({lead['Link']}) |"
            )
        lines.append("")

    if not verified and not unknown:
        lines.append("_No leads survived pipeline filtering._\n")

    lines.append("---\n")
    lines.append(
        "> **Disclaimer:** This tool is for discovery only. "
        "Always verify bounty legitimacy before investing time.\n"
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    log.info("Markdown report written to %s", path)


# ─── JSON output ─────────────────────────────────────────────────────
def write_json_output(
    leads: list[dict],
    elapsed: float,
    path: str,
) -> None:
    """Write all leads to a JSON file with key fields."""
    output = {
        "generated_at": (
            datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z"
        ),
        "pipeline_time_seconds": round(elapsed, 2),
        "total_leads": len(leads),
        "leads": [
            {
                "score": lead.get("Score", 0),
                "amount": lead.get("Amount", ""),
                "numeric_amount": lead.get("AmountNum", 0),
                "currency": lead.get("Currency", "USD"),
                "repo": lead.get("Repo", ""),
                "title": lead.get("Title", ""),
                "labels": lead.get("Labels", ""),
                "link": lead.get("Link", ""),
                "has_onchain_escrow": lead.get("HasOnchainEscrow", False),
                "mentions_wallet_payout": lead.get("MentionsWalletPayout", False),
                "mentions_no_kyc": lead.get("MentionsNoKyc", False),
            }
            for lead in leads
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)
    log.info("JSON report written to %s", path)
