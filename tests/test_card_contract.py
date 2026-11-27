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

CARD_UUID = "4b1e0c2a-33ac-4055-aa8c-8c1f7b5541a7"
JOB_UUID = "5d2c1f0e-0000-4000-8000-000000000002"

# A card with no PR: what 228 of 267 live cards look like.
NO_PR_CARD = {
    **CARD,
    "id": f"job:{JOB_UUID}",
    "kind": "job",
    "stage": "needs_me",
    "next": "Answer its question",
    "pr": None,
    "pr_url": None,
    "pr_number": None,
    "repo": None,
    "job_id": JOB_UUID,
    "job_status": "needs_input",
    "question": "Which schema wins?",
}

EMPTY_COMMAND = {
    "needs": [],
    "working": [],
    "done": {"succeeded": 2, "failed": 0, "latest": [], "window_hours": 24},
    "sessions": [],
    "session_health": {"silent_after_s": 900, "last_heard_at": None, "stale_card_ids": []},
    "plans_to_approve": [],
    "ready_to_start": 0,
}


def _merge_call(i: int) -> dict:
    pr = {**CARD["pr"], "number": i, "url": f"https://github.com/owner/repo/pull/{i}"}
    return {
        **CARD,
        "id": f"item:{i}",
        "stage": "needs_me",
        "next": "Ready to merge",
        "summary": "s" * 600,
        "why": "w" * 150,
        "note": "n" * 450,
        "pr": pr,
    }


DISPATCH_QUESTION = {
    "id": "dispatch:notes-v1:sheet",
    "kind": "dispatch",
    "title": "sheet: show where a note is filed",
    "repo": "owner/repo",
    "next": "Answer its question",
    "question": "q" * 5000,
    "updated_at": "2026-11-26T12:00:00Z",
}

WORKING = [
    {
        "kind": "job",
        "title": "Fix the parser",
        "status": "running",
        "steps": ["a", "b"],
        "tools": 3,
        "elapsed_s": 120,
        "timeout_s": 1800,
    },
    {
        "kind": "dispatch",
        "plan": "notes-v1",
        "task": "sheet",
        "title": "sheet: x",
        "tools": 6,
        "elapsed_s": 76,
        "line": "sonnet / high",
        "heartbeat_at": "2026-11-26T12:00:00Z",
    },
]

COMMANDS = {
    "empty": EMPTY_COMMAND,
    "merge_calls": {
        **EMPTY_COMMAND,
        "needs": [_merge_call(i) for i in range(1, 15)],
        "working": WORKING,
        "sessions": [{"session_id": "s1"}],
    },
    "question": {**EMPTY_COMMAND, "needs": [_merge_call(1), DISPATCH_QUESTION, NO_PR_CARD]},
    "oversized": {
        **EMPTY_COMMAND,
        "needs": [
            {**_merge_call(i), "title": "t" * 300 + "x" * 700, "question": "q" * 9000}
            for i in range(1, 121)
        ],
        "working": WORKING * 40,
    },
}

