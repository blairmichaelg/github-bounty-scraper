from github_bounty_scraper.core import _assemble_lead_result
from github_bounty_scraper.signals import SignalResult


def test_assemble_lead_result():
    issue = {
        "title": "Title",
        "html_url": "https://github.com/owner/repo/issues/1",
        "labels": {"nodes": [{"name": "label1"}]},
    }
    soft = SignalResult(has_onchain_escrow=True, mentions_no_kyc=False, mentions_wallet_payout=True)
    res = _assemble_lead_result(issue, 100.0, "$100", "USD", 1.0, 0.5, "owner/repo", soft)
    assert res["AmountNum"] == 100.0
    assert res["HasOnchainEscrow"] is True
    assert res["Labels"] == "[label1]"
