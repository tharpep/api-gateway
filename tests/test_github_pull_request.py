import base64
import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import github_app

REPO = "tharpep/sandbox"
R = f"/repos/{REPO}"
PERMS = {"contents": "write", "pull_requests": "write"}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FakeGitHub:
    """Enough of GitHub's Git Data and pulls API to run the route, recording every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict[str, tuple[str, str]]] = {}
        self.commits: dict[str, dict] = {}
        self.refs: dict[str, str] = {}
        self.pulls: list[dict] = []

    def tree(self, files: dict[str, tuple[str, bytes]]) -> str:
        entries = {}
        for path, (mode, data) in files.items():
            sha = blob_sha(data)
            self.blobs[sha] = data
            entries[path] = (mode, sha)
        return self._store_tree(entries)

    def _store_tree(self, entries: dict[str, tuple[str, str]]) -> str:
        sha = hashlib.sha1(json.dumps(sorted(entries.items())).encode()).hexdigest()
        self.trees[sha] = entries
        return sha

    def commit(self, tree: str, parents: list[str], message: str = "seed") -> str:
        sha = hashlib.sha1(json.dumps([tree, parents, message]).encode()).hexdigest()
        self.commits[sha] = {"tree": tree, "parents": parents, "message": message}
        return sha

    def files_at(self, ref: str) -> dict[str, tuple[str, bytes]]:
        commit = self.commits[self.refs.get(ref, ref)]
        return {p: (m, self.blobs[s]) for p, (m, s) in self.trees[commit["tree"]].items()}

    def _ancestors(self, sha: str) -> list[str]:
        seen, todo = [], [sha]
        while todo:
            cur = todo.pop(0)
            if cur not in seen:
                seen.append(cur)
                todo.extend(self.commits[cur]["parents"])
        return seen

    def _resolve(self, name: str) -> str | None:
        return self.refs.get(name) or (name if name in self.commits else None)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        assert path.startswith(R) or path == "/graphql", path
        sub = path[len(R) :]
        params = dict(request.url.params)
        self.calls.append((request.method, sub, body or params or None))
        m = request.method
        if m == "GET" and sub == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if m == "GET" and sub.startswith("/git/ref/heads/"):
            sha = self.refs.get(sub[len("/git/ref/heads/") :])
            if sha is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": sha}})
        if m == "GET" and sub.startswith("/compare/"):
            base, head = sub[len("/compare/") :].split("...")
            base_sha, head_sha = self._resolve(base), self._resolve(head)
            if base_sha is None or head_sha is None:
                return httpx.Response(404, json={"message": "Not Found"})
            head_anc, base_anc = self._ancestors(head_sha), self._ancestors(base_sha)
            merge_base = next(c for c in head_anc if c in base_anc)
            if base_sha == head_sha:
                status = "identical"
            elif merge_base == base_sha:
                status = "ahead"
            elif merge_base == head_sha:
                status = "behind"
            else:
                status = "diverged"
            return httpx.Response(
                200, json={"status": status, "merge_base_commit": {"sha": merge_base}}
            )
        if m == "GET" and sub.startswith("/git/commits/"):
            sha = sub[len("/git/commits/") :]
            if sha not in self.commits:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                200, json={"sha": sha, "tree": {"sha": self.commits[sha]["tree"]}}
            )
        if m == "GET" and sub.startswith("/git/trees/"):
            entries = self.trees[sub[len("/git/trees/") :]]
            tree = [
                {"path": p, "mode": md, "type": "blob", "sha": s} for p, (md, s) in entries.items()
            ]
            return httpx.Response(200, json={"tree": tree, "truncated": False})
        if m == "POST" and sub == "/git/blobs":
            data = base64.b64decode(body["content"])
            sha = blob_sha(data)
            self.blobs[sha] = data
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and sub == "/git/trees":
            entries = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if "sha" in e and "content" in e:
                    return httpx.Response(422, json={"message": "sha and content"})
                if "content" in e:
                    data = e["content"].encode()
                    self.blobs[blob_sha(data)] = data
                    entries[e["path"]] = (e["mode"], blob_sha(data))
                elif e["sha"] is None:
                    if e["path"] not in entries:
                        return httpx.Response(422, json={"message": "no such path"})
                    del entries[e["path"]]
                else:
                    entries[e["path"]] = (e["mode"], e["sha"])
            return httpx.Response(201, json={"sha": self._store_tree(entries)})
        if m == "POST" and sub == "/git/commits":
            sha = self.commit(body["tree"], body["parents"], body["message"])
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and sub == "/git/refs":
            self.refs[body["ref"][len("refs/heads/") :]] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"]})
        if m == "PATCH" and sub.startswith("/git/refs/heads/"):
            branch = sub[len("/git/refs/heads/") :]
            if not body.get("force") and self.refs[branch] not in self._ancestors(body["sha"]):
                return httpx.Response(422, json={"message": "Update is not a fast forward"})
            self.refs[branch] = body["sha"]
            return httpx.Response(200, json={})
        if m == "GET" and sub == "/pulls":
            branch = params["head"].split(":", 1)[1]
            found = [p for p in self.pulls if p["head"]["ref"] == branch and p["state"] == "open"]
            return httpx.Response(200, json=found)
        if m == "GET" and sub.startswith("/pulls/"):
            return httpx.Response(200, json=self.pulls[int(sub[len("/pulls/") :]) - 1])
        if m == "POST" and sub == "/pulls":
            pr = {
                "number": len(self.pulls) + 1,
                "html_url": f"https://github.com/{REPO}/pull/{len(self.pulls) + 1}",
                "head": {"ref": body["head"]},
                "state": "open",
                "draft": body["draft"],
                "node_id": "PR_x",
            }
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        raise AssertionError(f"unexpected call {m} {path}")


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(github_app, "get_client", lambda: client)
    key = (REPO.lower(), tuple(sorted(PERMS.items())))
    monkeypatch.setitem(github_app._tokens, key, ("tok", 4_102_444_800.0))
    main_tree = fake.tree(
        {
            "README.md": ("100644", b"hello\n"),
            "run.sh": ("100755", b"#!/bin/sh\necho old\n"),
            "logo.png": ("100644", b"\x89PNG\r\n\x1a\n\x00\x00"),
        }
    )
    fake.root = fake.commit(main_tree, [])
    fake.refs["main"] = fake.root
    return fake


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(github_app.router, prefix="/github/app")
    return TestClient(app)


def request(**extra):
    return {
        "repo": REPO,
        "branch": "sazed/job-1",
        "title": "Do the thing",
        "body": "Body",
        "commit_message": "Do the thing\n\nSazed job 1",
        "files": {
            "run.sh": b64(b"#!/bin/sh\necho new\n"),
            "new.txt": b64(b"fresh\n"),
            "README.md": None,
            "missing.txt": None,
        },
        **extra,
    }


def test_pin_old_style_request_on_a_new_branch(gh, api):
    """Pins the calls and bodies for a request without base_sha or modes, as on d813fd3."""
    resp = api.post("/github/app/pull-request", json=request())
    assert resp.status_code == 201, resp.text

    main_tree = gh.commits[gh.root]["tree"]
    new_tree = gh.commits[resp.json()["head_sha"]]["tree"]
    run_sha = blob_sha(b"#!/bin/sh\necho new\n")
    new_sha = blob_sha(b"fresh\n")
    assert gh.calls == [
        ("GET", "", None),
        ("GET", "/git/ref/heads/main", None),
        ("GET", "/git/ref/heads/sazed/job-1", None),
        ("GET", f"/git/commits/{gh.root}", None),
        ("GET", f"/git/trees/{main_tree}", {"recursive": "1"}),
        ("POST", "/git/blobs", {"content": b64(b"fresh\n"), "encoding": "base64"}),
        ("POST", "/git/blobs", {"content": b64(b"#!/bin/sh\necho new\n"), "encoding": "base64"}),
        (
            "POST",
            "/git/trees",
            {
                "base_tree": main_tree,
                "tree": [
                    {"path": "README.md", "mode": "100644", "type": "blob", "sha": None},
                    {"path": "new.txt", "mode": "100644", "type": "blob", "sha": new_sha},
                    {"path": "run.sh", "mode": "100755", "type": "blob", "sha": run_sha},
                ],
            },
        ),
        (
            "POST",
            "/git/commits",
            {"message": "Do the thing\n\nSazed job 1", "tree": new_tree, "parents": [gh.root]},
        ),
        (
            "POST",
            "/git/refs",
            {"ref": "refs/heads/sazed/job-1", "sha": resp.json()["head_sha"]},
        ),
        ("GET", "/pulls", {"head": "tharpep:sazed/job-1", "state": "open"}),
        (
            "POST",
            "/pulls",
            {
                "title": "Do the thing",
                "head": "sazed/job-1",
                "base": "main",
                "body": "Body",
                "draft": False,
            },
        ),
    ]
    assert resp.json() == {
        "number": 1,
        "url": f"https://github.com/{REPO}/pull/1",
        "branch": "sazed/job-1",
        "files": 3,
        "updated": False,
        "head_sha": resp.json()["head_sha"],
        "draft": False,
    }
    assert gh.files_at("sazed/job-1") == {
        "run.sh": ("100755", b"#!/bin/sh\necho new\n"),
        "new.txt": ("100644", b"fresh\n"),
        "logo.png": ("100644", b"\x89PNG\r\n\x1a\n\x00\x00"),
    }


def test_pin_old_style_follow_up_on_an_existing_branch(gh, api):
    """Pins an existing branch: tree from the merge base, fast-forward, the open PR reused."""
    first = api.post("/github/app/pull-request", json=request(draft=True)).json()
    gh.calls.clear()

    resp = api.post(
        "/github/app/pull-request",
        json=request(files={"new.txt": b64(b"fresher\n")}, pr_number=1),
    )
    assert resp.status_code == 201, resp.text

    main_tree = gh.commits[gh.root]["tree"]
    head = resp.json()["head_sha"]
    assert gh.calls == [
        ("GET", "/pulls/1", None),
        ("GET", "", None),
        ("GET", "/git/ref/heads/main", None),
        ("GET", "/git/ref/heads/sazed/job-1", None),
        ("GET", "/compare/main...sazed/job-1", None),
        ("GET", f"/git/commits/{gh.root}", None),
        ("GET", f"/git/trees/{main_tree}", {"recursive": "1"}),
        ("POST", "/git/blobs", {"content": b64(b"fresher\n"), "encoding": "base64"}),
        (
            "POST",
            "/git/trees",
            {
                "base_tree": main_tree,
                "tree": [
                    {
                        "path": "new.txt",
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha(b"fresher\n"),
                    }
                ],
            },
        ),
        (
            "POST",
            "/git/commits",
            {
                "message": "Do the thing\n\nSazed job 1",
                "tree": gh.commits[head]["tree"],
                "parents": [first["head_sha"]],
            },
        ),
        ("PATCH", "/git/refs/heads/sazed/job-1", {"sha": head, "force": False}),
        ("GET", "/pulls", {"head": "tharpep:sazed/job-1", "state": "open"}),
    ]
    assert resp.json()["updated"] is True
    assert resp.json()["draft"] is True
    assert gh.files_at("sazed/job-1") == {
        "README.md": ("100644", b"hello\n"),
        "run.sh": ("100755", b"#!/bin/sh\necho old\n"),
        "logo.png": ("100644", b"\x89PNG\r\n\x1a\n\x00\x00"),
        "new.txt": ("100644", b"fresher\n"),
    }
