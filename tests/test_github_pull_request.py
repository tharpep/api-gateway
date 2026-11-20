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
        self.normalize_crlf = False
        self.truncated: set[str] = set()
        self.truncate_new_trees = False

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
            merge_base = next((c for c in head_anc if c in base_anc), None)
            if merge_base is None:
                return httpx.Response(404, json={"message": "No common ancestor"})
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
            sha = sub[len("/git/trees/") :]
            tree = [
                {"path": p, "mode": md, "type": "blob", "sha": s}
                for p, (md, s) in self.trees[sha].items()
            ]
            return httpx.Response(200, json={"tree": tree, "truncated": sha in self.truncated})
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
                    text = e["content"]
                    if self.normalize_crlf:
                        text = text.replace("\r\n", "\n")
                    data = text.encode()
                    self.blobs[blob_sha(data)] = data
                    entries[e["path"]] = (e["mode"], blob_sha(data))
                elif e["sha"] is None:
                    if e["path"] not in entries:
                        return httpx.Response(422, json={"message": "no such path"})
                    del entries[e["path"]]
                else:
                    entries[e["path"]] = (e["mode"], e["sha"])
            sha = self._store_tree(entries)
            if self.truncate_new_trees:
                self.truncated.add(sha)
            return httpx.Response(201, json={"sha": sha})
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
    """Pins the calls for a request without base_sha or modes: d813fd3's, with text inline and
    read back before the commit (G6-5)."""
    resp = api.post("/github/app/pull-request", json=request())
    assert resp.status_code == 201, resp.text

    main_tree = gh.commits[gh.root]["tree"]
    new_tree = gh.commits[resp.json()["head_sha"]]["tree"]
    run_text = "#!/bin/sh\necho new\n"
    assert gh.calls == [
        ("GET", "", None),
        ("GET", "/git/ref/heads/main", None),
        ("GET", "/git/ref/heads/sazed/job-1", None),
        ("GET", f"/git/commits/{gh.root}", None),
        ("GET", f"/git/trees/{main_tree}", {"recursive": "1"}),
        (
            "POST",
            "/git/trees",
            {
                "base_tree": main_tree,
                "tree": [
                    {"path": "README.md", "mode": "100644", "type": "blob", "sha": None},
                    {"path": "new.txt", "mode": "100644", "type": "blob", "content": "fresh\n"},
                    {"path": "run.sh", "mode": "100755", "type": "blob", "content": run_text},
                ],
            },
        ),
        ("GET", f"/git/trees/{new_tree}", {"recursive": "1"}),
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
        (
            "POST",
            "/git/trees",
            {
                "base_tree": main_tree,
                "tree": [
                    {"path": "new.txt", "mode": "100644", "type": "blob", "content": "fresher\n"}
                ],
            },
        ),
        ("GET", f"/git/trees/{gh.commits[head]['tree']}", {"recursive": "1"}),
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


def advance_main(gh) -> str:
    files = gh.files_at("main")
    files["README.md"] = ("100644", b"upstream edit\n")
    gh.refs["main"] = gh.commit(gh.tree(files), [gh.refs["main"]], "upstream")
    return gh.refs["main"]


def export(**extra):
    return request(files={"new.txt": b64(b"fresh\n"), "README.md": None}, draft=True, **extra)


def writes(gh) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in gh.calls if m != "GET"]


def test_base_sha_on_a_new_branch_grows_from_that_commit(gh, api):
    """G6-2/G6-3: parent and tree come from base_sha, not the moved tip, and base_moved is set."""
    tip = advance_main(gh)

    resp = api.post("/github/app/pull-request", json=export(base_sha=gh.root))
    assert resp.status_code == 201, resp.text

    head = resp.json()["head_sha"]
    assert gh.commits[head]["parents"] == [gh.root]
    assert gh.files_at(head) == {
        "run.sh": ("100755", b"#!/bin/sh\necho old\n"),
        "logo.png": ("100644", b"\x89PNG\r\n\x1a\n\x00\x00"),
        "new.txt": ("100644", b"fresh\n"),
    }
    assert resp.json()["base_moved"] is True
    assert [(m, p) for m, p, _ in gh.calls] == [
        ("GET", ""),
        ("GET", "/git/ref/heads/main"),
        ("GET", f"/compare/{gh.root}...main"),
        ("GET", "/git/ref/heads/sazed/job-1"),
        ("GET", f"/git/commits/{gh.root}"),
        ("GET", f"/git/trees/{gh.commits[gh.root]['tree']}"),
        ("POST", "/git/trees"),
        ("GET", f"/git/trees/{gh.commits[head]['tree']}"),
        ("POST", "/git/commits"),
        ("POST", "/git/refs"),
        ("GET", "/pulls"),
        ("POST", "/pulls"),
    ]
    assert gh.refs["main"] == tip


