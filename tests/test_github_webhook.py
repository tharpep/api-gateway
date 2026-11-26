import pytest

from app.routers.webhooks import normalize_github


def _pr_payload(author: str, head_repo: dict | None) -> dict:
    return {
        "action": "opened",
        "number": 12,
        "repository": {"full_name": "tharpep/sazed"},
        "sender": {"login": author},
        "pull_request": {
            "number": 12,
            "title": "Link PRs to cards",
            "body": "Card: 3fa9c1b2",
            "html_url": "https://github.com/tharpep/sazed/pull/12",
            "draft": False,
            "merged": False,
            "user": {"login": author, "type": "User"},
            "head": {"ref": "feature/3fa9c1b2-link-prs", "repo": head_repo},
            "base": {"ref": "main", "repo": {"full_name": "tharpep/sazed"}},
        },
    }


def _expected(author: str, head_repo: str | None) -> dict:
    return {
        "event": "pull_request",
        "action": "opened",
        "repo": "tharpep/sazed",
        "number": 12,
        "is_pr": True,
        "sender": author,
        "author": author,
        "head_repo": head_repo,
        "title": "Link PRs to cards",
        "body": "Card: 3fa9c1b2",
        "branch": "feature/3fa9c1b2-link-prs",
        "draft": False,
        "merged": False,
        "url": "https://github.com/tharpep/sazed/pull/12",
        "path": None,
        "line": None,
    }


@pytest.mark.parametrize(
    "author, head_repo, forwarded",
    [
        ("tharpep", {"full_name": "tharpep/sazed"}, "tharpep/sazed"),
        ("stranger", {"full_name": "stranger/sazed"}, "stranger/sazed"),
        ("stranger", None, None),
    ],
    ids=["own-repo", "fork", "deleted-fork"],
)
def test_a_pull_request_event_forwards_its_author_and_head_repo(author, head_repo, forwarded):
    sent = normalize_github("pull_request", _pr_payload(author, head_repo))
    assert sent == _expected(author, forwarded)


def test_an_event_without_a_pull_request_object_sends_null_author_and_head_repo():
    payload = {
        "action": "created",
        "repository": {"full_name": "tharpep/sazed"},
        "sender": {"login": "tharpep"},
        "issue": {
            "number": 12,
            "title": "Link PRs to cards",
            "user": {"login": "stranger"},
            "pull_request": {"url": "https://api.github.com/repos/tharpep/sazed/pulls/12"},
        },
        "comment": {
            "body": "looks good",
            "html_url": "https://github.com/tharpep/sazed/pull/12#issuecomment-1",
        },
    }
    assert normalize_github("issue_comment", payload) == {
        "event": "issue_comment",
        "action": "created",
        "repo": "tharpep/sazed",
        "number": 12,
        "is_pr": True,
        "sender": "tharpep",
        "author": None,
        "head_repo": None,
        "title": "Link PRs to cards",
        "body": "looks good",
        "branch": None,
        "draft": False,
        "merged": False,
        "url": "https://github.com/tharpep/sazed/pull/12#issuecomment-1",
        "path": None,
        "line": None,
    }
