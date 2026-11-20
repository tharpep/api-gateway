import asyncio

import httpx

from app.routers import github

MERGED_PR = {
    "number": 83,
    "title": "feat: board cards",
    "state": "closed",
    "body": None,
    "user": {"login": "tharpep"},
    "head": {"ref": "feature/x", "sha": "de866131081494feb20161792d46a3cbcfa144bf"},
    "base": {"ref": "main", "sha": "0" * 40},
    "draft": False,
    "mergeable": None,
    "merged": True,
    "merged_at": "2026-11-19T20:53:40Z",
    "merge_commit_sha": "6f1e9762dc85c3e892ec32c876913b836ec772e2",
    "additions": 1337,
    "deletions": 108,
    "changed_files": 25,
    "created_at": "2026-11-19T18:00:00Z",
    "html_url": "https://github.com/tharpep/sazed-frontend/pull/83",
}


def test_get_pr_returns_head_sha_and_diff_size(monkeypatch):
    """The PR read carries the head commit, the merge commit and the diff size from GitHub."""

    async def fake(method, path, **kwargs):
        return httpx.Response(200, json=MERGED_PR)

    monkeypatch.setattr(github, "_gh", fake)
    pr = asyncio.run(github.get_pr("tharpep", "sazed-frontend", 83))
    assert pr["head"] == "feature/x"
    assert pr["head_sha"] == "de866131081494feb20161792d46a3cbcfa144bf"
    assert pr["merge_commit_sha"] == "6f1e9762dc85c3e892ec32c876913b836ec772e2"
    assert (pr["additions"], pr["deletions"], pr["changed_files"]) == (1337, 108, 25)
    assert pr["merged"] is True
