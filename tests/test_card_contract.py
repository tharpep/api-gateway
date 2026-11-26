"""Every tool that draws a card returns what that card's schema accepts.

tests/card_schemas.json is a copy of sazed-frontend's src/cards/card-schemas.json, which a frontend
test regenerates from the Zod widget catalog. Copy it here whenever it changes.
"""

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server
from app.mcp_server import mcp
from app.routers import calendar, homelab, services

SCHEMAS = json.loads((Path(__file__).parent / "card_schemas.json").read_text(encoding="utf-8"))

PROGRESS = {"done": 1, "in_flight": 1, "needs_me": 0, "next": 2, "total": 4}
BAR = {"done": 0.25, "in_flight": 0.25, "needs_me": 0.0, "next": 0.5}

CARD = {
    "id": "item:1",
    "kind": "item",
    "source": "backlog",
    "title": "Write the parser",
    "stage": "doing",
    "next": "Waiting on CI",
    "updated_at": "2026-11-20T10:00:00Z",
    "done_at": None,
    "session_link": None,
    "pr": {
        "repo": "owner/repo",
        "number": 7,
        "url": "https://github.com/owner/repo/pull/7",
        "state": "open",
        "checks": {
            "state": "passing",
            "passed": 2,
            "total": 2,
            "runs": [{"name": "ci", "conclusion": "success"}],
        },
        "review": None,
        "additions": 10,
        "deletions": 2,
        "merged_at": None,
        "fetched_at": "2026-11-20T10:00:00Z",
    },
    "repo": "owner/repo",
    "item_type": "feature",
    "effort": "M",
    "impact": "high",
    "rank": 1,
    "workstream_id": "w1",
    "actions": [],
    "job_id": None,
    "job_status": None,
    "ci_state": "passing",
    "fix_count": None,
    "pr_number": 7,
    "pr_url": "https://github.com/owner/repo/pull/7",
    "pr_draft": False,
    "plan_id": None,
    "plan_section": None,
    "plan_version": None,
    "plan_approval": {
        "at": "2026-11-25T18:40:02.118000+00:00",
        "by": "agent:orchestrator",
        "plan": "parser",
        "version": 3,
        "section": "build",
        "changed_since": False,
    },
    "started": {"at": "2026-11-25T18:52:10.004000+00:00", "by": "pryce"},
}


def _workstream(cards: bool) -> dict:
    step = {
        "id": "w1",
        "title": "Parser",
        "summary": "Parse the input",
        "position": 1,
        "state": "open",
        "status": "in_flight",
        "depends_on": [],
        "progress": PROGRESS,
        "bar": BAR,
    }
    return {**step, "cards": [CARD]} if cards else step


def _project(cards: bool) -> dict:
    return {
        "id": "p1",
        "slug": "parser",
        "title": "Parser",
        "summary": "A parser",
        "state": "active",
        "rank": 1,
        "repos": ["owner/repo"],
        "plan": None,
        "latest_update": "Parser half done.",
        "latest_update_at": "2026-11-20T10:00:00Z",
        "updated_at": "2026-11-20T10:00:00Z",
        "headline": "Step 1 of 1",
        "progress": PROGRESS,
        "bar": BAR,
        "steps_done": 0,
        "steps_total": 1,
        "current": {"id": "w1", "title": "Parser", "status": "in_flight"},
        "workstreams": [_workstream(cards)],
    }


JOB = {
    "id": "j1",
    "status": "succeeded",
    "kind": "code_task",
    "model": "claude-opus-5-5",
    "title": "Fix the parser",
    "result": "Done.",
    "error": None,
    "meta": {"pr_url": "https://github.com/owner/repo/pull/7", "ci_state": "passing"},
    "recent_steps": ["read files"],
}

JOBS = {
    "jobs": [
        {
            "id": "j1",
            "kind": "research",
            "status": "running",
            "title": "Look into parsers",
            "created_at": "2026-11-20T10:00:00Z",
            "result_preview": None,
            "error": None,
        }
    ]
}

SAZED = {
    "/board/projects": {"projects": [_project(cards=False)]},
    "/board/projects/parser": _project(cards=True),
    "/jobs/code-task": {**JOB, "status": "needs_approval", "result": None},
    "/jobs/5d2c1f0e-0000-4000-8000-000000000001?events=false": JOB,
    "/jobs?limit=10": JOBS,
}

STORAGE = {
    "pools": [
        {"name": "local-lvm", "type": "lvmthin", "used_bytes": 1, "total_bytes": 4, "free_bytes": 3}
    ],
    "media": {
        "vmid": 100,
        "free_bytes": 3,
        "series": {"count": 1, "bytes": 1},
        "movies": {"count": 0, "bytes": 0},
        "downloads": {"torrents": 2, "bytes": 2, "not_imported": {"count": 1, "bytes": 1}},
    },
    "containers": [],
    "errors": [],
}

CONTAINER = {
    "vmid": 104,
    "name": "api-gateway",
    "status": "running",
    "cpu_percent": 0.5,
    "mem_used_bytes": 1,
    "mem_total_bytes": 2,
    "disk_used_bytes": 1,
    "disk_total_bytes": 2,
    "recent_lifecycle": [],
}

SERVICES = {
    "summary": {"up": 1, "down": 1, "unknown": 0},
    "last_cycle_at": "2026-11-20T10:00:00Z",
    "services": [
        {"id": "a", "name": "A", "group": "apps", "up": True, "in_maintenance": False},
        {"id": "b", "name": "B", "group": "apps", "up": False, "in_maintenance": False},
    ],
}

