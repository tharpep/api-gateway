import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server
from app.routers.webhooks import normalize_github

CARD = "item:7048f9ca-33ac-4055-aa8c-8c1f7b5541a7"


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "7048f9ca", "title": "A card", "status": "new"}

    monkeypatch.setattr(mcp_server, "_sazed", fake)
    return calls


def _update(**kw):
    return asyncio.run(mcp_server.board_update_card(CARD, **kw))


def test_the_session_branch_and_pr_go_as_one_link(sent):
    _update(session="cc-7", branch="feature/x", pr="tharpep/sazed#12")
    link = {"session": "cc-7", "branch": "feature/x", "pr": "tharpep/sazed#12"}
    assert sent[0][2] == {"link": link}


def test_unlink_sends_a_null_link(sent):
    _update(unlink_session=True)
    assert sent[0][2] == {"link": None}


def test_a_call_without_link_fields_sends_no_link(sent):
    _update(note="ok")
    assert sent[0][2] == {"note": "ok"}


def test_unlink_cannot_be_combined_with_a_link(sent):
    with pytest.raises(ToolError):
        _update(session="cc-7", unlink_session=True)
    assert sent == []


@pytest.mark.parametrize("merged, expected", [(True, True), (False, False)])
def test_the_forwarded_event_says_whether_the_pr_merged(merged, expected):
    payload = {
        "action": "closed",
        "repository": {"full_name": "tharpep/sazed"},
        "pull_request": {"number": 12, "merged": merged},
    }
    assert normalize_github("pull_request", payload)["merged"] is expected
