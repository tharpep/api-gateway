import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import vault

URL = "http://vault.test"


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(settings, "vault_url", URL)
    monkeypatch.setattr(settings, "vault_api_key", "read-key")
    monkeypatch.setattr(settings, "vault_capture_key", "capture-key")
    app = FastAPI()
    app.include_router(vault.router, prefix="/vault")
    client = TestClient(app)

    def use(response=None, error=None):
        fake = FakeClient(response, error)
        monkeypatch.setattr(vault, "get_client", lambda: fake)
        return fake

    client.use = use
    return client


def ok(result):
    return httpx.Response(200, json={"result": json.dumps(result)})


def test_capture_posts_capture_verb_with_capture_key(http):
    """Capture answers 201 with the parsed result and sends only the capture key."""
    fake = http.use(ok({"path": "inbox/a.md", "captured": True}))
    r = http.post("/vault/capture", json={"content": "hello", "source": "phone-1"})
    assert r.status_code == 201
    assert r.json() == {"path": "inbox/a.md", "captured": True}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/capture"
    assert kwargs["json"] == {"content": "hello", "source": "phone-1"}
    assert kwargs["headers"] == {"X-Vault-Key": "capture-key"}
    assert kwargs["timeout"] == 20.0


def test_capture_source_defaults_to_console(http):
    """A body without a source is captured from the console."""
    fake = http.use(ok({"path": "inbox/a.md", "captured": True}))
    assert http.post("/vault/capture", json={"content": "x"}).status_code == 201
    assert fake.calls[0][1]["json"]["source"] == "console"


def test_notes_posts_notes_verb_with_read_key(http):
    """Notes passes folders, query, limit and offset, and sends only the read key."""
    fake = http.use(ok({"notes": [{"path": "notes/a.md"}], "total": 1}))
    r = http.get(
        "/vault/notes",
        params={"folder": ["inbox", "claude"], "q": "milk", "limit": 5, "offset": 10},
    )
    assert r.status_code == 200
    assert r.json() == {"notes": [{"path": "notes/a.md"}], "total": 1}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/notes"
    assert kwargs["json"] == {
        "folders": ["inbox", "claude"],
        "query": "milk",
        "limit": 5,
        "offset": 10,
    }
    assert kwargs["headers"] == {"X-Vault-Key": "read-key"}
    assert kwargs["timeout"] == 20.0


def test_notes_defaults(http):
    """Without a query string the service gets no folders, an empty query, limit 50, offset 0."""
    fake = http.use(ok({"notes": [], "total": 0}))
    assert http.get("/vault/notes").status_code == 200
    assert fake.calls[0][1]["json"] == {"folders": [], "query": "", "limit": 50, "offset": 0}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": ""},
        {"content": "x" * 100001},
        {"content": "x", "source": "Console"},
        {"content": "x", "source": "1console"},
        {"content": "x", "source": "a" * 51},
        {"content": "x", "source": ""},
    ],
)
def test_capture_refuses_invalid_body(http, body):
    """A body outside the contract is a 422 and never reaches the service."""
    fake = http.use(ok({}))
    assert http.post("/vault/capture", json=body).status_code == 422
    assert fake.calls == []


def test_capture_accepts_the_limits(http):
    """100000 characters and a 50-character source are inside the contract."""
    http.use(ok({"path": "p", "captured": True}))
    body = {"content": "x" * 100000, "source": "a" * 50}
    assert http.post("/vault/capture", json=body).status_code == 201


@pytest.mark.parametrize(
    "params",
    [
        {"folder": "elsewhere"},
        {"folder": ["inbox", "elsewhere"]},
        {"q": "x" * 201},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
    ],
)
def test_notes_refuses_invalid_query(http, params):
    """A query outside the contract is a 422 and never reaches the service."""
    fake = http.use(ok({}))
    assert http.get("/vault/notes", params=params).status_code == 422
    assert fake.calls == []


def test_notes_accepts_the_limits(http):
    """Limit 200, offset 0 and a 200-character query are inside the contract."""
    http.use(ok({"notes": [], "total": 0}))
    params = {"limit": 200, "offset": 0, "q": "x" * 200, "folder": "notes"}
    assert http.get("/vault/notes", params=params).status_code == 200


