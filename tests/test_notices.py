import asyncio

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import alerts, notices
from app.config import settings
from app.notices import Kind, Priority
from app.registry import ServiceDef
from app.routers import notify

CONSOLE = "https://console.example.test"


@pytest.fixture
def sent(monkeypatch):
    """Every Pushover request, captured instead of sent."""
    calls: list[dict] = []

    class FakeClient:
        async def post(self, url, data=None, files=None):
            calls.append(data)
            return httpx.Response(200, json={"status": 1, "request": "r"})

    monkeypatch.setattr(notify, "get_client", lambda: FakeClient())
    monkeypatch.setattr(notify.activity, "record", lambda *a, **k: None)
    monkeypatch.setattr(settings, "pushover_user_key", "u")
    monkeypatch.setattr(settings, "pushover_api_token", "t")
    monkeypatch.setattr(settings, "console_url", CONSOLE + "/")
    monkeypatch.setattr(settings, "alerts_enabled", True)
    return calls


def _send(kind: Kind, refs: dict[str, str] | None = None) -> dict:
    return asyncio.run(notify.send_notice(kind, "Title", "Body", refs))


def test_every_kind_has_a_rule_and_none_is_emergency():
    """A new kind without a rule fails here, and so does one that repeats until acknowledged."""
    assert set(notices.RULES) == set(Kind)
    assert all(r.priority != Priority.EMERGENCY for r in notices.RULES.values())


@pytest.mark.parametrize(
    ("kind", "refs", "priority", "ttl", "url", "url_title"),
    [
        (Kind.QUESTION, {"job": "j1"}, 0, None, "/?open=job:j1", "Answer it"),
        (Kind.PERMISSION, {"automation": "a1"}, 0, None, "/?open=automation:a1", "Review it"),
        (
            Kind.MERGE_CALL,
            {"card": "c1", "job": "j1"},
            0,
            None,
            "/?open=backlog:c1",
            "Open merge call",
        ),
        (Kind.MERGE_CALL, {"job": "j1"}, 0, None, "/?open=job:j1", "Open merge call"),
        (Kind.RUN_FAILED, {"job": "j1"}, 0, None, "/?open=job:j1", "Open run"),
        (Kind.RUN_FAILED, {"automation": "a1"}, 0, None, "/?open=automation:a1", "Open run"),
        (Kind.STORAGE_WARNING, {}, 0, None, "/?page=system.homelab.storage", "Open storage"),
        (Kind.SERVICE_DOWN, {"service": "sazed"}, 1, None, "/?open=service:sazed", "Open"),
        (Kind.APP_DOWN, {}, 0, None, "/?page=system.homelab", "Open"),
        (Kind.RECOVERED, {"service": "jellyfin"}, -1, 86400, "/?open=service:jellyfin", "Open"),
        (Kind.SPEND_WARNING, {}, 0, None, "/?page=system.usage", "Open usage"),
        (Kind.JOB_DONE, {"job": "j1"}, -1, 86400, "/?open=job:j1", "Open result"),
        (Kind.JOB_DONE, {}, -1, 86400, "/?page=agents.runs", "Open result"),
        (Kind.ROUTINE, {"card": "c1"}, -1, 86400, "/?open=backlog:c1", "Open"),
        (Kind.ROUTINE, {"automation": "a1"}, -1, 86400, "/?open=automation:a1", "Open"),
        (Kind.ROUTINE, {}, -1, 86400, "/", "Open"),
    ],
)
def test_each_kind_sends_its_rule(sent, kind, refs, priority, ttl, url, url_title):
    """The rule, not the sender, sets priority, expiry, link and button label."""
    _send(kind, refs)
    (payload,) = sent
    assert payload["priority"] == priority
    assert payload.get("ttl") == ttl
    assert payload["url"] == CONSOLE + url
    assert payload["url_title"] == url_title


def test_deploy_failure_links_to_the_github_run(sent):
    _send(Kind.DEPLOY_FAILED, {"url": "https://github.com/o/r/actions/runs/1"})
    assert sent[0]["url"] == "https://github.com/o/r/actions/runs/1"
    assert sent[0]["priority"] == 0 and "ttl" not in sent[0]


def test_a_bad_ref_loses_only_its_link(sent):
    """An alert still arrives when an id would not make a safe link."""
    _send(Kind.SERVICE_DOWN, {"service": "../x?y"})
    assert sent[0]["url"] == CONSOLE + "/?page=system.homelab"


def test_rest_kind_overrides_the_callers_own_rules(sent):
    body = notify.NotificationRequest(
        title="T",
        message="M",
        priority=1,
        url="https://x.test",
        kind="job_done",
        refs={"job": "j1"},
    )
    asyncio.run(notify.send_notification(body))
    assert sent[0]["priority"] == -1 and sent[0]["ttl"] == 86400
    assert sent[0]["url"] == CONSOLE + "/?open=job:j1"


def test_rest_without_kind_passes_ttl_and_url_title_through(sent):
    body = notify.NotificationRequest(
        title="T", message="M", url="https://x.test", url_title="See it", ttl=600
    )
    asyncio.run(notify.send_notification(body))
    assert (sent[0]["url"], sent[0]["url_title"], sent[0]["ttl"]) == (
        "https://x.test",
        "See it",
        600,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"ttl": 0},
        {"url": "https://x.test/" + "a" * 512},
        {"url_title": "a" * 101},
        {"refs": {"nope": "1"}},
        {"refs": {"job": "a/b"}},
        {"refs": {"storage": "x"}},
        {"refs": {"url": "javascript:alert(1)"}},
        {"kind": "emergency"},
    ],
)
def test_rest_rejects_out_of_limit_fields(fields):
    with pytest.raises(ValidationError):
        notify.NotificationRequest(title="T", message="M", **fields)


def test_send_pushover_checks_limits_for_direct_callers(sent):
    with pytest.raises(HTTPException):
        asyncio.run(notify.send_pushover("T", "M", ttl=0))
    with pytest.raises(HTTPException):
        asyncio.run(notify.send_pushover("T", "M", url_title="a" * 101))
    assert sent == []


def test_emergency_ignores_ttl(sent):
    asyncio.run(notify.send_pushover("T", "M", Priority.EMERGENCY, ttl=60))
    assert "ttl" not in sent[0] and sent[0]["expire"] == 3600


def _service(sid: str, group: str) -> ServiceDef:
    return ServiceDef.model_construct(id=sid, name=sid.title(), group=group)


def test_outage_alerts_pick_their_kind_and_link(sent):
    """One infra service down is high priority and opens it; recovery is quiet and expires."""
    asyncio.run(alerts.outages([(_service("sazed", "sazed"), False, "timeout")], None))
    asyncio.run(alerts.outages([(_service("sazed", "sazed"), True, "")], None))
    down, back = sent
    assert (down["priority"], down["url"]) == (1, CONSOLE + "/?open=service:sazed")
    assert (back["priority"], back["ttl"]) == (-1, 86400)


def test_several_apps_down_open_the_homelab_page(sent):
    asyncio.run(
        alerts.outages(
            [(_service("a", "apps"), False, "x"), (_service("b", "apps"), False, "y")], None
        )
    )
    assert (sent[0]["priority"], sent[0]["url"]) == (0, CONSOLE + "/?page=system.homelab")
