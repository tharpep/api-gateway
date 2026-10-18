"""GitHub as the sazed-worker bot (plans/07): issues, comments, clone tokens, PRs from files.

Everything here acts as the GitHub App (`<app-slug>[bot]`), not as Pryce: it has no admin bypass
and can't approve its own PRs, so merges stay his. The App's key lives only here (GITHUB_APP_ID,
GITHUB_APP_KEY_B64); the worker never holds a GitHub credential that can write. Each operation
uses an installation token scoped to one repo and only the permissions it needs, cached until
shortly before GitHub expires it (one hour).

PRs are built through the Git Data API from a set of changed files (blobs, tree, commit, ref,
pull), so no git push or hooks run anywhere, and the branch name is checked to be `sazed/...`.
"""

import base64
import calendar
import logging
import re
import time
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.http_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()

GITHUB_API = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
_BRANCH_RE = re.compile(r"^sazed/[A-Za-z0-9._-]{1,80}$")
_MAX_FILES = 200
_MAX_TOTAL_BYTES = 5 * 1024 * 1024

_installations: dict[str, int] = {}  # owner -> installation id
_tokens: dict[tuple, tuple[str, float]] = {}  # (repo, perms) -> (token, expires epoch)


# ---------------------------------------------------------------- tokens


def _app_jwt() -> str:
    if not settings.github_app_id or not settings.github_app_key_b64:
        raise HTTPException(503, "GitHub App not configured (GITHUB_APP_ID / GITHUB_APP_KEY_B64)")
    key = base64.b64decode(settings.github_app_key_b64)
    now = int(time.time())
    # GitHub rejects exp more than 10 minutes out; 5 leaves room for clock skew.
    return jwt.encode(
        {"iat": now - 60, "exp": now + 300, "iss": settings.github_app_id.strip()},
        key,
        algorithm="RS256",
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _call(method: str, path: str, token: str, **kwargs) -> Any:
    try:
        resp = await get_client().request(
            method, f"{GITHUB_API}{path}", headers=_headers(token), timeout=30.0, **kwargs
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitHub unreachable: {e}") from e
    if resp.status_code >= 400:
        try:
            message = resp.json().get("message", resp.text)
        except ValueError:
            message = resp.text
        status = 404 if resp.status_code == 404 else 502
        raise HTTPException(status, f"GitHub {resp.status_code} on {method} {path}: {message}")
    return resp.json() if resp.content else None


def _check_repo(repo: str) -> tuple[str, str]:
    if not _REPO_RE.match(repo):
        raise HTTPException(422, f"'{repo}' isn't a repo like owner/name")
    owner, name = repo.split("/", 1)
    return owner, name


async def installation_token(repo: str, permissions: dict[str, str]) -> tuple[str, str]:
    """(token, expires_at) for one repo with exactly these permissions."""
    owner, name = _check_repo(repo)
    key = (repo.lower(), tuple(sorted(permissions.items())))
    cached = _tokens.get(key)
    if cached and cached[1] - time.time() > 300:
        return cached[0], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cached[1]))
    if owner not in _installations:
        inst = await _call("GET", f"/repos/{owner}/{name}/installation", _app_jwt())
        _installations[owner] = inst["id"]
    data = await _call(
        "POST",
        f"/app/installations/{_installations[owner]}/access_tokens",
        _app_jwt(),
        json={"repositories": [name], "permissions": permissions},
    )
    expires = calendar.timegm(time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
    _tokens[key] = (data["token"], expires)
    return data["token"], data["expires_at"]


# ---------------------------------------------------------------- issues


class IssueRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    body: str | None = Field(None, max_length=60_000)
    labels: list[str] | None = None


class CommentRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=60_000)


@router.post("/repos/{owner}/{repo}/issues", status_code=201)
async def create_issue(owner: str, repo: str, req: IssueRequest):
    """Open an issue as the bot."""
    token, _ = await installation_token(f"{owner}/{repo}", {"issues": "write"})
    payload: dict[str, Any] = {"title": req.title}
    if req.body:
        payload["body"] = req.body
    if req.labels:
        payload["labels"] = req.labels
    i = await _call("POST", f"/repos/{owner}/{repo}/issues", token, json=payload)
    return {"number": i["number"], "title": i["title"], "url": i["html_url"]}


