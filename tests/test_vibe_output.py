import pytest
from github_bounty_scraper.output import write_markdown_output
from github_bounty_scraper.config import ScraperConfig

def test_markdown_vibe_arrows(tmp_path):
    """Verify that score deltas produce ↑ or ↓ arrows in Markdown output."""
    output_file = tmp_path / "test.md"
    
    verified = [
        {
            "Score": 80.0,
            "PrevScore": 70.0,
            "Amount": "$100.00",
            "Currency": "USD",
            "Repo": "owner/repo",
            "Title": "Increased Score",
            "Labels": "[]",
            "Link": "https://github.com/..."
        },
        {
            "Score": 60.0,
            "PrevScore": 75.0,
            "Amount": "$200.00",
            "Currency": "USD",
            "Repo": "owner/repo2",
            "Title": "Decreased Score",
            "Labels": "[]",
            "Link": "https://github.com/..."
        },
        {
            "Score": 50.0,
            "PrevScore": None,
            "Amount": "$50.00",
            "Currency": "USD",
            "Repo": "owner/repo3",
            "Title": "New Score",
            "Labels": "[]",
            "Link": "https://github.com/..."
        }
    ]
    
    write_markdown_output(verified, [], 1.0, str(output_file))
    
    content = output_file.read_text(encoding="utf-8")
    assert "↑ Increased Score" in content
    assert "↓ Decreased Score" in content
    assert "| New Score" in content  # No arrow