def test_base_sha_at_the_tip_is_not_moved(gh, api):
    """G6-3: base_moved is false when the tip is base_sha, and no ancestry check is needed."""
    resp = api.post("/github/app/pull-request", json=export(base_sha=gh.root.upper()))
    assert resp.status_code == 201, resp.text
    assert resp.json()["base_moved"] is False
    assert gh.commits[resp.json()["head_sha"]]["parents"] == [gh.root]
    assert not any(p.startswith("/compare/") for _, p, _ in gh.calls)


def test_base_sha_on_an_existing_branch_fast_forwards_the_head(gh, api):
    """G6-2: tree from base_sha plus files, parent the branch head, no force; the PR is reused."""
    first = api.post("/github/app/pull-request", json=export(base_sha=gh.root)).json()
    advance_main(gh)
    gh.calls.clear()

    resp = api.post(
        "/github/app/pull-request",
        json=request(files={"new.txt": b64(b"fresher\n")}, base_sha=gh.root, draft=True),
    )
    assert resp.status_code == 201, resp.text

    head = resp.json()["head_sha"]
    assert gh.commits[head]["parents"] == [first["head_sha"]]
    assert gh.files_at(head) == {
        "README.md": ("100644", b"hello\n"),
        "run.sh": ("100755", b"#!/bin/sh\necho old\n"),
        "logo.png": ("100644", b"\x89PNG\r\n\x1a\n\x00\x00"),
        "new.txt": ("100644", b"fresher\n"),
    }
    assert ("PATCH", "/git/refs/heads/sazed/job-1", {"sha": head, "force": False}) in gh.calls
    assert writes(gh) == [
        ("POST", "/git/trees"),
        ("POST", "/git/commits"),
        ("PATCH", "/git/refs/heads/sazed/job-1"),
    ]
    assert resp.json()["updated"] is True
    assert resp.json()["base_moved"] is True


def test_base_sha_off_the_base_branch_is_refused_before_any_write(gh, api):
    """G6-2: a base_sha that isn't an ancestor of the base, or doesn't exist, writes nothing."""
    stray = gh.commit(gh.tree({"other.txt": ("100644", b"x\n")}), [gh.root], "stray")

    resp = api.post("/github/app/pull-request", json=export(base_sha=stray))
    assert resp.status_code == 422
    assert "isn't an ancestor of 'main'" in resp.json()["detail"]

    resp = api.post("/github/app/pull-request", json=export(base_sha="ab" * 20))
    assert resp.status_code == 422
    assert "not found" in resp.json()["detail"]

    assert writes(gh) == []
    assert "sazed/job-1" not in gh.refs


def pr_diff(gh, head: str) -> dict:
    merge_base = gh.handler(
        httpx.Request("GET", f"https://api.github.com{R}/compare/main...{head}")
    )
    a = gh.files_at(merge_base.json()["merge_base_commit"]["sha"])
    b = gh.files_at(head)
    return {p: (a.get(p), b.get(p)) for p in a.keys() | b.keys() if a.get(p) != b.get(p)}


