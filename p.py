"""
Quick DB viewer for bounty_stats.db — shows top issues and repo stats.
"""

import argparse
import sqlite3
import sys


def main() -> None:
    """Print the top issue_stats and repo_stats from the local SQLite DB."""
    parser = argparse.ArgumentParser(
        description="View bounty_stats.db contents."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Number of rows to display per table (default: 15)",
    )
    args = parser.parse_args()
    limit = args.limit

    try:
        conn = sqlite3.connect("bounty_stats.db")
    except sqlite3.OperationalError as exc:
        print(f"Error opening database: {exc}", file=sys.stderr)
        sys.exit(1)

    cursor = conn.cursor()

    # ── Table 1: Top issues by scraped amount ──
    print("=" * 70)
    print(f"  TOP {limit} ISSUES BY SCRAPED AMOUNT")
    print("=" * 70)
    print(f"{'Amount':>12}  | Issue URL")
    print("-" * 70)

    try:
        cursor.execute(
            "SELECT scraped_amount, issue_url FROM issue_stats "
            "ORDER BY scraped_amount DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        for row in rows:
            amount_str = (
                f"${row[0]:,.0f}" if row[0] > 0 else "Unknown/Custom"
            )
            print(f"{amount_str:>12}  | {row[1]}")
    except sqlite3.OperationalError as exc:
        print(f"  (issue_stats table not found: {exc})")

    # ── Table 2: Top repos by escrows_seen ──
    print()
    print("=" * 70)
    print(f"  TOP {limit} REPOS BY ESCROWS SEEN")
    print("=" * 70)
    print(
        f"{'Escrows':>8}  {'Rugs':>6}  {'Snipes':>7}  "
        f"{'Merges45d':>10}  | Repo"
    )
    print("-" * 70)

    try:
        cursor.execute(
            "SELECT repo_name, escrows_seen, rugs_seen, snipes_detected, "
            "merges_last_45d FROM repo_stats "
            "ORDER BY escrows_seen DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        for row in rows:
            repo, escrows, rugs, snipes, merges = row
            print(
                f"{escrows:>8}  {rugs:>6}  {snipes:>7}  "
                f"{merges:>10}  | {repo}"
            )
    except sqlite3.OperationalError as exc:
        print(f"  (repo_stats table not found: {exc})")

    conn.close()


if __name__ == "__main__":
    main()
