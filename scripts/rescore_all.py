#!/usr/bin/env python3
"""
Rescore all leads blending heuristic score with ML model probability.

Usage:
    python scripts/rescore_all.py
    python scripts/rescore_all.py --model bounty_model.pkl --blend-ml 0.5
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3

import joblib
import numpy as np


def rescore(model_path: str, blend_ml: float) -> None:
    model = joblib.load(model_path)
    src = pathlib.Path("scripts/train_model.py").read_text()
    feat_match = re.search(r"FEATURE_COLUMNS\s*=\s*\[(.*?)\]", src, re.DOTALL)
    features = re.findall(r"[\"'](.*?)[\"']", feat_match.group(1))
    print(f"Model: {model_path} | Features: {features} | ML blend: {blend_ml:.0%}")

    conn = sqlite3.connect("bounty_stats.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.*,
               COALESCE(r.merges_last_45d, 0)    AS merges_last_45d,
               COALESCE(r.total_escrows_seen, 0) AS total_escrows_seen,
               COALESCE(r.rugs_seen, 0)          AS rugs_seen
        FROM issue_stats s
        LEFT JOIN repo_stats r ON s.repo_name = r.repo_name
    """).fetchall()

    classes = list(model.classes_)
    pos_idx = classes.index(1) if 1 in classes else 1
    updated = skipped = 0

    for r in rows:
        d = dict(r)
        try:
            x = np.array([[float(d.get(f) or 0) for f in features]])
            ml_prob = float(model.predict_proba(x)[0][pos_idx]) * 100
            heuristic = float(d.get("score") or 0)
            blended = round((1 - blend_ml) * heuristic + blend_ml * ml_prob, 2)
            conn.execute("UPDATE issue_stats SET score = ? WHERE issue_url = ?", (blended, d["issue_url"]))
            updated += 1
        except Exception:
            skipped += 1

    conn.commit()
    conn.close()
    print(f"Rescored {updated} leads, skipped {skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rescore all leads with blended model")
    parser.add_argument("--model", default="bounty_model.pkl")
    parser.add_argument("--blend-ml", type=float, default=0.4)
    args = parser.parse_args()
    rescore(args.model, args.blend_ml)
