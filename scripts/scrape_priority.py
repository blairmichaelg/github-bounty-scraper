#!/usr/bin/env python3
"""
Scrape only the confirmed high-value bounty programs from open_bounty_programs.json.
Run this daily for fastest time-to-discovery on competitive programs.

Usage:
    python scripts/scrape_priority.py
    python scripts/scrape_priority.py --max-issues 50 --dry-run
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_programs(path: str = "open_bounty_programs.json") -> list[dict]:
    data = json.loads(Path(path).read_text())
    programs = []
    for tier_key in sorted(k for k in data if k.startswith("tier_")):
        programs.extend(data[tier_key]["repos"])
    return programs


def build_query(program: dict) -> str:
    repo = program["repo"]
    label = program.get("label_filter", "bounty")
    return f'repo:{repo} label:"{label}" state:open is:issue'


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape priority bounty programs")
    parser.add_argument("--max-issues", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--programs-file", default="open_bounty_programs.json")
    args = parser.parse_args()

    programs = load_programs(args.programs_file)
    print(f"Priority scrape: {len(programs)} programs")
    print()

    for prog in programs:
        query = build_query(prog)
        print(f"  [{prog['repo']}] {prog.get('typical_range', '?')} {prog['currency']}")
        print(f"  Query: {query}")
        if args.dry_run:
            print(f"  [DRY RUN — skipping]\n")
            continue

        cmd = [
            sys.executable, "-m", "github_bounty_scraper",
            "--max-issues", str(args.max_issues),
            "scrape",
            "--query", query,
        ]
        print(f"  Running...", flush=True)
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode != 0:
            print(f"  WARNING: scrape returned exit code {result.returncode}")
        print()

    print("Priority scrape complete.")
    print("Run: venv/Scripts/python.exe scripts/rescore_all.py --model models/bounty_model_manual_v1.pkl --blend-ml 0.4")


if __name__ == "__main__":
    main()
