"""GitHub integration — repos, issues, PRs, and code."""

import asyncio
import base64

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

router = APIRouter()

GITHUB_API = "https://api.github.com"


def _headers() -> dict[str, str]:
    if not settings.github_token:
        raise HTTPException(503, "GitHub token not configured")
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _owner(owner: str | None) -> str:
    """Resolve owner — falls back to configured default."""
    return owner or settings.github_owner


async def _gh(method: str, path: str, **kwargs) -> httpx.Response:
    """Make an authenticated GitHub API request."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, f"{GITHUB_API}{path}", headers=_headers(), **kwargs
        )
    if resp.status_code == 404:
        raise HTTPException(404, f"GitHub resource not found: {path}")
    if resp.status_code == 422:
        raise HTTPException(422, f"GitHub validation error: {resp.text}")
    if not resp.is_success:
        raise HTTPException(502, f"GitHub API error {resp.status_code}: {resp.text}")
    return resp


# ---------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------


@router.get("/repos")
async def list_repos(
    sort: str = Query(default="updated", description="'updated', 'created', 'pushed', 'full_name'"),
    per_page: int = Query(default=30, ge=1, le=100),
):
    """List all repos accessible to the authenticated user."""
    resp = await _gh(
        "GET", "/user/repos", params={"type": "owner", "sort": sort, "per_page": per_page}
    )
    return [
        {
            "name": r["name"],
            "full_name": r["full_name"],
            "description": r["description"],
            "language": r["language"],
            "stars": r["stargazers_count"],
            "forks": r["forks_count"],
            "open_issues": r["open_issues_count"],
            "private": r["private"],
            "default_branch": r["default_branch"],
            "url": r["html_url"],
            "updated_at": r["updated_at"],
        }
        for r in resp.json()
    ]


@router.get("/repos/{owner}/{repo}")
async def get_repo(owner: str, repo: str):
    """Get details for a specific repository."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}")
    r = resp.json()
    return {
        "name": r["name"],
        "full_name": r["full_name"],
        "description": r["description"],
        "language": r["language"],
        "stars": r["stargazers_count"],
        "forks": r["forks_count"],
        "open_issues": r["open_issues_count"],
        "default_branch": r["default_branch"],
        "private": r["private"],
        "url": r["html_url"],
        "updated_at": r["updated_at"],
    }


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/issues")
async def list_issues(
    owner: str,
    repo: str,
    state: str = Query(default="open", description="'open', 'closed', or 'all'"),
    labels: str | None = Query(default=None, description="Comma-separated label names"),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """List issues in a repository. Excludes pull requests."""
    params: dict = {"state": state, "per_page": per_page}
    if labels:
        params["labels"] = labels
    resp = await _gh("GET", f"/repos/{owner}/{repo}/issues", params=params)
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "state": i["state"],
            "labels": [lb["name"] for lb in i["labels"]],
            "author": i["user"]["login"],
            "created_at": i["created_at"],
            "updated_at": i["updated_at"],
            "url": i["html_url"],
        }
        for i in resp.json()
        if "pull_request" not in i  # issues endpoint returns PRs too — filter them out
    ]


@router.get("/repos/{owner}/{repo}/issues/{number}")
async def get_issue(owner: str, repo: str, number: int):
    """Get a specific issue including all comments."""
    issue_resp, comments_resp = await asyncio.gather(
        _gh("GET", f"/repos/{owner}/{repo}/issues/{number}"),
        _gh("GET", f"/repos/{owner}/{repo}/issues/{number}/comments"),
    )
    i = issue_resp.json()
    return {
        "number": i["number"],
        "title": i["title"],
        "state": i["state"],
        "body": i["body"],
        "author": i["user"]["login"],
        "labels": [lb["name"] for lb in i["labels"]],
        "created_at": i["created_at"],
        "updated_at": i["updated_at"],
        "url": i["html_url"],
        "comments": [
            {
                "author": c["user"]["login"],
                "body": c["body"],
                "created_at": c["created_at"],
            }
            for c in comments_resp.json()
        ],
    }