@router.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
async def add_issue_comment(owner: str, repo: str, number: int, req: CommentRequest):
    """Comment on an issue or PR as the bot."""
    token, _ = await installation_token(f"{owner}/{repo}", {"issues": "write"})
    c = await _call(
        "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", token, json={"body": req.body}
    )
    return {"id": c["id"], "url": c["html_url"]}


# ---------------------------------------------------------------- worker support


class CloneTokenRequest(BaseModel):
    repo: str


@router.post("/clone-token")
async def clone_token(req: CloneTokenRequest):
    """A read-only, single-repo token for the worker to clone a private repo (expires in 1h)."""
    token, expires_at = await installation_token(req.repo, {"contents": "read"})
    return {"token": token, "expires_at": expires_at}


class PullRequestFromFiles(BaseModel):
    repo: str
    branch: str = Field(..., description="Must be sazed/<something>")
    title: str = Field(..., min_length=1, max_length=256)
    body: str = Field("", max_length=60_000)
    commit_message: str = Field(..., min_length=1, max_length=2000)
    # path -> new content (base64), or null to delete the file
    files: dict[str, str | None]
    base: str | None = None  # defaults to the repo's default branch


def _check_path(path: str) -> None:
    parts = path.split("/")
    if not path or path.startswith("/") or ".." in parts or "" in parts:
        raise HTTPException(422, f"bad path '{path}'")
    if parts[0] == ".git" or path.startswith(".github/workflows/"):
        raise HTTPException(422, f"changes to '{path}' aren't allowed (git internals / workflows)")


@router.post("/pull-request", status_code=201)
async def pull_request_from_files(req: PullRequestFromFiles):
    """Commit the given files on a new sazed/... branch off the base, and open a PR as the bot."""
    owner, name = _check_repo(req.repo)
    if not _BRANCH_RE.match(req.branch):
        raise HTTPException(422, "branch must look like sazed/<id>")
    if not req.files:
        raise HTTPException(422, "no files changed")
    if len(req.files) > _MAX_FILES:
        raise HTTPException(422, f"too many files ({len(req.files)} > {_MAX_FILES})")
    total = 0
    for path, content in req.files.items():
        _check_path(path)
        total += len(content or "")
    if total > _MAX_TOTAL_BYTES * 4 // 3:
        raise HTTPException(422, "changes are too large")

    token, _ = await installation_token(req.repo, {"contents": "write", "pull_requests": "write"})
    r = f"/repos/{owner}/{name}"
    base = req.base or (await _call("GET", r, token))["default_branch"]
    base_sha = (await _call("GET", f"{r}/git/ref/heads/{base}", token))["object"]["sha"]
    base_tree = (await _call("GET", f"{r}/git/commits/{base_sha}", token))["tree"]["sha"]
    # Keep existing file modes (e.g. executable scripts); new files are regular files.
    existing = await _call("GET", f"{r}/git/trees/{base_tree}?recursive=1", token)
    modes = {e["path"]: e["mode"] for e in existing.get("tree", []) if e.get("type") == "blob"}

    entries = []
    for path, content in sorted(req.files.items()):
        mode = modes.get(path, "100644")
        if content is None:
            if path in modes:
                entries.append({"path": path, "mode": mode, "type": "blob", "sha": None})
            continue
        blob = await _call(
            "POST", f"{r}/git/blobs", token, json={"content": content, "encoding": "base64"}
        )
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
    if not entries:
        raise HTTPException(422, "nothing to commit")

    tree = await _call(
        "POST", f"{r}/git/trees", token, json={"base_tree": base_tree, "tree": entries}
    )
    commit = await _call(
        "POST",
        f"{r}/git/commits",
        token,
        json={"message": req.commit_message, "tree": tree["sha"], "parents": [base_sha]},
    )
    await _call(
        "POST",
        f"{r}/git/refs",
        token,
        json={"ref": f"refs/heads/{req.branch}", "sha": commit["sha"]},
    )
    pr = await _call(
        "POST",
        f"{r}/pulls",
        token,
        json={"title": req.title, "head": req.branch, "base": base, "body": req.body},
    )
    logger.info("Bot opened PR %s#%s (%d files)", req.repo, pr["number"], len(entries))
    return {
        "number": pr["number"],
        "url": pr["html_url"],
        "branch": req.branch,
        "files": len(entries),
    }
