"""
Entry point for ``python -m github_bounty_scraper``.

Subcommands:
- scrape: Run the main discovery and enrichment pipeline.
- inspect-leads: View recently discovered bounty candidates.
- vibe-check: Run LLM-based audit on exploration candidates.
- dump-dataset: Export stats to CSV for model training.
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
    import os, joblib, numpy as np
    from .db import get_recent_leads
    leads = await get_recent_leads(db_path, mode, limit)
    if not leads:
        print(f"No leads found for mode={mode} yet.")
        sys.exit(0)
    
    # Attempt to load ML model for probability lift
    model = None
    if os.path.exists("bounty_model.pkl"):
        try:
            model = joblib.load("bounty_model.pkl")
        except: pass

    for L in leads:
        if model:
            # Prepare features for ML model
            # ['log_amount', 'vibe_score', 'positive_escrow_count', 'escrow_weight_sum',
            #  'has_onchain_escrow', 'mentions_no_kyc', 'mentions_wallet_payout',
            #  'merges_last_45d', 'is_closed']
            try:
                log_amt = np.log10(max(0, float(L.get("numeric_amount") or 0)) + 1)
                feats = np.array([[
                    log_amt,
                    float(L.get("vibe_score") or 0),
                    float(L.get("positive_escrow_count") or 0),
                    float(L.get("escrow_weight_sum") or 0.0),
                    int(L.get("has_onchain_escrow") or 0),
                    int(L.get("mentions_no_kyc") or 0),
                    int(L.get("mentions_wallet_payout") or 0),
                    float(L.get("merges_last_45d") or 0),
                    1 if 'closed' in str(L.get('lead_mode', '')).lower() else 0
                ]])
                L["ml_prob"] = float(model.predict_proba(feats)[0, 1])
            except:
                L["ml_prob"] = 0.0
        else:
            L["ml_prob"] = 0.0

    def _rank_key(b):
        return (
            -b.get("ml_prob", 0),                # 1. ML score descending
            -int(b.get("has_onchain_escrow", 0)), # 2. on-chain escrow first
            -int(b.get("mentions_wallet_payout", 0)), # 3. direct wallet payout
            -int(b.get("mentions_no_kyc", 0)),    # 4. no-KYC payout
            -float(b.get("numeric_amount") or 0), # 5. larger amounts
        )

    leads.sort(key=_rank_key)

    print(f"{'ML%':<5} | {'SCORE':<7} | {'Δ':<6} | {'AMOUNT':<12} | {'MODE':<14} | {'VIBE':<5} | {'REPO/NAME':<30} | URL")
    print("-" * 145)
    for L in leads:
        ml_prob_str = f"{L.get('ml_prob', 0)*100:3.0f}%"
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
        vibe = str(L.get("vibe_score")) if L.get("vibe_score") is not None else "—"
        repo = str(L.get('repo_name', ''))[:30]
        url = str(L.get('issue_url', ''))
        
        print(f"{ml_prob_str:<5} | {score_str:<7} | {delta_str:<6} | {amt:<12} | {mode_str:<14} | {vibe:<5} | {repo:<30} | {url}")

        # Task 3c: Add WHY tags
        why_parts = []
        if L.get("has_onchain_escrow"):       why_parts.append("on-chain escrow")
        if L.get("mentions_wallet_payout"):   why_parts.append("wallet payout")
        if L.get("mentions_no_kyc"):          why_parts.append("no KYC")
        if L.get("vibe_score") and L.get("vibe_score") >= 70:  why_parts.append(f"vibe={L['vibe_score']}")
        if val and val > 0:                   why_parts.append(f"${val:.0f}")
        
        why = " · ".join(why_parts) if why_parts else "weak signals"
        print(f"  ↳ {why}")


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
        asyncio.run(
            dump_dataset(
                db_path=ns.db_path,
                out_path=ns.out,
                raw_file=getattr(ns, "raw_file", "exploration_raw.jsonl"),
                label_threshold=getattr(ns, "label_threshold", 25.0),
            )
        )


if __name__ == "__main__":
    main()