class CreateIssueRequest(BaseModel):
    title: str
    body: str | None = None
    labels: list[str] | None = None


@router.post("/repos/{owner}/{repo}/issues", status_code=201)
async def create_issue(owner: str, repo: str, req: CreateIssueRequest):
    """Open a new issue."""
    payload: dict = {"title": req.title}
    if req.body:
        payload["body"] = req.body
    if req.labels:
        payload["labels"] = req.labels
    resp = await _gh("POST", f"/repos/{owner}/{repo}/issues", json=payload)
    i = resp.json()
    return {"number": i["number"], "title": i["title"], "url": i["html_url"]}


class UpdateIssueRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    state: str | None = None        # "open" or "closed"
    labels: list[str] | None = None


@router.patch("/repos/{owner}/{repo}/issues/{number}")
async def update_issue(owner: str, repo: str, number: int, req: UpdateIssueRequest):
    """Update an issue — title, body, state (open/close), or labels."""
    payload: dict = {}
    if req.title is not None:
        payload["title"] = req.title
    if req.body is not None:
        payload["body"] = req.body
    if req.state is not None:
        payload["state"] = req.state
    if req.labels is not None:
        payload["labels"] = req.labels
    resp = await _gh("PATCH", f"/repos/{owner}/{repo}/issues/{number}", json=payload)
    i = resp.json()
    return {"number": i["number"], "title": i["title"], "state": i["state"], "url": i["html_url"]}


class CommentRequest(BaseModel):
    body: str


@router.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
async def add_issue_comment(owner: str, repo: str, number: int, req: CommentRequest):
    """Add a comment to an issue."""
    resp = await _gh(
        "POST",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        json={"body": req.body},
    )
    c = resp.json()
    return {"id": c["id"], "url": c["html_url"]}


