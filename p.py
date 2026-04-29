"""
Quick DB viewer for bounty_stats.db — shows top issues and repo stats
with filtering and export options.
"""

import argparse
import csv
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
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Filter issues by first_seen_at >= this date.",
    )
    parser.add_argument(
        "--min-amount",
        type=float,
        default=None,
        dest="min_amount",
        metavar="USD",
        help="Filter issues with numeric_amount >= this value.",
    )
    parser.add_argument(
        "--show-unknown",
        action="store_true",
        dest="show_unknown",
        help="Include Unknown/Custom Token leads (amount < 0).",
    )
    parser.add_argument(
        "--sort-by",
        choices=["amount", "score", "date"],
        default="score",
        dest="sort_by",
        help="Sort order for issues (default: score).",
    )
    parser.add_argument(
        "--export",
        type=str,
        default=None,
        metavar="FILE.csv",
        help="Export results to a CSV file.",
    )
    args = parser.parse_args()
    limit = args.limit

    try:
        conn = sqlite3.connect("bounty_stats.db")
    except sqlite3.OperationalError as exc:
        print(f"Error opening database: {exc}", file=sys.stderr)
        sys.exit(1)

    cursor = conn.cursor()

    # ── Detect available columns ──
    try:
        cursor.execute("PRAGMA table_info(issue_stats)")
        issue_cols = {row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        issue_cols = set()

    has_score = "score" in issue_cols
    has_numeric = "numeric_amount" in issue_cols
    has_currency = "currency_symbol" in issue_cols
    has_first_seen = "first_seen_at" in issue_cols
    has_raw_display = "raw_display_amount" in issue_cols

    # ── Build query ──
    select_cols = []
    select_cols.append("scraped_amount")
    if has_numeric:
        select_cols.append("numeric_amount")
    if has_score:
        select_cols.append("score")
    if has_currency:
        select_cols.append("currency_symbol")
    if has_raw_display:
        select_cols.append("raw_display_amount")
    if has_first_seen:
        select_cols.append("first_seen_at")
    select_cols.append("issue_url")

    where_clauses = []
    where_params: list = []

    if not args.show_unknown:
        amount_col = "numeric_amount" if has_numeric else "scraped_amount"
        where_clauses.append(f"{amount_col} > 0")

    if args.min_amount is not None:
        amount_col = "numeric_amount" if has_numeric else "scraped_amount"
        where_clauses.append(f"{amount_col} >= ?")
        where_params.append(args.min_amount)

    if args.since and has_first_seen:
        # Convert date string to unix timestamp (approximate).
        import datetime
        try:
            since_dt = datetime.datetime.strptime(args.since, "%Y-%m-%d")
            since_ts = since_dt.timestamp()
            where_clauses.append("first_seen_at >= ?")
            where_params.append(since_ts)
        except ValueError:
            print(f"Invalid date format: {args.since} (expected YYYY-MM-DD)", file=sys.stderr)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Sort order.
    if args.sort_by == "score" and has_score:
        order = "score DESC"
    elif args.sort_by == "date" and has_first_seen:
        order = "first_seen_at DESC"
    else:
        amount_col = "numeric_amount" if has_numeric else "scraped_amount"
        order = f"{amount_col} DESC"

    query = f"SELECT {', '.join(select_cols)} FROM issue_stats{where_sql} ORDER BY {order} LIMIT ?"
    where_params.append(limit)

    # ── Table 1: Top issues ──
    print("=" * 80)
    print(f"  TOP {limit} ISSUES (sorted by {args.sort_by})")
    print("=" * 80)

    header = f"{'Score':>7}  {'Amount':>12}  {'Currency':>8}  | Issue URL" if has_score else f"{'Amount':>12}  | Issue URL"
    print(header)
    print("-" * 80)

    try:
        cursor.execute(query, where_params)
        rows = cursor.fetchall()
        export_rows = []

        for row in rows:
            # Unpack based on available columns.
            idx = 0
            scraped = row[idx]
            idx += 1
            numeric = row[idx] if has_numeric else scraped
            idx += (1 if has_numeric else 0)
            score = row[idx] if has_score else None
            idx += (1 if has_score else 0)
            currency = row[idx] if has_currency else "USD"
            idx += (1 if has_currency else 0)
            raw_disp = row[idx] if has_raw_display else None
            idx += (1 if has_raw_display else 0)
            if has_first_seen:
                idx += 1
            issue_url = row[idx]

            amount_str = raw_disp if raw_disp else (
                f"${numeric:,.0f}" if numeric and numeric > 0 else "Unknown/Custom"
            )

            if has_score:
                score_str = f"{score:.1f}" if score else "0.0"
                print(f"{score_str:>7}  {amount_str:>12}  {str(currency or 'USD'):>8}  | {issue_url}")
            else:
                print(f"{amount_str:>12}  | {issue_url}")

            export_rows.append({
                "score": score or 0,
                "amount": numeric or scraped,
                "currency": currency or "USD",
                "display": amount_str,
                "url": issue_url,
            })

    except sqlite3.OperationalError as exc:
        print(f"  (issue_stats table not found: {exc})")
        export_rows = []

    # ── Table 2: Top repos by escrows_seen ──
    print()
    print("=" * 80)
    print(f"  TOP {limit} REPOS BY ESCROWS SEEN")
    print("=" * 80)
    print(
        f"{'Escrows':>8}  {'Rugs':>6}  {'Snipes':>7}  "
        f"{'Merges45d':>10}  {'MaxBounty':>10}  | Repo"
    )
    print("-" * 80)

    try:
        # Detect max_bounty_amount column.
        cursor.execute("PRAGMA table_info(repo_stats)")
        repo_cols = {row[1] for row in cursor.fetchall()}
        has_max_bounty = "max_bounty_amount" in repo_cols

        if has_max_bounty:
            cursor.execute(
                "SELECT repo_name, escrows_seen, rugs_seen, snipes_detected, "
                "merges_last_45d, max_bounty_amount FROM repo_stats "
                "ORDER BY escrows_seen DESC LIMIT ?",
                (limit,),
            )
        else:
            cursor.execute(
                "SELECT repo_name, escrows_seen, rugs_seen, snipes_detected, "
                "merges_last_45d FROM repo_stats "
                "ORDER BY escrows_seen DESC LIMIT ?",
                (limit,),
            )

        rows = cursor.fetchall()
        for row in rows:
            repo = row[0]
            escrows = row[1] or 0
            rugs = row[2] or 0
            snipes = row[3] or 0
            merges = row[4] or 0
            max_bounty = row[5] if has_max_bounty and len(row) > 5 else 0

            max_b_str = f"${max_bounty:,.0f}" if max_bounty and max_bounty > 0 else "-"
            print(
                f"{escrows:>8}  {rugs:>6}  {snipes:>7}  "
                f"{merges:>10}  {max_b_str:>10}  | {repo}"
            )
    except sqlite3.OperationalError as exc:
        print(f"  (repo_stats table not found: {exc})")

    # ── CSV export ──
    if args.export and export_rows:
        try:
            with open(args.export, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["score", "amount", "currency", "display", "url"])
                writer.writeheader()
                writer.writerows(export_rows)
            print(f"\nExported {len(export_rows)} rows to {args.export}")
        except OSError as exc:
            print(f"Export error: {exc}", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