REWRITE = {"path": "inbox/a.md", "content": "new text", "version": "v1"}
FILE = {
    "path": "inbox/a.md",
    "topic": "cooking",
    "reason": "a recipe",
    "kind": "recipe",
    "filer": "sazed",
}
MOVE = {"path": "inbox/a.md", "topic": "cooking"}

ROUTES = [
    ("post", "/vault/capture", {"json": {"content": "x"}}),
    ("get", "/vault/notes", {}),
    ("get", "/vault/note", {"params": {"path": "inbox/a.md"}}),
    ("put", "/vault/note", {"json": REWRITE}),
    ("delete", "/vault/note", {"params": {"path": "inbox/a.md"}}),
    ("get", "/vault/topics", {}),
    ("post", "/vault/file", {"json": FILE}),
    ("post", "/vault/move", {"json": MOVE}),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES)
def test_unset_url_is_503(http, monkeypatch, method, path, kwargs):
    """Without VAULT_URL no request goes out."""
    fake = http.use(ok({}))
    monkeypatch.setattr(settings, "vault_url", "")
    assert getattr(http, method)(path, **kwargs).status_code == 503
    assert fake.calls == []


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "key"),
    [
        ("post", "/vault/capture", {"json": {"content": "x"}}, "vault_capture_key"),
        ("get", "/vault/notes", {}, "vault_api_key"),
        ("get", "/vault/note", {"params": {"path": "inbox/a.md"}}, "vault_api_key"),
        ("put", "/vault/note", {"json": REWRITE}, "vault_capture_key"),
        ("delete", "/vault/note", {"params": {"path": "inbox/a.md"}}, "vault_capture_key"),
        ("get", "/vault/topics", {}, "vault_api_key"),
        ("post", "/vault/file", {"json": FILE}, "vault_api_key"),
        ("post", "/vault/move", {"json": MOVE}, "vault_capture_key"),
    ],
)
def test_unset_key_is_503(http, monkeypatch, method, path, kwargs, key):
    """Each route needs its own key; the other route's key does not stand in."""
    fake = http.use(ok({}))
    monkeypatch.setattr(settings, key, "")
    assert getattr(http, method)(path, **kwargs).status_code == 503
    assert fake.calls == []


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES)
def test_connect_error_is_503(http, method, path, kwargs):
    """A refused connection is a 503."""
    http.use(error=httpx.ConnectError("down"))
    assert getattr(http, method)(path, **kwargs).status_code == 503


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES)
def test_timeout_is_504(http, method, path, kwargs):
    """A timeout is a 504."""
    http.use(error=httpx.ReadTimeout("slow"))
    assert getattr(http, method)(path, **kwargs).status_code == 504


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES)
def test_service_403_carries_refused_text(http, method, path, kwargs):
    """The service's refusal becomes a 403 whose detail is the refused text."""
    http.use(httpx.Response(403, json={"refused": "write gate: not under claude/"}))
    r = getattr(http, method)(path, **kwargs)
    assert r.status_code == 403
    assert r.json()["detail"] == "write gate: not under claude/"


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES)
def test_service_400_carries_error_text(http, method, path, kwargs):
    """The service's 400 keeps its error text as detail."""
    http.use(httpx.Response(400, json={"error": "bad folder"}))
    r = getattr(http, method)(path, **kwargs)
    assert r.status_code == 400
    assert r.json()["detail"] == "bad folder"


@pytest.mark.parametrize("status", [401, 404, 500, 503])
@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES)
def test_other_errors_are_502(http, method, path, kwargs, status):
    """Any other status at or above 400, including the service's 401, is a 502."""
    http.use(httpx.Response(status, json={"error": "x"}))
    assert getattr(http, method)(path, **kwargs).status_code == 502


