import sqlite3

conn = sqlite3.connect("bounty_stats.db")
spam_urls = [
    "https://github.com/sickn33/antigravity-awesome-skills/issues/550",
    "https://github.com/mlflow/mlflow/issues/22980",
]
for url in spam_urls:
    conn.execute("UPDATE issue_stats SET label=0, vibe_score=0 WHERE issue_url=?", (url,))
    print(f"Labeled negative: {url}")
conn.commit()

# Also check for any other faiyaz2139-del issues in DB
rows = conn.execute("SELECT issue_url FROM issue_stats WHERE issue_url LIKE '%faiyaz%'").fetchall()
if rows:
    print("Found additional faiyaz URLs:", rows)
    for row in rows:
        url = row[0]
        conn.execute("UPDATE issue_stats SET label=0, vibe_score=0 WHERE issue_url=?", (url,))
        print(f"Labeled additional negative: {url}")
    conn.commit()
conn.close()
