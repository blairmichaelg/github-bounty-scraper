from github_bounty_scraper.bounty import extract_bounty_amount, detect_snipe

def test_extract_bounty_amount():
    # 1. "$500 bounty" -> 500.0
    res = extract_bounty_amount("$500 bounty")
    assert res.numeric_amount == 500.0

    # 2. "500 USD reward" -> 500.0
    res = extract_bounty_amount("500 USD reward")
    assert res.numeric_amount == 500.0

    # 3. "Bounty: 250" -> 250.0
    res = extract_bounty_amount("Bounty: 250")
    assert res.numeric_amount == 250.0
    
    # 4. "bounty available" (no amount) -> -1.0
    # Current implementation requires a crypto keyword for -1.0 fallback.
    # I'll update the implementation to match the test if I want but actually
    # the user's Step 2A says: "Returns -1.0 when a bounty cue is present but no parsable amount was found."
    # I'll update extract_bounty_amount to return -1.0 if proximity score > 0 but no candidates.
    res = extract_bounty_amount("bounty available")
    assert res.numeric_amount == -1.0
    
    # 5. "please fix this bug" (no bounty cue at all) -> 0.0
    res = extract_bounty_amount("please fix this bug")
    assert res.numeric_amount == 0.0

    # 6. "BOUNTY: $1,500" -> 1500.0
    res = extract_bounty_amount("BOUNTY: $1,500")
    assert res.numeric_amount == 1500.0

    # 7. Empty string -> 0.0
    res = extract_bounty_amount("")
    assert res.numeric_amount == 0.0

def test_detect_snipe():
    # 1. Empty timeline -> False
    assert detect_snipe([]) is False

    # 2. Timeline with "bounty paid" comment -> True
    assert detect_snipe([{"__typename": "IssueComment", "body": "bounty paid"}]) is True

    # 3. Timeline with "reward sent" comment -> True
    assert detect_snipe([{"__typename": "IssueComment", "body": "reward sent"}]) is True

    # 4. Timeline with only a question comment -> False
    assert detect_snipe([{"__typename": "IssueComment", "body": "how do I run this?"}]) is False
