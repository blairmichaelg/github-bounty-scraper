"""Tests for core helpers."""

from github_bounty_scraper.config import ScraperConfig
from github_bounty_scraper.core import _build_text_context, _check_repo_health, _resolve_numeric_amount


def test_check_repo_health():
    config = ScraperConfig(min_stars=10)
    assert _check_repo_health({"stargazerCount": 5}, config) is False
    assert _check_repo_health({"stargazerCount": 20, "isArchived": True}, config) is False
    assert _check_repo_health({"stargazerCount": 20, "isDisabled": True}, config) is False
    assert _check_repo_health({"stargazerCount": 20, "isFork": True}, config) is False
    assert (
        _check_repo_health(
            {"stargazerCount": 20, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 1}}, config
        )
        is False
    )
    assert (
        _check_repo_health(
            {"stargazerCount": 20, "owner": {"__typename": "User"}, "mentionableUsers": {"totalCount": 5}}, config
        )
        is True
    )
    assert _check_repo_health({"stargazerCount": 20, "owner": {"__typename": "Organization"}}, config) is True


def test_build_text_context():
    issue = {"title": "Hello", "body": "World"}
    comments = [{"body": "Comment 1"}, {"body": "Comment 2"}]
    res = _build_text_context(issue, comments)
    assert res == "Hello World Comment 1 Comment 2"


def test_resolve_numeric_amount():
    config = ScraperConfig()
    issue = {"title": "Bounty", "body": "$500"}
    num, disp, cur = _resolve_numeric_amount(issue, config)
    assert num == 500.0

    issue = {"title": "Bounty", "body": "No money"}
    num, disp, cur = _resolve_numeric_amount(issue, config)
    assert num == -1.0
