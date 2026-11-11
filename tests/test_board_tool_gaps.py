import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server

CARD = "item:7048f9ca-33ac-4055-aa8c-8c1f7b5541a7"
WORKSTREAM = "0b6c5a0e-1f0e-4f6e-9a39-7d8d9a3c1d11"


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "7048f9ca", "title": "A card", "status": "new"}

    monkeypatch.setattr(mcp_server, "_sazed", fake)
    return calls


def test_update_sends_effort_and_impact(sent):
    asyncio.run(mcp_server.board_update_card(CARD, effort="M", impact="high"))
    assert sent[0][2] == {"effort": "M", "impact": "high"}


def test_file_card_sends_rank_only_when_given(sent):
    asyncio.run(mcp_server.board_file_card("A new card", WORKSTREAM, rank=1))
    asyncio.run(mcp_server.board_file_card("Another card", WORKSTREAM))
    assert sent[0][2]["rank"] == 1
    assert "rank" not in sent[1][2]


def test_board_list_returns_each_cards_rank(monkeypatch):
    async def fake(method, path, body=None):
        return {"counts": {}, "cards": [{"id": "item:a", "title": "A", "stage": "next", "rank": 3}]}

    monkeypatch.setattr(mcp_server, "_sazed", fake)
    out = asyncio.run(mcp_server.board_list())
    assert out["cards"][0]["rank"] == 3


def test_board_project_returns_each_cards_rank(monkeypatch):
    project = {
        "slug": "p",
        "workstreams": [
            {"id": "w", "cards": [{"id": "item:a", "title": "A", "stage": "next", "rank": 2}]}
        ],
    }

    async def fake(method, path, body=None):
        return project

    monkeypatch.setattr(mcp_server, "_sazed", fake)
    out = asyncio.run(mcp_server.board_project("p"))
    assert out["steps"][0]["cards"] == [{"id": "item:a", "title": "A", "stage": "next", "rank": 2}]


def test_batch_applies_entries_in_order_through_the_single_card_path(sent):
    out = asyncio.run(
        mcp_server.board_update_cards(
            [{"card_id": CARD, "rank": 1}, {"card_id": CARD, "effort": "S", "note": "n"}]
        )
    )
    assert out == {"results": [{"id": CARD, "ok": True}, {"id": CARD, "ok": True}]}
    assert [c[2] for c in sent] == [{"rank": 1}, {"effort": "S", "note": "n"}]
    assert {c[:2] for c in sent} == {("PATCH", "/backlog/7048f9ca-33ac-4055-aa8c-8c1f7b5541a7")}


def test_one_failed_entry_does_not_stop_the_rest(monkeypatch):
    calls = []

    async def fake(method, path, body=None):
        calls.append(path)
        if "bad" in path:
            raise ToolError("Item not found")
        return {"id": "x", "title": "t", "status": "new"}

    monkeypatch.setattr(mcp_server, "_sazed", fake)
    out = asyncio.run(
        mcp_server.board_update_cards(
            [
                {"card_id": CARD, "note": "a"},
                {"card_id": "item:bad", "note": "b"},
                {"note": "no card id"},
                {"card_id": "job:123", "note": "c"},
                {"card_id": CARD, "note": "d"},
            ]
        )
    )
    results = out["results"]
    assert [r["ok"] for r in results] == [True, False, False, False, True]
    assert results[1] == {"id": "item:bad", "ok": False, "error": "Item not found"}
    assert results[2]["id"] is None and "card_id" in results[2]["error"]
    assert "job cards" in results[3]["error"]
    assert len(calls) == 3


def test_batch_cap_is_fifty(sent):
    entries = [{"card_id": CARD, "note": "n"}] * 50
    assert len(asyncio.run(mcp_server.board_update_cards(entries))["results"]) == 50
    with pytest.raises(ToolError, match="At most 50"):
        asyncio.run(mcp_server.board_update_cards(entries + [entries[0]]))
    assert len(sent) == 50
