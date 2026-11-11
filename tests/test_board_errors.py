import asyncio

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server
from app.config import settings

WORKSTREAM = "7048f9ca-33ac-4055-aa8c-8c1f7b5541a7"


class FakeClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def request(self, *args, **kwargs):
        return self.response


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setattr(settings, "sazed_url", "http://sazed.test")

    def respond(status: int, payload: dict):
        client = FakeClient(httpx.Response(status, json=payload))
        monkeypatch.setattr(mcp_server, "get_client", lambda: client)

    return respond


def _file_card():
    return asyncio.run(mcp_server.board_file_card("A similar card", WORKSTREAM))


def test_duplicate_409_names_the_matched_card(upstream):
    upstream(
        409,
        {
            "detail": "Already in the backlog: Rules as data",
            "existing": {"id": "abc", "status": "shortlisted", "title": "Rules as data"},
        },
    )
    with pytest.raises(ToolError) as caught:
        _file_card()
    assert str(caught.value) == (
        "Already in the backlog: Rules as data "
        "(matched item:abc, status shortlisted, title 'Rules as data')"
    )


def test_validation_list_reads_as_one_clause_per_field(upstream):
    upstream(
        422,
        {"detail": [{"loc": ["body", "title"], "msg": "String should have at least 3 characters"}]},
    )
    with pytest.raises(ToolError, match="^title: String should have at least 3 characters$"):
        _file_card()


def test_created_card_reports_a_similar_parked_one(upstream):
    upstream(
        201,
        {
            "id": "new",
            "title": "A similar card",
            "status": "new",
            "summary": "dropped",
            "similar_parked": {"id": "old", "title": "Parked twin", "status": "parked", "why": "x"},
        },
    )
    assert _file_card() == {
        "id": "new",
        "title": "A similar card",
        "status": "new",
        "similar_parked": {"id": "old", "title": "Parked twin", "status": "parked"},
    }