# ---------------------------------------------------------------------------
# Pull Requests
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/pulls")
async def list_prs(
    owner: str,
    repo: str,
    state: str = Query(default="open", description="'open', 'closed', or 'all'"),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """List pull requests in a repository."""
    resp = await _gh(
        "GET", f"/repos/{owner}/{repo}/pulls", params={"state": state, "per_page": per_page}
    )
    return [
        {
            "number": p["number"],
            "title": p["title"],
            "state": p["state"],
            "author": p["user"]["login"],
            "head": p["head"]["ref"],
            "base": p["base"]["ref"],
            "draft": p["draft"],
            "created_at": p["created_at"],
            "url": p["html_url"],
        }
        for p in resp.json()
    ]


@router.get("/repos/{owner}/{repo}/pulls/{number}")
async def get_pr(owner: str, repo: str, number: int):
    """Get details for a specific pull request."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/pulls/{number}")
    p = resp.json()
    return {
        "number": p["number"],
        "title": p["title"],
        "state": p["state"],
        "body": p["body"],
        "author": p["user"]["login"],
        "head": p["head"]["ref"],
        "base": p["base"]["ref"],
        "draft": p["draft"],
        "mergeable": p.get("mergeable"),
        "created_at": p["created_at"],
        "url": p["html_url"],
    }


@router.post("/repos/{owner}/{repo}/pulls/{number}/comments", status_code=201)
async def add_pr_comment(owner: str, repo: str, number: int, req: CommentRequest):
    """Add a comment to a pull request."""
    # PRs use the issues comments endpoint for general (non-review) comments
    resp = await _gh(
        "POST",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        json={"body": req.body},
    )
    c = resp.json()
    return {"id": c["id"], "url": c["html_url"]}


class CreatePRRequest(BaseModel):
    title: str
    head: str           # source branch
    base: str           # target branch
    body: str | None = None
    draft: bool = False


@router.post("/repos/{owner}/{repo}/pulls", status_code=201)
async def create_pr(owner: str, repo: str, req: CreatePRRequest):
    """Open a new pull request."""
    payload: dict = {"title": req.title, "head": req.head, "base": req.base, "draft": req.draft}
    if req.body:
        payload["body"] = req.body
    resp = await _gh("POST", f"/repos/{owner}/{repo}/pulls", json=payload)
    p = resp.json()
    return {"number": p["number"], "title": p["title"], "draft": p["draft"], "url": p["html_url"]}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get("/search/issues")
async def search_issues(
    q: str = Query(..., description="Search query. Append 'repo:owner/name' to scope to a repo."),
    per_page: int = Query(default=10, ge=1, le=30),
):
    """Search issues and PRs across GitHub."""
    resp = await _gh("GET", "/search/issues", params={"q": q, "per_page": per_page})
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "state": i["state"],
            "repo": i["repository_url"].split("/repos/")[-1],
            "type": "pr" if "pull_request" in i else "issue",
            "created_at": i["created_at"],
            "url": i["html_url"],
        }
        for i in resp.json().get("items", [])
    ]


@router.get("/search/code")
async def search_code(
    q: str = Query(..., description="Search query. Use 'repo:owner/name' to scope to a repo."),
    per_page: int = Query(default=10, ge=1, le=30),
):
    """Search code across GitHub. Rate-limited to 30 requests/minute."""
    resp = await _gh("GET", "/search/code", params={"q": q, "per_page": per_page})
    return [
        {
            "name": f["name"],
            "path": f["path"],
            "repo": f["repository"]["full_name"],
            "url": f["html_url"],
            "sha": f["sha"],
        }
        for f in resp.json().get("items", [])
    ]


# ---------------------------------------------------------------------------
# File contents
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/contents/{path:path}")
async def get_github_file(
    owner: str,
    repo: str,
    path: str,
    ref: str | None = Query(default=None, description="Branch, tag, or commit SHA. Defaults to default branch."),
):
    """Read a file or list a directory from a GitHub repository."""
    params = {}
    if ref:
        params["ref"] = ref
    resp = await _gh("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)
    data = resp.json()

    if isinstance(data, list):
        # Directory listing
        return {
            "type": "directory",
            "path": path,
            "entries": [
                {"name": f["name"], "type": f["type"], "path": f["path"], "size": f.get("size")}
                for f in data
            ],
        }

    content = ""
    if data.get("encoding") == "base64":
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            raise HTTPException(422, f"Could not decode file content for '{path}'.")
    else:
        content = data.get("content", "")

    return {
        "type": "file",
        "name": data["name"],
        "path": data["path"],
        "size": data["size"],
        "sha": data["sha"],
        "content": content,
    }


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/commits")
async def list_commits(
    owner: str,
    repo: str,
    sha: str | None = Query(default=None, description="Branch, tag, or SHA to start from."),
    author: str | None = Query(default=None, description="Filter by GitHub username or email."),
    path: str | None = Query(default=None, description="Only commits touching this file path."),
    since: str | None = Query(default=None, description="ISO 8601 timestamp — only commits after this."),
    until: str | None = Query(default=None, description="ISO 8601 timestamp — only commits before this."),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """List commits on a repository."""
    params: dict = {"per_page": per_page}
    for k, v in {"sha": sha, "author": author, "path": path, "since": since, "until": until}.items():
        if v:
            params[k] = v
    resp = await _gh("GET", f"/repos/{owner}/{repo}/commits", params=params)
    return [
        {
            "sha": c["sha"],
            "short_sha": c["sha"][:7],
            "message": c["commit"]["message"].splitlines()[0],
            "author": c["commit"]["author"]["name"],
            "author_login": (c.get("author") or {}).get("login"),
            "date": c["commit"]["author"]["date"],
            "url": c["html_url"],
        }
        for c in resp.json()
    ]


@router.get("/repos/{owner}/{repo}/commits/{sha}")
async def get_commit(owner: str, repo: str, sha: str):
    """Get a single commit with stats and changed files."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/commits/{sha}")
    c = resp.json()
    return {
        "sha": c["sha"],
        "message": c["commit"]["message"],
        "author": c["commit"]["author"]["name"],
        "author_login": (c.get("author") or {}).get("login"),
        "date": c["commit"]["author"]["date"],
        "stats": c.get("stats", {}),
        "files": [
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", "")[:2000],
            }
            for f in c.get("files", [])
        ],
        "url": c["html_url"],
    }


