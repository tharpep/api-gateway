import asyncio
import re

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server
from app.config import settings
from app.mcp_server import mcp

READ = (True, None, None, False)
READ_OPEN = (True, None, None, True)
WRITE = (False, False, False, False)
WRITE_IDEMPOTENT = (False, False, True, False)
WRITE_OPEN = (False, False, False, True)
DESTRUCTIVE = (False, True, False, False)
DESTRUCTIVE_IDEMPOTENT = (False, True, True, False)

GROUPS = {
    READ: """homelab_overview homelab_storage homelab_services homelab_container show_card
        calendar_today journal_summary kb_search tasks_list job_status bakeoff_status
        learning_summary list_jobs board_list board_card board_command board_projects
        board_project plan_list plan_read plan_diff profile_list profile_read mcp_server_list
        vault_search vault_read vault_list vault_backlinks vault_unresolved vault_orphans
        vault_tags""",
    READ_OPEN: "multi_search",
    WRITE: """journal_add tasks_add delegate_code_task continue_job resume_job start_bakeoff
        promote_bakeoff board_file_card board_add_project board_add_workstream plan_save
        plan_create profile_save mcp_server_save vault_create vault_append vault_file_note""",
    WRITE_IDEMPOTENT: """homelab_start_container board_move_card board_update_card
        board_update_cards board_approve_plan board_start_card vault_set_property""",
    WRITE_OPEN: "send_to_phone research second_opinion kb_ingest_url",
    DESTRUCTIVE: "homelab_restart_container vault_edit",
    DESTRUCTIVE_IDEMPOTENT: """cancel_job vault_delete board_update_project
        board_update_workstream plan_update""",
}
EXPECTED = {name: hints for hints, names in GROUPS.items() for name in names.split()}


def _registered() -> dict[str, tuple]:
    tools = asyncio.run(mcp.list_tools())
    return {
        t.name: (
            t.annotations.readOnlyHint,
            t.annotations.destructiveHint,
            t.annotations.idempotentHint,
            t.annotations.openWorldHint,
        )
        for t in tools
    }


def test_tool_names_and_annotations_are_pinned():
    """Adding, renaming or re-annotating a tool must change this file in the same commit."""
    assert _registered() == EXPECTED


CARD_ID = "item:4b1e0c2a-33ac-4055-aa8c-8c1f7b5541a7"
PLAN_STATE_TOOLS = {"board_approve_plan": "approve-plan", "board_start_card": "start"}


class FakeClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.calls: list[tuple] = []

    async def request(self, method, url, json=None, **kwargs):
        self.calls.append((method, url, json))
        return self.response


@pytest.fixture
def sazed(monkeypatch):
    """Stubs Sazed's HTTP layer, so the real _sazed maps status codes to tool errors."""
    monkeypatch.setattr(settings, "sazed_url", "http://sazed.test")

    def respond(status: int, payload: dict) -> FakeClient:
        client = FakeClient(httpx.Response(status, json=payload))
        monkeypatch.setattr(mcp_server, "get_client", lambda: client)
        return client

    return respond


def _run(tool: str, card_id: str = CARD_ID, agent: str = "orchestrator"):
    return asyncio.run(getattr(mcp_server, tool)(card_id, agent))


@pytest.mark.parametrize(("tool", "route"), PLAN_STATE_TOOLS.items())
def test_plan_state_tools_post_the_bare_id_and_an_agent_actor(sazed, tool, route):
    card = {
        "id": CARD_ID,
        "title": "T",
        "stage": "next",
        "next": "Ready to start",
        "plan_approval": {"by": "agent:orchestrator"},
        "started": None,
        "history": [],
    }
    client = sazed(200, {"changed": True, "card": card})
    out = _run(tool, agent="Orchestrator")
    assert client.calls == [
        ("POST", f"http://sazed.test/backlog/{CARD_ID[5:]}/{route}", {"by": "agent:orchestrator"})
    ]
    assert out == {"changed": True, "card": {k: card[k] for k in card if k != "history"}}


AGENT_INPUTS = [
    "pryce",
    "PRYCE",
    "Pryce",
    " pryce",
    "pryce ",
    ":pryce",
    "agent:pryce",
    "x:pryce",
    "",
    ":",
    "-x",
    ".x",
    "_x",
    "a" * 65,
    "a b",
    "a/b",
    "\u00e9",
    "\u0440ryce",
    "\uff10",
    "\u212aelvin",
    "pryce\n",
    "\x00",
    "orch",
    "a" * 64,
    "o.r_c-h9",
]


