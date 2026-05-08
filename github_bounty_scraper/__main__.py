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

import hashlib
import pathlib

from .cli import parse_args  # noqa: E402
from .core import run_pipeline  # noqa: E402

PROD_MODEL_FEATURES = [
    "vibe_score",
    "positive_escrow_count",
    "escrow_weight_sum",
    "has_onchain_escrow",
    "mentions_no_kyc",
    "mentions_wallet_payout",
    "merges_last_45d",
    "is_closed",
]


def _verify_model_checksum(model_path: str, checksum_path: str) -> None:
    """Raise RuntimeError if the model file does not match its expected SHA256."""
    expected = pathlib.Path(checksum_path).read_text().strip().split()[0]
    actual = hashlib.sha256(pathlib.Path(model_path).read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"Model checksum mismatch! Expected {expected}, got {actual}. "
            "The model file may be corrupted or tampered with."
        )


async def _run_inspect(db_path: str, mode: str, limit: int, min_ml_prob: float = 0.0) -> None:
    import json
    import os

    import joblib
    import numpy as np

    # Task 5: Assert leakage-free model
    meta = {}
    if os.path.exists("best_threshold.json"):
        with open("best_threshold.json", "r") as f:
            meta = json.load(f)
            if not meta.get("leakage_free"):
                print("\nFATAL: Model in bounty_model.pkl is NOT leakage-free (detected by best_threshold.json).")
                print("Run tools/train_bounty_model.py to generate a production-ready model.")
                sys.exit(1)

    prod_features = meta.get("features") or PROD_MODEL_FEATURES

    from .db import get_recent_leads

    leads = await get_recent_leads(db_path, mode, limit)
    if not leads:
        print(f"No leads found for mode={mode} yet.")
        sys.exit(0)

    # Attempt to load ML model for probability lift
    model = None
    if os.path.exists("bounty_model.pkl"):
        try:
            _verify_model_checksum("bounty_model.pkl", "bounty_model.pkl.sha256")
            model = joblib.load("bounty_model.pkl")
        except Exception:
            pass

    for L in leads:
        if model:
            try:
                row = {}
                row["log_amount"] = np.log10(max(0, float(L.get("numeric_amount") or 0)) + 1)
                row["vibe_score"] = float(L.get("vibe_score") or 0)
                row["positive_escrow_count"] = float(L.get("positive_escrow_count") or 0)
                row["escrow_weight_sum"] = float(L.get("escrow_weight_sum") or 0.0)
                row["has_onchain_escrow"] = int(L.get("has_onchain_escrow") or 0)
                row["mentions_no_kyc"] = int(L.get("mentions_no_kyc") or 0)
                row["mentions_wallet_payout"] = int(L.get("mentions_wallet_payout") or 0)
                row["merges_last_45d"] = float(L.get("merges_last_45d") or 0)
                row["is_closed"] = 1 if "closed" in str(L.get("lead_mode", "")).lower() else 0

                feat_vec = np.array([[row[f] for f in prod_features]])
                assert feat_vec.shape[1] == len(prod_features), (
                    f"Feature mismatch: model expects {len(prod_features)} features, got {feat_vec.shape[1]}"
                )
                L["ml_prob"] = float(model.predict_proba(feat_vec)[0, 1])
            except Exception:
                L["ml_prob"] = 0.0
        else:
            L["ml_prob"] = 0.0

    def _rank_key(b):
        return (
            -b.get("ml_prob", 0),  # 1. ML score descending
            -int(b.get("has_onchain_escrow", 0)),  # 2. on-chain escrow first
            -int(b.get("mentions_wallet_payout", 0)),  # 3. direct wallet payout
            -int(b.get("mentions_no_kyc", 0)),  # 4. no-KYC payout
            -float(b.get("numeric_amount") or 0),  # 5. larger amounts
        )

    leads.sort(key=_rank_key)
    
    if min_ml_prob > 0:
        leads = [L for L in leads if L.get("ml_prob", 0) >= min_ml_prob]
        if not leads:
            print(f"No leads found with ML probability >= {min_ml_prob:.2f}")
            return

    print(
        f"{'ML%':<5} | {'SCORE':<7} | {'Δ':<6} | {'AMOUNT':<12} | {'MODE':<14} | {'VIBE':<5} | {'REPO/NAME':<30} | URL"
    )
    print("-" * 145)
    for L in leads:
        ml_prob_str = f"{L.get('ml_prob', 0) * 100:3.0f}%"
        score_val = L["score"]
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

        mode_str = str(L.get("lead_mode", "strict"))
        vibe = str(L.get("vibe_score")) if L.get("vibe_score") is not None else "—"
        repo = str(L.get("repo_name", ""))[:30]
        url = str(L.get("issue_url", ""))

        print(
            f"{ml_prob_str:<5} | {score_str:<7} | {delta_str:<6} | {amt:<12} | {mode_str:<14} | {vibe:<5} | {repo:<30} | {url}"
        )

        # Task 3c: Add WHY tags
        why_parts = []
        if L.get("has_onchain_escrow"):
            why_parts.append("on-chain escrow")
        if L.get("mentions_wallet_payout"):
            why_parts.append("wallet payout")
        if L.get("mentions_no_kyc"):
            why_parts.append("no KYC")
        vibe_val = L.get("vibe_score")
        if vibe_val is not None and vibe_val >= 70:
            why_parts.append(f"vibe={vibe_val}")
        if val and val > 0:
            why_parts.append(f"${val:.0f}")

        why = " · ".join(why_parts) if why_parts else "weak signals"
        print(f"  ↳ {why}")


def main() -> None:
    command, ns, config = parse_args()
    if command == "scrape":
        if getattr(ns, "auto_refresh", False):
            import sqlite3
            import time

            db_path = getattr(ns, "db_path", "bounty_stats.db")
            refresh_days = getattr(ns, "refresh_days", 3)
            try:
                conn = sqlite3.connect(db_path)
                newest = conn.execute("SELECT MAX(last_seen_at) FROM issue_stats").fetchone()[0]
                conn.close()
                if newest and (time.time() - newest) < refresh_days * 86400:
                    age_h = (time.time() - newest) / 3600
                    print(f"Leads are fresh ({age_h:.1f}h old). Skipping scrape.")
                    print("Run without --auto-refresh to force a full pass.")
                    sys.exit(0)
            except Exception as e:
                print(f"Auto-refresh check failed ({e}). Proceeding with scrape.")

        asyncio.run(run_pipeline(config))
    elif command == "inspect-leads":
        asyncio.run(_run_inspect(ns.db_path, ns.mode, ns.limit or config.top_n, min_ml_prob=getattr(ns, "min_ml_prob", 0.0)))
    elif command == "vibe-check":
        from .vibe import run_vibe_check

        asyncio.run(
            run_vibe_check(
                raw_candidates_file=ns.raw_candidates_file,
                db_path=ns.db_path,
                limit=ns.limit or config.limit,
                mode=ns.mode,
                concurrency=ns.concurrency,
                model=config.gemini_model,
                retry_file=config.vibe_retry_file,
            )
        )
    elif command == "dump-dataset":
        from .db import dump_dataset

        asyncio.run(
            dump_dataset(
                db_path=ns.db_path,
                out_path=ns.out_csv,
                raw_candidates_file=getattr(ns, "raw_candidates_file", "exploration_raw.jsonl"),
                label_threshold=getattr(ns, "label_threshold", 25.0),
            )
        )


if __name__ == "__main__":
    main()