def test_re_export_after_update_branch_with_an_older_base_sha_is_refused(gh, api):
    """Review 1: once main was merged into the branch, re-exporting from the older base_sha
    would revert main's commits on the branch, so it is a 409 that writes nothing."""
    api.post("/github/app/pull-request", json=export(base_sha=gh.root))
    b2 = advance_main(gh)
    head = gh.refs["sazed/job-1"]
    merged = gh.files_at(head)
    merged["README.md"] = gh.files_at(b2)["README.md"]
    gh.refs["sazed/job-1"] = gh.commit(gh.tree(merged), [head, b2], "Merge main")
    merged_head = gh.refs["sazed/job-1"]
    gh.calls.clear()

    resp = api.post("/github/app/pull-request", json=export(base_sha=gh.root))
    assert resp.status_code == 409
    assert "newer than base_sha" in resp.json()["detail"]
    assert "not reopening it" not in resp.json()["detail"]
    assert writes(gh) == []
    assert gh.refs["sazed/job-1"] == merged_head


def test_closed_pr_409_says_not_reopening_it(gh, api):
    """Sazed tells the closed-PR 409 from the re-export 409 by the phrase "not reopening it"."""
    api.post("/github/app/pull-request", json=request())
    gh.pulls[0]["state"] = "closed"
    gh.calls.clear()

    resp = api.post("/github/app/pull-request", json=request(pr_number=1))
    assert resp.status_code == 409
    assert "not reopening it" in resp.json()["detail"]
    assert writes(gh) == []


def test_re_export_with_a_newer_base_sha_adds_it_as_a_second_parent(gh, api):
    """Review 2: a plan rebuilt on a newer base gets that base as a second parent, so the PR
    diff shows only the plan's changes and the ref still fast-forwards."""
    first = api.post("/github/app/pull-request", json=export(base_sha=gh.root)).json()
    b2 = advance_main(gh)

    resp = api.post(
        "/github/app/pull-request", json=request(files={"new.txt": b64(b"fresh\n")}, base_sha=b2)
    )
    assert resp.status_code == 201, resp.text

    head = resp.json()["head_sha"]
    assert gh.commits[head]["parents"] == [first["head_sha"], b2]
    assert ("PATCH", "/git/refs/heads/sazed/job-1", {"sha": head, "force": False}) in gh.calls
    assert pr_diff(gh, head) == {"new.txt": (None, ("100644", b"fresh\n"))}
    assert resp.json()["base_moved"] is False


def test_base_sha_refuses_a_delete_it_does_not_have_and_a_truncated_tree(gh, api):
    """Review 3: with base_sha, the map and the tree must agree, so both are 422s before any
    write. Without base_sha, today's behaviour holds (the pins drop the unknown delete)."""
    resp = api.post(
        "/github/app/pull-request",
        json=request(files={"new.txt": b64(b"x\n"), "gone.txt": None}, base_sha=gh.root),
    )
    assert resp.status_code == 422
    assert "'gone.txt' is deleted but isn't in base_sha" in resp.json()["detail"]

    gh.truncated.add(gh.commits[gh.root]["tree"])
    resp = api.post("/github/app/pull-request", json=export(base_sha=gh.root))
    assert resp.status_code == 422
    assert "too large to read whole" in resp.json()["detail"]
    assert writes(gh) == []

    resp = api.post("/github/app/pull-request", json=request())
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("damage", ["normalize_crlf", "truncate_new_trees"])
def test_inline_text_github_did_not_store_byte_for_byte_stops_before_the_commit(gh, api, damage):
    """Review 5: inline files are read back after the tree call; altered bytes or an unreadable
    tree stop with a 502 before any commit or ref moves."""
    setattr(gh, damage, True)

    resp = api.post(
        "/github/app/pull-request",
        json=request(files={"a.txt": b64(b"one\n"), "win.txt": b64(b"line\r\n")}),
    )
    assert resp.status_code == 502
    expected = "'win.txt'" if damage == "normalize_crlf" else "too large to check"
    assert expected in resp.json()["detail"]
    assert writes(gh) == [("POST", "/git/trees")]
    assert gh.refs == {"main": gh.root}