@pytest.mark.parametrize("agent", AGENT_INPUTS)
def test_no_agent_value_yields_pryce(sazed, agent):
    """Every input either fails before Sazed is called or sends agent:<name> in Sazed's alphabet."""
    client = sazed(200, {"changed": False, "card": {}})
    for tool in PLAN_STATE_TOOLS:
        try:
            _run(tool, agent=agent)
        except ToolError:
            continue
        by = client.calls[-1][2]["by"]
        assert by != "pryce"
        assert re.fullmatch(r"agent:[a-z0-9][a-z0-9._-]{0,63}", by, re.ASCII)


@pytest.mark.parametrize("agent", ["a:b", "Orch Bot", "", "\u212aelvin", "\u00e9", "a" * 65, "-x"])
def test_a_bad_agent_is_rejected_before_sazed_is_called(sazed, agent):
    client = sazed(200, {"changed": False, "card": {}})
    with pytest.raises(ToolError, match="^agent must be"):
        _run("board_approve_plan", agent=agent)
    assert client.calls == []


def test_an_uppercase_agent_is_lowercased(sazed):
    client = sazed(200, {"changed": False, "card": {}})
    _run("board_start_card", agent="Orch.Bot-2")
    assert client.calls[0][2] == {"by": "agent:orch.bot-2"}


SAZED_ERRORS = [
    ("board_approve_plan", 409, "the card is still in the inbox; Pryce must triage it first"),
    ("board_approve_plan", 409, "item is parked; restore it first"),
    ("board_approve_plan", 404, "Item not found"),
    ("board_start_card", 409, "the plan is not approved; approve it first"),
    (
        "board_start_card",
        409,
        "the card's plan or section changed since approval; approve it again",
    ),
    ("board_start_card", 409, "plan board-visibility is done; nothing to build"),
    ("board_start_card", 403, "only pryce can withdraw an approval or a start"),
]


@pytest.mark.parametrize(("tool", "status", "detail"), SAZED_ERRORS)
def test_sazed_refusals_reach_the_model_unchanged(sazed, tool, status, detail):
    sazed(status, {"detail": detail})
    with pytest.raises(ToolError) as caught:
        _run(tool)
    assert str(caught.value) == detail


def test_sazed_422_reads_as_a_field_clause(sazed):
    sazed(422, {"detail": [{"loc": ["body", "by"], "msg": "String should match pattern"}]})
    with pytest.raises(ToolError, match="^by: String should match pattern$"):
        _run("board_start_card")


def test_a_job_card_is_refused(sazed):
    client = sazed(200, {"changed": False, "card": {}})
    with pytest.raises(ToolError, match="job cards"):
        _run("board_approve_plan", card_id="job:abc")
    assert client.calls == []


UUID = "11111111-2222-3333-4444-555555555555"
CRAFTED = [
    f"../../jobs/{UUID}/approve?",
    f"../../jobs/{UUID}/approve#",
    f"../jobs/{UUID}/cancel?x=",
    "x/../../../sessions",
    "..",
    ".",
    "%2e%2e",
    f"%2e%2e%2fjobs%2f{UUID}%2fapprove",
    f"{UUID}%2f..%2f..%2fjobs",
    f"{UUID}/../../jobs/{UUID}/approve",
    f"{UUID}?x=1",
    f"{UUID}#x",
    f"{UUID}%0a",
    f"{UUID}\n",
    f"{UUID}\\..\\..",
    f"http://evil.test/jobs/{UUID}/approve",
    f"//evil.test/{UUID}",
    "",
]

