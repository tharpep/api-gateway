import asyncio

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server
from app.config import settings

REVIEW = {
    "name": "review",
    "engine": "claude",
    "capability": "review",
    "model": "claude-sonnet-5-5",
    "effort": None,
    "budget_usd": None,
    "timeout_s": 900,
    "max_tool_calls": 150,
    "evidence_required": False,
    "description": "Job review",
    "mcp": {},
    "read_only": True,
    "version": 3,
}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return [REVIEW]
        return {**REVIEW, **(body or {})}

    monkeypatch.setattr(mcp_server, "_sazed", fake)
    return calls


def _save(**kwargs):
    return asyncio.run(mcp_server.profile_save(reason="test the save", **kwargs))


def test_partial_save_keeps_the_rows_other_fields(sent):
    """Only timeout_s changes: engine, capability, the tool cap and the null budget stay."""
    _save(name="review", timeout_s=1200)
    method, path, body = sent[-1]
    assert (method, path) == ("PUT", "/agents/profiles/review")
    assert body == {
        **{k: REVIEW[k] for k in mcp_server._PROFILE_FIELDS},
        "timeout_s": 1200,
        "author": "agent:claude",
        "reason": "test the save",
        "expected_version": 3,
    }


def test_none_clears_a_cap(sent):
    """The word none sends null, which Sazed reads as no cap."""
    _save(name="review", max_tool_calls="none")
    assert sent[-1][2]["max_tool_calls"] is None


def test_new_profile_sends_only_what_was_given(sent):
    """A new profile has no row to merge, so nothing is invented for it."""
    _save(
        name="gemini_scout",
        engine="agy",
        capability="research",
        model="gemini-3.8-flash-high",
        timeout_s=600,
        budget_usd="none",
    )
    assert sent[-1][2] == {
        "engine": "agy",
        "capability": "research",
        "model": "gemini-3.8-flash-high",
        "timeout_s": 600,
        "author": "agent:claude",
        "reason": "test the save",
    }


class RecordingClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.headers = None

    async def request(self, method, url, json=None, headers=None, timeout=None):
        self.headers = headers
        return self.response


def _upstream(monkeypatch, status, payload):
    monkeypatch.setattr(settings, "sazed_url", "http://sazed.test")
    client = RecordingClient(httpx.Response(status, json=payload))
    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    return client


def test_every_sazed_call_asks_for_shape_2(monkeypatch):
    """Without the header Sazed hides job profiles and puts the raw dispatch word in state."""
    client = _upstream(monkeypatch, 200, [REVIEW])
    asyncio.run(mcp_server.profile_list())
    assert client.headers["X-Sazed-Shape"] == "2"


def test_a_refused_save_reads_as_sazeds_message(monkeypatch):
    """A 409 from Sazed reaches the caller as its detail text."""
    refusal = "'review' is a job profile; its capability stays 'review'"
    _upstream(monkeypatch, 409, {"detail": refusal})
    with pytest.raises(ToolError) as caught:
        asyncio.run(mcp_server._sazed("PUT", "/agents/profiles/review", {}))
    assert str(caught.value) == refusal