# ---------------------------------------------------------------------------
# Branches & Tags
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(owner: str, repo: str, per_page: int = Query(default=30, ge=1, le=100)):
    """List branches in a repository."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/branches", params={"per_page": per_page})
    return [
        {
            "name": b["name"],
            "sha": b["commit"]["sha"],
            "protected": b["protected"],
        }
        for b in resp.json()
    ]


@router.get("/repos/{owner}/{repo}/tags")
async def list_tags(owner: str, repo: str, per_page: int = Query(default=30, ge=1, le=100)):
    """List tags in a repository."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/tags", params={"per_page": per_page})
    return [{"name": t["name"], "sha": t["commit"]["sha"]} for t in resp.json()]


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/releases")
async def list_releases(owner: str, repo: str, per_page: int = Query(default=10, ge=1, le=100)):
    """List releases in a repository."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/releases", params={"per_page": per_page})
    return [
        {
            "id": r["id"],
            "tag": r["tag_name"],
            "name": r["name"],
            "draft": r["draft"],
            "prerelease": r["prerelease"],
            "body": r.get("body", ""),
            "published_at": r["published_at"],
            "url": r["html_url"],
        }
        for r in resp.json()
    ]


@router.get("/repos/{owner}/{repo}/releases/latest")
async def get_latest_release(owner: str, repo: str):
    """Get the latest published (non-draft, non-prerelease) release."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/releases/latest")
    r = resp.json()
    return {
        "id": r["id"],
        "tag": r["tag_name"],
        "name": r["name"],
        "body": r.get("body", ""),
        "published_at": r["published_at"],
        "url": r["html_url"],
    }


# ---------------------------------------------------------------------------
# PR reviews & files
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/pulls/{number}/reviews")
async def get_pr_reviews(owner: str, repo: str, number: int):
    """Get all reviews on a pull request."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews")
    return [
        {
            "id": rv["id"],
            "author": rv["user"]["login"],
            "state": rv["state"],  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
            "body": rv.get("body", ""),
            "submitted_at": rv.get("submitted_at"),
        }
        for rv in resp.json()
    ]


@router.get("/repos/{owner}/{repo}/pulls/{number}/files")
async def get_pr_files(owner: str, repo: str, number: int):
    """List files changed in a pull request."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/pulls/{number}/files")
    return [
        {
            "filename": f["filename"],
            "status": f["status"],
            "additions": f["additions"],
            "deletions": f["deletions"],
            "patch": f.get("patch", "")[:2000],
        }
        for f in resp.json()
    ]


# ---------------------------------------------------------------------------
# Contributors & compare
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/contributors")
async def list_contributors(owner: str, repo: str, per_page: int = Query(default=20, ge=1, le=100)):
    """List contributors sorted by commit count."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/contributors", params={"per_page": per_page})
    return [
        {"login": c["login"], "contributions": c["contributions"], "url": c["html_url"]}
        for c in resp.json()
    ]


@router.get("/repos/{owner}/{repo}/compare")
async def compare_refs(
    owner: str,
    repo: str,
    base: str = Query(..., description="Base ref (branch, tag, or SHA)."),
    head: str = Query(..., description="Head ref to compare against base."),
):
    """Compare two refs — returns status, commit list, and changed files."""
    resp = await _gh("GET", f"/repos/{owner}/{repo}/compare/{base}...{head}")
    d = resp.json()
    return {
        "status": d["status"],  # "ahead", "behind", "diverged", "identical"
        "ahead_by": d["ahead_by"],
        "behind_by": d["behind_by"],
        "total_commits": len(d.get("commits", [])),
        "commits": [
            {
                "sha": c["sha"][:7],
                "message": c["commit"]["message"].splitlines()[0],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
            }
            for c in d.get("commits", [])[:20]
        ],
        "files_changed": len(d.get("files", [])),
        "files": [
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
            }
            for f in d.get("files", [])[:30]
        ],
    }