SAZED = {
    "/board/command": EMPTY_COMMAND,
    f"/board/item:{CARD_UUID}": {**CARD, "id": f"item:{CARD_UUID}"},
    f"/board/job:{JOB_UUID}": NO_PR_CARD,
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
    "board_command": [{}],
    "board_card": [{"card_id": f"item:{CARD_UUID}"}, {"card_id": f"job:{JOB_UUID}"}],
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


def _command(monkeypatch, payload: dict) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def sazed(method, path, body=None):
        calls.append((method, path))
        return payload

    monkeypatch.setattr(mcp_server, "_sazed", sazed)
    return calls


@pytest.mark.parametrize("sample", list(COMMANDS))
def test_board_command_validates_and_only_reads(monkeypatch, sample):
    """Every board_command sample validates as a Needs you card, from one GET of /board/command."""
    calls = _command(monkeypatch, COMMANDS[sample])
    result = asyncio.run(mcp.call_tool("board_command", {}))
    assert calls == [("GET", "/board/command")]
    assert _schema_errors("NeedsYouWidget", result.structuredContent) == []


def test_board_command_is_bounded(monkeypatch):
    """120 needs with long text stay under 100,000 characters across both copies, questions
    are cut to 5,000 characters, and needs_total still counts every need."""
    _command(monkeypatch, COMMANDS["oversized"])
    result = asyncio.run(mcp.call_tool("board_command", {}))
    out = result.structuredContent
    assert len(result.content[0].text) * 2 <= 100_000
    assert 0 < len(out["needs"]) < 50
    assert out["needs_total"] == 120
    assert all(len(n["title"]) == 300 and len(n["question"]) == 5000 for n in out["needs"])
    assert all(n["title"].endswith("…") for n in out["needs"])
    assert len(out["working"]) == 50


def test_board_command_trims_needs_to_card_fields(monkeypatch):
    """A need keeps only what the card draws, questions come first, and PR checks carry no runs."""
    _command(monkeypatch, COMMANDS["question"])
    out = asyncio.run(mcp.call_tool("board_command", {})).structuredContent
    assert [n["id"] for n in out["needs"]] == [DISPATCH_QUESTION["id"], NO_PR_CARD["id"], "item:1"]
    merge = out["needs"][2]
    assert "summary" not in merge and "note" not in merge
    assert merge["pr"]["checks"] == {"state": "passing", "passed": 2, "total": 2}
    assert merge["pr"]["review"] is None
    assert out["session_count"] == 0
    assert out["open_url"] == "https://sazed.tail77c590.ts.net/?page=home"


def test_board_card_links_into_the_console():
    """An item card opens as ?open=backlog:<uuid>, a job card as ?open=job:<uuid>."""
    item = asyncio.run(mcp.call_tool("board_card", {"card_id": f"item:{CARD_UUID}"}))
    job = asyncio.run(mcp.call_tool("board_card", {"card_id": f"job:{JOB_UUID}"}))
    base = "https://sazed.tail77c590.ts.net/?open="
    assert item.structuredContent["open_url"] == f"{base}backlog:{CARD_UUID}"
    assert job.structuredContent["open_url"] == f"{base}job:{JOB_UUID}"
    assert job.structuredContent["pr"] is None


def test_board_card_caps_check_runs(monkeypatch):
    """A PR with 45 check runs draws the first 20; passed and total still count all 45."""
    checks = {"state": "passing", "passed": 45, "total": 45}
    runs = [{"name": f"r{i}", "conclusion": "success"} for i in range(45)]
    card = {**CARD, "pr": {**CARD["pr"], "checks": {**checks, "runs": runs}}}
    _command(monkeypatch, card)
    out = asyncio.run(mcp.call_tool("board_card", {"card_id": f"item:{CARD_UUID}"}))
    pr = out.structuredContent["pr"]
    assert [r["name"] for r in pr["checks"]["runs"]] == [f"r{i}" for i in range(20)]
    assert pr["checks"]["total"] == 45
    assert pr["checks"]["runs_omitted"] == 25
    assert _schema_errors("PullRequestWidget", out.structuredContent) == []


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "http://github.com/owner/repo/pull/7",
        "https://evil.example/owner/repo/pull/7",
        "https://github.com.evil.example/x",
        "https://user@github.com/x",
        "https://github.com:8443/x",
        None,
    ],
)
def test_pr_links_off_github_are_dropped(monkeypatch, url):
    """A PR or review link that is not https on github.com reaches neither card as a link."""
    pr = {**CARD["pr"], "url": url, "review": {"verdict": "approve", "source": "x", "url": url}}
    card = {**CARD, "pr": pr, "pr_url": url}
    _command(monkeypatch, card)
    out = asyncio.run(mcp.call_tool("board_card", {"card_id": f"item:{CARD_UUID}"}))
    assert out.structuredContent["pr"]["url"] == ""
    assert out.structuredContent["pr"]["review"]["url"] == ""
    assert out.structuredContent["pr_url"] is None
    _command(monkeypatch, {**EMPTY_COMMAND, "needs": [card]})
    need = asyncio.run(mcp.call_tool("board_command", {})).structuredContent["needs"][0]
    assert need["pr"]["url"] == ""