# Each tool that puts an argument in a Sazed URL: how to call it with a crafted value, and the
# only paths it may send to.
ID_TOOLS = {
    "board_approve_plan": (
        lambda v: {"card_id": f"item:{v}", "agent": "orch"},
        rf"/backlog/{UUID}/approve-plan",
    ),
    "board_start_card": (
        lambda v: {"card_id": f"item:{v}", "agent": "orch"},
        rf"/backlog/{UUID}/start",
    ),
    "board_update_card": (lambda v: {"card_id": f"item:{v}", "note": "n"}, rf"/backlog/{UUID}"),
    "board_move_card": (lambda v: {"card_id": v, "workstream_id": None}, rf"/backlog/{UUID}"),
    "board_card": (lambda v: {"card_id": f"job:{v}"}, rf"/board/job:{UUID}"),
    "board_update_workstream": (
        lambda v: {"workstream_id": v, "title": "t"},
        rf"/board/workstreams/{UUID}",
    ),
    "board_project": (lambda v: {"slug": v}, r"/board/projects/ok-slug"),
    "board_update_project": (lambda v: {"slug": v, "title": "t"}, r"/board/projects/ok-slug"),
    "board_add_workstream": (
        lambda v: {"slug": v, "title": "t"},
        r"/board/projects/ok-slug/workstreams",
    ),
    "continue_job": (lambda v: {"job_id": v, "instruction": "go"}, rf"/jobs/{UUID}(/continue)?"),
    "resume_job": (lambda v: {"job_id": v}, rf"/jobs/{UUID}(/resume)?"),
    "job_status": (lambda v: {"job_id": v}, rf"/jobs/{UUID}"),
    "cancel_job": (lambda v: {"job_id": v}, rf"/jobs/{UUID}/cancel"),
    "bakeoff_status": (lambda v: {"bakeoff_id": v}, r"/bakeoffs/0a1b2c3d"),
    "promote_bakeoff": (lambda v: {"bakeoff_id": v, "arm": "opus"}, r"/bakeoffs/0a1b2c3d/promote"),
    "plan_list": (lambda v: {"project": v}, r"/board/plans"),
    "plan_read": (lambda v: {"slug": v}, r"/board/plans/ok-slug"),
    "plan_save": (
        lambda v: {"slug": v, "body": "b", "reason": "r", "expected_version": 1},
        r"/board/plans/ok-slug/versions",
    ),
    "plan_diff": (lambda v: {"slug": v, "a": 1, "b": 2}, r"/board/plans/ok-slug/diff"),
    "plan_update": (lambda v: {"slug": v, "title": "t"}, r"/board/plans/ok-slug"),
    "profile_read": (lambda v: {"name": v}, r"/agents/profiles/ok-name"),
    "profile_save": (lambda v: {"name": v, "reason": "r"}, r"/agents/profiles(/ok-name)?"),
    "mcp_server_save": (
        lambda v: {"name": v, "transport": "http", "reason": "r"},
        r"/agents/mcp-servers/ok-name",
    ),
}
GOOD = {
    "board_project": "ok-slug",
    "board_update_project": "ok-slug",
    "board_add_workstream": "ok-slug",
    "plan_list": "ok-slug",
    "plan_read": "ok-slug",
    "plan_save": "ok-slug",
    "plan_diff": "ok-slug",
    "plan_update": "ok-slug",
    "profile_read": "ok-name",
    "profile_save": "ok-name",
    "mcp_server_save": "ok-name",
    "bakeoff_status": "0a1b2c3d",
    "promote_bakeoff": "0a1b2c3d",
}


@pytest.fixture
def transport(monkeypatch):
    """A real httpx client on a mock transport, so URL normalisation (../, ?, #) is httpx's own."""
    monkeypatch.setattr(settings, "sazed_url", "http://sazed.test")
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/agents/profiles":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": UUID, "status": "queued", "kind": "x", "card": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    monkeypatch.setattr(mcp_server, "_WAIT_SECONDS", 0)
    return seen


def _call_tool(tool: str, args: dict):
    try:
        asyncio.run(mcp.call_tool(tool, args))
    except ToolError:
        pass


@pytest.mark.parametrize("tool", ID_TOOLS)
@pytest.mark.parametrize("crafted", CRAFTED)
def test_a_crafted_id_reaches_no_other_route(transport, tool, crafted):
    """No id, however crafted, sends a request to any path but the tool's own, so no board
    tool can reach a job's approve or cancel route."""
    build, own = ID_TOOLS[tool]
    _call_tool(tool, build(crafted))
    for request in transport:
        assert request.url.host == "sazed.test"
        assert re.fullmatch(own, request.url.path), (tool, crafted, str(request.url))
        assert "approve" not in request.url.query.decode() and request.url.fragment == ""


@pytest.mark.parametrize("tool", ID_TOOLS)
def test_a_real_id_reaches_the_tools_own_route(transport, tool):
    build, own = ID_TOOLS[tool]
    asyncio.run(mcp.call_tool(tool, build(GOOD.get(tool, UUID))))
    assert transport and all(re.fullmatch(own, r.url.path) for r in transport)


def test_no_tool_withdraws():
    """Withdraw is Pryce's alone in the console."""
    assert not {n for n in _registered() if "withdraw" in n}


def test_an_empty_5xx_reads_as_a_line(monkeypatch):
    monkeypatch.setattr(settings, "sazed_url", "http://sazed.test")
    monkeypatch.setattr(mcp_server, "get_client", lambda: FakeClient(httpx.Response(503)))
    with pytest.raises(ToolError, match="^Sazed answered 503 with no detail$"):
        _run("board_start_card")


def test_server_instructions_fit_the_cap():
    assert len(mcp.instructions) <= 4096