def test_note_posts_note_verb_with_read_key(http):
    """Reading a note passes the path and sends only the read key."""
    fake = http.use(ok({"path": "inbox/a.md", "content": "hi", "version": "v1"}))
    r = http.get("/vault/note", params={"path": "inbox/a.md"})
    assert r.status_code == 200
    assert r.json() == {"path": "inbox/a.md", "content": "hi", "version": "v1"}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/note"
    assert kwargs["json"] == {"path": "inbox/a.md"}
    assert kwargs["headers"] == {"X-Vault-Key": "read-key"}


def test_rewrite_posts_rewrite_verb_with_capture_key(http):
    """Rewriting a note passes path, content and version and sends only the capture key."""
    fake = http.use(ok({"path": "inbox/a.md", "version": "v2"}))
    r = http.put("/vault/note", json=REWRITE)
    assert r.status_code == 200
    assert r.json() == {"path": "inbox/a.md", "version": "v2"}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/rewrite"
    assert kwargs["json"] == REWRITE
    assert kwargs["headers"] == {"X-Vault-Key": "capture-key"}


def test_discard_posts_discard_verb_with_capture_key(http):
    """Discarding a note passes the path, sends only the capture key and answers 200."""
    fake = http.use(ok({"path": "inbox/a.md", "discarded": True}))
    r = http.delete("/vault/note", params={"path": "inbox/a.md"})
    assert r.status_code == 200
    assert r.json() == {"path": "inbox/a.md", "discarded": True}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/discard"
    assert kwargs["json"] == {"path": "inbox/a.md"}
    assert kwargs["headers"] == {"X-Vault-Key": "capture-key"}


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES[2:])
def test_service_409_carries_conflict_text(http, method, path, kwargs):
    """The service's conflict becomes a 409 whose detail is the conflict text."""
    http.use(httpx.Response(409, json={"conflict": "version changed"}))
    r = getattr(http, method)(path, **kwargs)
    assert r.status_code == 409
    assert r.json()["detail"] == "version changed"


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get", {}),
        ("get", {"params": {"path": ""}}),
        ("get", {"params": {"path": "x" * 301}}),
        ("delete", {}),
        ("delete", {"params": {"path": ""}}),
        ("delete", {"params": {"path": "x" * 301}}),
        ("put", {"json": {}}),
        ("put", {"json": {**REWRITE, "path": ""}}),
        ("put", {"json": {**REWRITE, "path": "x" * 301}}),
        ("put", {"json": {**REWRITE, "content": ""}}),
        ("put", {"json": {**REWRITE, "content": "x" * 100001}}),
        ("put", {"json": {"path": "a.md", "content": "x"}}),
        ("put", {"json": {**REWRITE, "version": ""}}),
        ("put", {"json": {**REWRITE, "version": "v" * 65}}),
    ],
)
def test_note_refuses_invalid_input(http, method, kwargs):
    """A path, body or version outside the contract is a 422 and never reaches the service."""
    fake = http.use(ok({}))
    assert getattr(http, method)("/vault/note", **kwargs).status_code == 422
    assert fake.calls == []


def test_note_accepts_the_limits(http):
    """A 300-character path, 100000 characters of content and a 64-character version pass."""
    http.use(ok({"path": "p"}))
    body = {"path": "p" * 300, "content": "x" * 100000, "version": "v" * 64}
    assert http.put("/vault/note", json=body).status_code == 200
    assert http.get("/vault/note", params={"path": "p" * 300}).status_code == 200
    assert http.delete("/vault/note", params={"path": "p" * 300}).status_code == 200


@pytest.mark.parametrize("payload", [{"result": "not json"}, {"nothing": 1}])
def test_unreadable_success_is_502(http, payload):
    """A 200 whose body is not the documented envelope is a 502."""
    http.use(httpx.Response(200, json=payload))
    assert http.get("/vault/notes").status_code == 502


def test_notes_sends_no_topic_without_the_query(http):
    """Without a topic query the body has no topic key."""
    fake = http.use(ok({"notes": [], "total": 0}))
    assert http.get("/vault/notes").status_code == 200
    assert "topic" not in fake.calls[0][1]["json"]