@pytest.mark.parametrize(
    "console_url",
    [
        "http://192.168.50.155:3000",
        "https://sazed.example/?x=1",
        "https://sazed.example/#frag",
        "https://user:pw@sazed.example",
        "https://sazed.example:8443",
        " https://sazed.example",
        "",
    ],
)
def test_no_console_link_from_a_console_url_the_card_would_refuse(monkeypatch, console_url):
    """A console_url that is not a bare https origin gives the cards no console button."""
    monkeypatch.setattr(mcp_server.settings, "console_url", console_url)
    out = asyncio.run(mcp.call_tool("board_command", {})).structuredContent
    assert out["open_url"] is None
    card = asyncio.run(mcp.call_tool("board_card", {"card_id": f"item:{CARD_UUID}"}))
    assert card.structuredContent["open_url"] is None


def test_board_command_lists_at_most_50_needs(monkeypatch):
    """60 short merge calls list 50, and needs_total still counts 60."""
    short = [{**_merge_call(i), "summary": "", "why": "", "note": ""} for i in range(1, 61)]
    _command(monkeypatch, {**EMPTY_COMMAND, "needs": short})
    out = asyncio.run(mcp.call_tool("board_command", {})).structuredContent
    assert len(out["needs"]) == 50
    assert out["needs_total"] == 60


def test_a_long_question_is_cut_at_a_word(monkeypatch):
    """A question past 5,000 characters ends at a whole word followed by an ellipsis."""
    need = {**DISPATCH_QUESTION, "question": "word " * 1500}
    _command(monkeypatch, {**EMPTY_COMMAND, "needs": [need]})
    question = asyncio.run(mcp.call_tool("board_command", {})).structuredContent["needs"][0][
        "question"
    ]
    assert len(question) <= 5000
    assert question.endswith("word…")


HUGE = "x" * 1000


def test_hostile_board_command_is_bounded(monkeypatch):
    """Lists and text in every field Sazed could inflate still fit 100,000 characters across
    both copies, and the result still validates."""
    need = {
        **_merge_call(1),
        "stage": [HUGE] * 50,
        "job_status": {"x": HUGE},
        "updated_at": [HUGE],
        "id": HUGE * 5,
        "pr": {**CARD["pr"], "repo": HUGE * 5, "state": [HUGE] * 50, "number": [HUGE]},
    }
    hostile = {
        "needs": [need] * 200,
        "working": [{**WORKING[0], "tools": [HUGE] * 50, "kind": HUGE * 50}] * 60,
        "done": {"succeeded": [HUGE] * 50, "failed": HUGE, "window_hours": {"x": HUGE}},
        "sessions": [HUGE] * 500,
    }
    _command(monkeypatch, hostile)
    result = asyncio.run(mcp.call_tool("board_command", {}))
    assert len(result.content[0].text) * 2 <= 100_000
    assert result.structuredContent["needs_total"] == 200
    assert _schema_errors("NeedsYouWidget", result.structuredContent) == []


def test_hostile_board_card_is_bounded(monkeypatch):
    """A 200 KB note, a 50 KB summary and 500 history rows fit 100,000 characters across both
    copies: text ends with an ellipsis and the cut list says how many rows it left out."""
    card = {
        **CARD,
        "note": "n " * 100_000,
        "summary": "s" * 50_000,
        "history": [{"at": "2026-11-26", "what": HUGE}] * 500,
    }
    _command(monkeypatch, card)
    result = asyncio.run(mcp.call_tool("board_card", {"card_id": f"item:{CARD_UUID}"}))
    out = result.structuredContent
    assert len(result.content[0].text) * 2 <= 100_000
    assert out["note"].endswith("…") and out["summary"].endswith("…")
    assert out["history_omitted"] == len(card["history"]) - len(out["history"])
    assert out["pr"]["checks"]["runs"] == CARD["pr"]["checks"]["runs"]
    assert _schema_errors("PullRequestWidget", out) == []


def test_a_card_too_big_for_any_cut_keeps_only_what_the_card_draws(monkeypatch):
    """Nesting that no cut can shrink enough leaves the card's own fields and a note."""
    nested = [[[[HUGE] * 20] * 20] * 20] * 20
    _command(monkeypatch, {**CARD, "title": HUGE, "extra": nested})
    result = asyncio.run(mcp.call_tool("board_card", {"card_id": f"item:{CARD_UUID}"}))
    out = result.structuredContent
    assert len(result.content[0].text) * 2 <= 100_000
    assert "extra" not in out and out["truncated"]
    assert len(out["title"]) == 300 and out["pr"]["number"] == 7
    assert _schema_errors("PullRequestWidget", out) == []
