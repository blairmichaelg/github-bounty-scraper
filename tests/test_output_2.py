"""More output tests."""

import os
import tempfile

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.output import write_markdown_output, write_output, write_text_output


def test_write_text_output(capsys):
    verified = [
        {
            "Score": 1.0,
            "Amount": "$100",
            "AmountNum": 100.0,
            "Currency": "USD",
            "Repo": "test",
            "Title": "Hello",
            "Labels": "[]",
            "Link": "http",
            "HasOnchainEscrow": True,
            "MentionsWalletPayout": True,
            "MentionsNoKyc": True,
        }
    ]
    unknown = [
        {
            "Score": 1.0,
            "Amount": "$0",
            "AmountNum": -1.0,
            "Currency": "USD",
            "Repo": "test",
            "Title": "Hello",
            "Labels": "[]",
            "Link": "http",
            "HasOnchainEscrow": True,
            "MentionsWalletPayout": True,
            "MentionsNoKyc": True,
        }
    ]
    write_text_output(verified, unknown, 1.0)
    out, err = capsys.readouterr()
    assert "Hello" in out


def test_write_markdown_output():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.close()
        verified = [
            {
                "Score": 1.0,
                "Amount": "$100",
                "AmountNum": 100.0,
                "Currency": "USD",
                "Repo": "test",
                "Title": "Hello",
                "Labels": "[]",
                "Link": "http",
                "HasOnchainEscrow": True,
                "MentionsWalletPayout": True,
                "MentionsNoKyc": True,
                "PrevScore": 0.0,
            }
        ]
        unknown = [
            {
                "Score": 1.0,
                "Amount": "$0",
                "AmountNum": -1.0,
                "Currency": "USD",
                "Repo": "test",
                "Title": "Hello",
                "Labels": "[]",
                "Link": "http",
                "HasOnchainEscrow": True,
                "MentionsWalletPayout": True,
                "MentionsNoKyc": True,
            }
        ]
        write_markdown_output(verified, unknown, 1.0, f.name)
        assert os.path.exists(f.name)
        os.remove(f.name)


def test_write_output():
    config = ScraperConfig(output_format="text")
    verified = [
        {
            "Score": 1.0,
            "Amount": "$100",
            "AmountNum": 100.0,
            "Currency": "USD",
            "Repo": "test",
            "Title": "Hello",
            "Labels": "[]",
            "Link": "http",
            "HasOnchainEscrow": True,
            "MentionsWalletPayout": True,
            "MentionsNoKyc": True,
        }
    ]
    write_output(verified, 1.0, config)
