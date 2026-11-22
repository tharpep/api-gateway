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


ROUTES = [
    ("post", "/vault/capture", {"json": {"content": "x"}}),
    ("get", "/vault/notes", {}),
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


@pytest.mark.parametrize("payload", [{"result": "not json"}, {"nothing": 1}])
def test_unreadable_success_is_502(http, payload):
    """A 200 whose body is not the documented envelope is a 502."""
    http.use(httpx.Response(200, json=payload))
    assert http.get("/vault/notes").status_code == 502
