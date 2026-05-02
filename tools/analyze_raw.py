import json
import sys
import os

RAW_FILE = "exploration_raw.jsonl"

def main():
    if not os.path.exists(RAW_FILE):
        print(f"No raw candidate file yet ({RAW_FILE} not found).")
        sys.exit(0)
        
    cands = []
    try:
        with open(RAW_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    cands.append(json.loads(line))
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    print(f"Total raw candidates: {len(cands)}")
    if not cands:
        return

    amt_gt_0 = sum(1 for c in cands if c.get("numeric_amount", -1) > 0)
    amt_0_or_missing = sum(1 for c in cands if c.get("numeric_amount", -1) <= 0)

    org_owned = sum(1 for c in cands if c.get("is_org_owner"))
    user_owned = sum(1 for c in cands if not c.get("is_org_owner"))
    user_single = sum(1 for c in cands if not c.get("is_org_owner") and c.get("contributors_count", 0) < 2)
    user_multi = sum(1 for c in cands if not c.get("is_org_owner") and c.get("contributors_count", 0) >= 2)

    print(f"Amount > 0: {amt_gt_0}")
    print(f"Amount <= 0 / -1: {amt_0_or_missing}")
    print(f"Org-owned: {org_owned}")
    print(f"User-owned: {user_owned} (Single: {user_single}, Multi: {user_multi})")

    print("\n--- Sample Raw Candidates ---")
    for c in cands[:10]:
        title = c.get("title", "").strip()
        url = c.get("url", "")
        print(f"- {title}\n  {url}")

if __name__ == "__main__":
    main()
