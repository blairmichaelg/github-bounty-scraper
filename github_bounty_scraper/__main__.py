"""
Entry point for ``python -m github_bounty_scraper``.
"""

import asyncio
import sys

# Encoding safety (Windows terminals).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from .cli import parse_args  # noqa: E402
from .core import run_pipeline  # noqa: E402


def main() -> None:
    command, ns, config = parse_args()
    if command == "scrape":
        asyncio.run(run_pipeline(config))
    elif command == "inspect-leads":
        from .db import get_recent_leads
        
        async def run_inspect():
            leads = await get_recent_leads("bounty_stats.db", ns.mode, ns.limit)
            if not leads:
                print(f"No leads found for mode={ns.mode} yet.")
                sys.exit(0)
            
            print(f"{'SCORE':<7} | {'AMOUNT':<10} | {'MODE':<14} | {'ESCROW':<6} | {'DEAD':<4} | {'REPO/NAME':<30} | URL")
            print("-" * 120)
            for L in leads:
                score = f"{L['score']:.2f}"
                amt = f"${L['numeric_amount']:.2f}" if L['numeric_amount'] >= 0 else "Unknown"
                mode = str(L.get('lead_mode', 'strict'))
                escrow = "yes" if L.get('escrow_verified') else "no"
                dead = "yes" if L.get('is_dead_repo') else "no"
                repo = str(L.get('repo_name', ''))[:30]
                url = str(L.get('issue_url', ''))
                
                print(f"{score:<7} | {amt:<10} | {mode:<14} | {escrow:<6} | {dead:<4} | {repo:<30} | {url}")

        asyncio.run(run_inspect())

if __name__ == "__main__":
    main()
