from github_bounty_scraper.vibe import _gemini_endpoint, parse_vibe_output


def test_parse_vibe_output():
    text = "SCORE: 85\nREASON: This is a good one."
    score, reason = parse_vibe_output(text)
    assert score == 85
    assert "This is a good one" in reason

    text = "Nothing here"
    score, reason = parse_vibe_output(text)
    assert score == 0
    assert reason == "Nothing here"


def test_gemini_endpoint():
    url = _gemini_endpoint("gemini-1.5-flash")
    assert "gemini-1.5-flash:generateContent" in url