# One or more calls per card tool. A new card tool fails test_every_card_tool_has_a_sample.
SAMPLES = {
    "homelab_storage": [{}],
    "homelab_services": [{}],
    "homelab_container": [{"vmid": 104}],
    "calendar_today": [{}],
    "show_card": [
        {
            "title": "Week",
            "stats": [{"label": "PRs", "value": "4"}] * 4,
            "rows": [{"label": "Same label", "value": "x", "tone": "attention"}] * 6,
            "note": "A note",
        }
    ],
    "delegate_code_task": [{"repo": "owner/repo", "instruction": "Fix it"}],
    "job_status": [{"job_id": "5d2c1f0e-0000-4000-8000-000000000001"}],
    "list_jobs": [{}],
    "board_projects": [{}, {"detail": "full"}],
    "board_project": [{"slug": "parser"}, {"slug": "parser", "detail": "full"}],
}


@pytest.fixture(autouse=True)
def upstreams(monkeypatch):
    async def sazed(method, path, body=None):
        return SAZED[path]

    async def value(v):
        return v

    monkeypatch.setattr(mcp_server, "_sazed", sazed)
    monkeypatch.setattr(homelab, "storage", lambda: value(STORAGE))
    monkeypatch.setattr(homelab, "get_container", lambda vmid, task_limit: value(CONTAINER))
    monkeypatch.setattr(services, "list_services", lambda group, compact: value(SERVICES))
    today = calendar.CalendarResponse(
        events=[
            calendar.CalendarEvent(
                id="e1",
                title="Standup",
                start="2026-11-20T09:00:00-05:00",
                end="2026-11-20T09:15:00-05:00",
            )
        ],
        count=1,
    )
    monkeypatch.setattr(calendar, "get_today", lambda: value(today))


def _card_tools() -> dict[str, str]:
    tools = asyncio.run(mcp.list_tools())
    return {
        t.name: t.meta["ui"]["resourceUri"].removeprefix("ui://sazed/").removesuffix(".html")
        for t in tools
        if t.meta and "ui" in t.meta
    }


def test_every_card_tool_has_a_sample_and_a_schema():
    """A tool pointed at a card needs a sample call here and a schema in card_schemas.json."""
    cards = _card_tools()
    assert set(cards) == set(SAMPLES)
    assert set(cards.values()) <= set(SCHEMAS)


@pytest.mark.parametrize(
    ("tool", "args"), [(tool, args) for tool, calls in SAMPLES.items() for args in calls]
)
def test_tool_output_matches_its_card_schema(tool, args):
    """The structured result of a card tool validates against that card's JSON Schema."""
    structured = asyncio.run(mcp.call_tool(tool, args)).structuredContent
    assert _schema_errors(_card_tools()[tool], structured) == []


def _schema_errors(widget: str, structured: dict) -> list[str]:
    return [
        f"{'.'.join(map(str, e.absolute_path)) or 'props'}: {e.message}"
        for e in Draft202012Validator(SCHEMAS[widget]).iter_errors(structured)
    ]


def test_full_project_list_is_bounded(monkeypatch):
    """Past 100,000 characters, the top-ranked active projects stay full and the rest become
    briefs, with a note; the mixed list still validates against the card schema."""
    board = [
        {**_project(cards=False), "slug": f"p{i}", "rank": i, "summary": "x" * 4000}
        for i in range(1, 41)
    ]
    board[0]["state"] = "paused"

    async def sazed(method, path, body=None):
        return {"projects": board}

    monkeypatch.setattr(mcp_server, "_sazed", sazed)
    result = asyncio.run(mcp.call_tool("board_projects", {"detail": "full"}))
    out = result.structuredContent
    assert len(result.content[0].text) * 2 <= 100_000
    full = [p["slug"] for p in out["projects"] if "workstreams" in p]
    shortened = [p["slug"] for p in out["projects"] if "steps" in p]
    assert full == [f"p{i}" for i in range(2, 2 + len(full))]
    assert "p1" in shortened
    assert len(full) + len(shortened) == 40
    assert out["truncated"].startswith(f"{len(shortened)} of 40 projects")
    assert "board_project" in out["truncated"]
    assert _schema_errors("ProjectsWidget", out) == []


def test_small_full_project_list_is_untouched():
    """Under the bound, detail=full returns Sazed's list as it is, with no note."""
    out = asyncio.run(mcp.call_tool("board_projects", {"detail": "full"})).structuredContent
    assert out == SAZED["/board/projects"]


@pytest.mark.parametrize("tool", list(SAMPLES))
def test_card_text_is_compact_json_of_the_structured_result(tool):
    """The text copy carries the same fields as structuredContent, without indentation."""
    result = asyncio.run(mcp.call_tool(tool, SAMPLES[tool][0]))
    text = result.content[0].text
    assert json.loads(text) == result.structuredContent
    assert text == json.dumps(result.structuredContent, separators=(",", ":"), ensure_ascii=False)


@pytest.mark.parametrize(("field", "limit"), [("stats", 4), ("rows", 6)])
def test_show_card_refuses_more_than_the_card_draws(field, limit):
    """Past the card's limit the call fails with a reason, instead of the card dropping rows."""
    entry = {"label": "x", "value": "1"}
    with pytest.raises(ToolError, match=field):
        asyncio.run(mcp.call_tool("show_card", {"title": "t", field: [entry] * (limit + 1)}))