def test_modes_are_written_including_symlinks_and_mode_only_changes(gh, api):
    """G6-4: listed modes win; a 120000 entry's content is the link target; same content, new
    mode is still written."""
    files = {
        "tools/build.sh": b64(b"#!/bin/sh\nmake\n"),
        "latest": b64(b"run.sh"),
        "README.md": b64(b"hello\n"),
        "run.sh": b64(b"#!/bin/sh\necho old\n"),
    }
    modes = {
        "tools/build.sh": "100755",
        "latest": "120000",
        "README.md": "100755",
        "run.sh": "100644",
    }

    resp = api.post(
        "/github/app/pull-request", json=request(files=files, modes=modes, base_sha=gh.root)
    )
    assert resp.status_code == 201, resp.text

    tree_call = next(b for m, p, b in gh.calls if (m, p) == ("POST", "/git/trees"))
    assert tree_call["tree"] == [
        {"path": "README.md", "mode": "100755", "type": "blob", "content": "hello\n"},
        {"path": "latest", "mode": "120000", "type": "blob", "content": "run.sh"},
        {"path": "run.sh", "mode": "100644", "type": "blob", "content": "#!/bin/sh\necho old\n"},
        {
            "path": "tools/build.sh",
            "mode": "100755",
            "type": "blob",
            "content": "#!/bin/sh\nmake\n",
        },
    ]
    assert gh.files_at("sazed/job-1") == {
        "README.md": ("100755", b"hello\n"),
        "latest": ("120000", b"run.sh"),
        "run.sh": ("100644", b"#!/bin/sh\necho old\n"),
        "tools/build.sh": ("100755", b"#!/bin/sh\nmake\n"),
        "logo.png": ("100644", b"\x89PNG\r\n\x1a\n\x00\x00"),
    }


def test_text_goes_inline_and_other_content_gets_a_blob_call(gh, api):
    """G6-5: UTF-8 text rides in the tree call; non-UTF-8, NUL-bearing and empty files get
    blob calls."""
    binary = b"\x89PNG\r\n\x1a\n\xff\xfe"
    files = {
        "logo.png": b64(binary),
        "notes.md": b64("café ✓\r\n".encode()),
        "nul.dat": b64(b"a\0b"),
        "empty.txt": "",
    }

    resp = api.post("/github/app/pull-request", json=request(files=files))
    assert resp.status_code == 201, resp.text

    blob_calls = [b["content"] for m, p, b in gh.calls if (m, p) == ("POST", "/git/blobs")]
    assert blob_calls == [b64(b""), b64(binary), b64(b"a\0b")]
    tree_call = next(b for m, p, b in gh.calls if (m, p) == ("POST", "/git/trees"))
    assert tree_call["tree"][2] == {
        "path": "notes.md",
        "mode": "100644",
        "type": "blob",
        "content": "café ✓\r\n",
    }
    assert gh.files_at("sazed/job-1")["notes.md"] == ("100644", "café ✓\r\n".encode())
    assert gh.files_at("sazed/job-1")["logo.png"] == ("100644", binary)


@pytest.mark.parametrize(
    ("extra", "detail"),
    [
        ({"files": {f"f{i}.txt": b64(b"x") for i in range(201)}}, "too many files"),
        ({"files": {"big.bin": "A" * (5 * 1024 * 1024 * 4 // 3 + 4)}}, "too large"),
        ({"files": {"a.txt": "not base64!"}}, "valid base64"),
        ({"modes": {"README.md": "100755"}}, "has no content"),
        ({"modes": {"ghost.sh": "100755"}}, "has no content"),
        ({"modes": {"run.sh": "160000"}}, None),
        ({"base_sha": "main"}, None),
        ({"files": {".github/workflows/ci.yml": b64(b"x")}}, "aren't allowed"),
        (
            {"files": {".github/workflows": b64(b"x")}, "modes": {".github/workflows": "120000"}},
            "aren't allowed",
        ),
        ({"files": {".github": b64(b"x")}}, "aren't allowed"),
    ],
)
def test_bad_requests_are_refused_before_any_github_call(gh, api, extra, detail):
    """G6-5 and validation: limits, content, modes and base_sha are checked before GitHub."""
    resp = api.post("/github/app/pull-request", json=request(**extra))
    assert resp.status_code == 422
    if detail:
        assert detail in resp.json()["detail"]
    assert gh.calls == []
