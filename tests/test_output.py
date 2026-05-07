"""Tests for output.py."""

import os
import tempfile

from github_bounty_scraper.output import write_json_output


def test_write_results():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        f.close()
        try:
            leads = [
                {
                    "Title": "Hello",
                    "Amount": "$100",
                    "Score": 1.0,
                    "Repo": "test/test",
                    "Link": "https://test",
                    "Labels": "[]",
                    "AmountNum": 100.0,
                    "Currency": "USD",
                    "HasOnchainEscrow": False,
                    "MentionsNoKyc": False,
                    "MentionsWalletPayout": False,
                    "PrevScore": None,
                }
            ]
            write_json_output(leads, 1.5, f.name)
            assert os.path.exists(f.name)
        finally:
            os.remove(f.name)
