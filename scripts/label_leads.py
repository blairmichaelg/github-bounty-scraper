#!/usr/bin/env python3
"""
Interactive CLI labeling tool for bounty leads.
Opens each lead in the browser and asks: is this a real bounty? (y/n/s=skip/q=quit)

Usage:
    python scripts/label_leads.py --limit 100 --min-vibe 30 --max-vibe 70
    python scripts/label_leads.py --unlabeled-only --limit 50
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path


DB_PATH = "bounty_stats.db"
MANUAL_LABEL_TABLE = "manual_labels"


def ensure_label_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {MANUAL_LABEL_TABLE} (
            issue_url TEXT PRIMARY KEY,
            label INTEGER NOT NULL,  -- 1 = bounty, 0 = not bounty
            labeled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    """)
    conn.commit()


def get_candidates(conn: sqlite3.Connection, limit: int, min_vibe: int, max_vibe: int, unlabeled_only: bool) -> list:
    already_labeled = {row[0] for row in conn.execute(f"SELECT issue_url FROM {MANUAL_LABEL_TABLE}").fetchall()}
    query = f"""
        SELECT issue_url, repo_name, score, vibe_score, numeric_amount
        FROM issue_stats
        WHERE vibe_score BETWEEN ? AND ?
        ORDER BY score DESC
        LIMIT ?
    """
    rows = conn.execute(query, (min_vibe, max_vibe, limit * 3)).fetchall()
    if unlabeled_only:
        rows = [r for r in rows if r[0] not in already_labeled]
    return rows[:limit]


def label_leads(limit: int = 100, min_vibe: int = 30, max_vibe: int = 70, unlabeled_only: bool = True) -> None:
    conn = sqlite3.connect(DB_PATH)
    ensure_label_table(conn)
    candidates = get_candidates(conn, limit, min_vibe, max_vibe, unlabeled_only)

    if not candidates:
        print("No candidates to label with the given filters.")
        return

    print(f"\n=== BOUNTY LABELING TOOL ===")
    print(f"Labeling {len(candidates)} leads. Commands: y=bounty, n=not bounty, s=skip, q=quit\n")

    labeled = 0
    for i, (url, repo, score, vibe, amount) in enumerate(candidates):
        print(f"[{i+1}/{len(candidates)}] {repo}")
        print(f"  Score: {score:.1f} | Vibe: {vibe} | Amount: ${amount or 0:.0f}")
        print(f"  URL: {url}")
        
        # Open in browser
        try:
            webbrowser.open(url)
        except Exception:
            pass

        while True:
            try:
                answer = input("  Label (y/n/s/q): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                answer = "q"

            if answer == "q":
                print(f"\nLabeled {labeled} leads this session.")
                conn.close()
                return
            elif answer == "s":
                break
            elif answer in ("y", "n"):
                label_val = 1 if answer == "y" else 0
                conn.execute(
                    f"INSERT OR REPLACE INTO {MANUAL_LABEL_TABLE} (issue_url, label) VALUES (?, ?)",
                    (url, label_val)
                )
                conn.commit()
                labeled += 1
                print(f"  Saved: {'BOUNTY' if label_val else 'NOT BOUNTY'}\n")
                break
            else:
                print("  Invalid input. Use y, n, s, or q.")

    print(f"\nSession complete. Labeled {labeled} leads total.")
    total = conn.execute(f"SELECT COUNT(*) FROM {MANUAL_LABEL_TABLE}").fetchone()
    print(f"Total manual labels in DB: {total[0]}")
    conn.close()


def export_labeled_csv(output_path: str = "bounty_dataset_manual.csv") -> None:
    """Export issue_stats + repo_stats joined with manual labels for training."""
    conn = sqlite3.connect(DB_PATH)
    ensure_label_table(conn)
    import pandas as pd
    df = pd.read_sql_query(f"""
        SELECT
            s.issue_url,
            s.repo_name,
            s.score,
            s.vibe_score,
            s.numeric_amount,
            s.positive_escrow_count,
            s.escrow_weight_sum,
            COALESCE(r.merges_last_45d, 0)      AS merges_last_45d,
            COALESCE(r.total_escrows_seen, 0)   AS total_escrows_seen,
            COALESCE(r.rugs_seen, 0)            AS rugs_seen,
            COALESCE(s.is_dead_repo, 0)         AS is_dead_repo,
            COALESCE(s.escrow_verified, 0)      AS escrow_verified,
            m.label                             AS manual_label
        FROM issue_stats s
        LEFT JOIN repo_stats r ON s.repo_name = r.repo_name
        INNER JOIN {MANUAL_LABEL_TABLE} m ON s.issue_url = m.issue_url
    """, conn)
    conn.close()
    df.to_csv(output_path, index=False)
    print(f"Exported {len(df)} manually labeled rows to {output_path}")
    print(f"Columns: {list(df.columns)}")
    if not df.empty:
        print(f"Label distribution: {df['manual_label'].value_counts().to_dict()}")
        print(f"Null counts in feature columns:")
        feat_cols = ["numeric_amount","merges_last_45d","total_escrows_seen","rugs_seen","is_dead_repo","escrow_verified"]
        print(df[feat_cols].isnull().sum().to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive bounty lead labeling tool")
    subparsers = parser.add_subparsers(dest="command")

    label_parser = subparsers.add_parser("label", help="Interactively label leads")
    label_parser.add_argument("--limit", type=int, default=50)
    label_parser.add_argument("--min-vibe", type=int, default=30)
    label_parser.add_argument("--max-vibe", type=int, default=70)
    label_parser.add_argument("--unlabeled-only", action="store_true", default=True)

    export_parser = subparsers.add_parser("export", help="Export labeled data to CSV")
    export_parser.add_argument("--output", default="bounty_dataset_manual.csv")

    args = parser.parse_args()
    if args.command == "label":
        label_leads(args.limit, args.min_vibe, args.max_vibe, args.unlabeled_only)
    elif args.command == "export":
        export_labeled_csv(args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
