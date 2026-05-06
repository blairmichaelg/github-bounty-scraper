import csv, random
random.seed(42)

rows = list(csv.DictReader(open('bounty_dataset_v3.csv', encoding='utf-8')))
pos  = [r for r in rows if r['is_bounty'] == '1']
neg  = [r for r in rows if r['is_bounty'] == '0']
amb  = [r for r in rows if r['is_bounty'] == '']

# Add is_closed feature
for r in rows:
    r['is_closed'] = '1' if 'closed' in (r.get('lead_mode') or '').lower() else '0'

# Filter orphan rows from negatives (vibe=0, no title, no body)
neg_clean = [r for r in neg
             if (r.get('title') or '').strip()
             or (r.get('body_snippet') or '').strip()]
orphaned = len(neg) - len(neg_clean)

# Undersample negatives to 4:1 ratio max
TARGET_RATIO = 4
max_neg = len(pos) * TARGET_RATIO
neg_sampled = random.sample(neg_clean, min(max_neg, len(neg_clean)))

# Hard negatives: keep ALL mid-vibe (10-49) negatives regardless of sampling
mid_vibe_neg = [r for r in neg_clean
                if r.get('vibe_score','').strip()
                and 10 <= int(r['vibe_score']) <= 49]
# Merge (deduplicate by url)
kept_urls = {r['issue_url'] for r in neg_sampled}
for r in mid_vibe_neg:
    if r['issue_url'] not in kept_urls:
        neg_sampled.append(r)
        kept_urls.add(r['issue_url'])

final = pos + neg_sampled + amb
random.shuffle(final)

headers = list(rows[0].keys())
if 'is_closed' not in headers:
    headers.append('is_closed')

with open('bounty_dataset_train.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=headers)
    w.writeheader()
    w.writerows(final)

labeled_final = [r for r in final if r['is_bounty'] in ('0','1')]
p2 = sum(1 for r in labeled_final if r['is_bounty'] == '1')
n2 = sum(1 for r in labeled_final if r['is_bounty'] == '0')
print(f'Orphaned negatives removed : {orphaned}')
print(f'Positives                  : {p2}')
print(f'Negatives (sampled)        : {n2}')
print(f'  of which mid-vibe (10-49): {len(mid_vibe_neg)}')
print(f'Ambiguous (excluded)       : {len(amb)}')
print(f'Final training rows        : {len(final)}')
print(f'Imbalance ratio            : 1:{n2//max(p2,1)}')
print('Output: bounty_dataset_train.csv')