def test_notes_sends_the_topic_query(http):
    """A topic query is passed to the service as topic."""
    fake = http.use(ok({"notes": [], "total": 0}))
    assert http.get("/vault/notes", params={"topic": "cooking"}).status_code == 200
    assert fake.calls[0][1]["json"]["topic"] == "cooking"


def test_notes_refuses_a_long_topic(http):
    """A 41-character topic is a 422 and never reaches the service."""
    fake = http.use(ok({}))
    assert http.get("/vault/notes", params={"topic": "t" * 41}).status_code == 422
    assert fake.calls == []


def test_topics_posts_topics_verb_with_read_key(http):
    """Topics sends an empty body and only the read key."""
    fake = http.use(ok({"topics": [{"topic": "cooking", "count": 2}]}))
    r = http.get("/vault/topics")
    assert r.status_code == 200
    assert r.json() == {"topics": [{"topic": "cooking", "count": 2}]}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/topics"
    assert kwargs["json"] == {}
    assert kwargs["headers"] == {"X-Vault-Key": "read-key"}


def test_file_posts_file_under_verb_with_read_key(http):
    """Filing passes the five fields, sends only the read key and answers 200."""
    fake = http.use(ok({"path": "notes/cooking/a.md", "filed": True}))
    r = http.post("/vault/file", json=FILE)
    assert r.status_code == 200
    assert r.json() == {"path": "notes/cooking/a.md", "filed": True}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/file_under"
    assert kwargs["json"] == FILE
    assert kwargs["headers"] == {"X-Vault-Key": "read-key"}


def test_file_kind_and_filer_default_empty(http):
    """A body without kind and filer sends both as empty strings."""
    fake = http.use(ok({"filed": True}))
    body = {"path": "inbox/a.md", "topic": "cooking", "reason": "a recipe"}
    assert http.post("/vault/file", json=body).status_code == 200
    assert fake.calls[0][1]["json"] == {**body, "kind": "", "filer": ""}


def test_move_posts_move_to_verb_with_capture_key(http):
    """Moving passes path and topic, sends only the capture key and answers 200."""
    fake = http.use(ok({"path": "notes/cooking/a.md", "moved": True}))
    r = http.post("/vault/move", json=MOVE)
    assert r.status_code == 200
    assert r.json() == {"path": "notes/cooking/a.md", "moved": True}
    url, kwargs = fake.calls[0]
    assert url == f"{URL}/v1/move_to"
    assert kwargs["json"] == MOVE
    assert kwargs["headers"] == {"X-Vault-Key": "capture-key"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/vault/file", {k: v for k, v in FILE.items() if k != "path"}),
        ("/vault/file", {**FILE, "path": ""}),
        ("/vault/file", {**FILE, "path": "x" * 301}),
        ("/vault/file", {**FILE, "topic": ""}),
        ("/vault/file", {**FILE, "topic": "t" * 41}),
        ("/vault/file", {**FILE, "reason": ""}),
        ("/vault/file", {**FILE, "reason": "r" * 301}),
        ("/vault/file", {**FILE, "kind": "k" * 21}),
        ("/vault/file", {**FILE, "filer": "f" * 21}),
        ("/vault/move", {"topic": "cooking"}),
        ("/vault/move", {**MOVE, "path": ""}),
        ("/vault/move", {**MOVE, "path": "x" * 301}),
        ("/vault/move", {**MOVE, "topic": ""}),
        ("/vault/move", {**MOVE, "topic": "t" * 41}),
    ],
)
def test_file_and_move_refuse_invalid_body(http, path, body):
    """A body outside the contract is a 422 and never reaches the service."""
    fake = http.use(ok({}))
    assert http.post(path, json=body).status_code == 422
    assert fake.calls == []


def test_file_and_move_accept_the_limits(http):
    """The longest path, topic, reason, kind and filer are inside the contract."""
    http.use(ok({"filed": True}))
    body = {
        "path": "p" * 300,
        "topic": "t" * 40,
        "reason": "r" * 300,
        "kind": "k" * 20,
        "filer": "f" * 20,
    }
    assert http.post("/vault/file", json=body).status_code == 200
    assert http.post("/vault/move", json={"path": "p" * 300, "topic": "t" * 40}).status_code == 200
