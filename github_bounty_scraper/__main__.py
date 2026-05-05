"""
Entry point for ``python -m github_bounty_scraper``.
"""

import asyncio
import sys

# Encoding safety (Windows terminals).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

from .cli import parse_args  # noqa: E402
from .core import run_pipeline  # noqa: E402


async def _run_inspect(db_path: str, mode: str, limit: int) -> None:
    from .db import get_recent_leads
    leads = await get_recent_leads(db_path, mode, limit)
    if not leads:
        print(f"No leads found for mode={mode} yet.")
        sys.exit(0)
    
    print(f"{'SCORE':<7} | {'Δ':<6} | {'AMOUNT':<12} | {'MODE':<14} | {'ESCROW':<6} | {'DEAD':<4} | {'VIBE':<5} | {'REPO/NAME':<30} | URL")
    print("-" * 142)
    for L in leads:
        score_val = L['score']
        score_str = f"{score_val:.2f}"
        
        prev_val = L.get("prev_score")
        if prev_val is not None:
            diff = score_val - prev_val
            delta_str = f"{'+' if diff >= 0 else ''}{diff:.2f}"
        else:
            delta_str = "—"
        
        val = L.get("numeric_amount")
        if val is None or val == 0.0:
            amt = "Unknown"
        elif val < 0:
            amt = "Custom Token"
        else:
            amt = f"${val:,.2f}"
        
        mode_str = str(L.get('lead_mode', 'strict'))
        escrow = "yes" if L.get('escrow_verified') else "no"
        dead = "yes" if L.get('is_dead_repo') else "no"
        vibe = str(L.get("vibe_score")) if L.get("vibe_score") is not None else "—"
        repo = str(L.get('repo_name', ''))[:30]
        url = str(L.get('issue_url', ''))
        
        print(f"{score_str:<7} | {delta_str:<6} | {amt:<12} | {mode_str:<14} | {escrow:<6} | {dead:<4} | {vibe:<5} | {repo:<30} | {url}")


def main() -> None:
    command, ns, config = parse_args()
    if command == "scrape":
        asyncio.run(run_pipeline(config))
    elif command == "inspect-leads":
        asyncio.run(_run_inspect(ns.db_path, ns.mode, ns.limit))
    elif command == "vibe-check":
        from .vibe import run_vibe_check

        asyncio.run(
            run_vibe_check(
                raw_file=ns.raw_file,
                db_path=ns.db_path,
                limit=ns.limit,
                mode=ns.mode,
                concurrency=ns.concurrency,
                model=config.gemini_model,
            )
        )
    elif command == "dump-dataset":
        from .db import dump_dataset
        asyncio.run(dump_dataset(ns.db_path, ns.out))

if __name__ == "__main__":
    main()
