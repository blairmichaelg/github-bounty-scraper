"""Balance an exported bounty dataset for model training.

The input CSV is expected to come from:

    python -m github_bounty_scraper dump-dataset --out-csv bounty_dataset.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path


def balance_dataset(input_path: Path, output_path: Path, target_ratio: int, seed: int) -> None:
    rows = list(csv.DictReader(input_path.open(encoding="utf-8")))
    if not rows:
        raise SystemExit(f"No rows found in {input_path}")

    rng = random.Random(seed)
    pos = [r for r in rows if r["is_bounty"] == "1"]
    neg = [r for r in rows if r["is_bounty"] == "0"]
    amb = [r for r in rows if r["is_bounty"] == ""]

    for r in rows:
        r["is_closed"] = "1" if "closed" in (r.get("lead_mode") or "").lower() else "0"

    neg_clean = [r for r in neg if (r.get("title") or "").strip() or (r.get("body_snippet") or "").strip()]
    orphaned = len(neg) - len(neg_clean)

    max_neg = len(pos) * target_ratio
    neg_sampled = rng.sample(neg_clean, min(max_neg, len(neg_clean)))

    mid_vibe_neg = [r for r in neg_clean if r.get("vibe_score", "").strip() and 10 <= int(r["vibe_score"]) <= 49]
    kept_urls = {r["issue_url"] for r in neg_sampled}
    for r in mid_vibe_neg:
        if r["issue_url"] not in kept_urls:
            neg_sampled.append(r)
            kept_urls.add(r["issue_url"])

    final = pos + neg_sampled + amb
    rng.shuffle(final)

    headers = list(rows[0].keys())
    if "is_closed" not in headers:
        headers.append("is_closed")

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(final)

    labeled_final = [r for r in final if r["is_bounty"] in ("0", "1")]
    p2 = sum(1 for r in labeled_final if r["is_bounty"] == "1")
    n2 = sum(1 for r in labeled_final if r["is_bounty"] == "0")
    print(f"Orphaned negatives removed : {orphaned}")
    print(f"Positives                  : {p2}")
    print(f"Negatives (sampled)        : {n2}")
    print(f"  of which mid-vibe (10-49): {len(mid_vibe_neg)}")
    print(f"Ambiguous (excluded)       : {len(amb)}")
    print(f"Final training rows        : {len(final)}")
    print(f"Imbalance ratio            : 1:{n2 // max(p2, 1)}")
    print(f"Output                     : {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Balance a bounty dataset CSV for model training")
    parser.add_argument("--input", default=Path("bounty_dataset.csv"), type=Path)
    parser.add_argument("--output", default=Path("bounty_dataset_train.csv"), type=Path)
    parser.add_argument("--target-ratio", default=4, type=int, help="Maximum negative:positive ratio")
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")
    balance_dataset(args.input, args.output, args.target_ratio, args.seed)


if __name__ == "__main__":
    main()
