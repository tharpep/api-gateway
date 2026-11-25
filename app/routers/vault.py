"""Vault routes — capture, list, read, rewrite and discard notes through the vault service."""

import json
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.http_client import get_client

router = APIRouter()

_TIMEOUT = 20.0


class CaptureBody(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    source: str = Field(default="console", pattern=r"^[a-z][a-z0-9-]{0,49}$")


class RewriteBody(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=100000)
    version: str = Field(min_length=1, max_length=64)


def _detail(response: httpx.Response, field: str) -> str:
    try:
        text = response.json()[field]
    except (ValueError, KeyError, TypeError):
        return f"vault service answered {response.status_code}"
    return text if isinstance(text, str) else json.dumps(text)


async def _call(verb: str, key: str, body: dict[str, Any]) -> Any:
    if not settings.vault_url:
        raise HTTPException(503, "Notes service not configured (VAULT_URL not set)")
    if not key:
        raise HTTPException(503, "Notes key not configured")
    try:
        r = await get_client().post(
            f"{settings.vault_url.rstrip('/')}/v1/{verb}",
            json=body,
            headers={"X-Vault-Key": key},
            timeout=_TIMEOUT,
        )
    except httpx.ConnectError:
        raise HTTPException(503, "Notes service unreachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Notes service timed out")
    if r.status_code == 403:
        raise HTTPException(403, _detail(r, "refused"))
    if r.status_code == 409:
        raise HTTPException(409, _detail(r, "conflict"))
    if r.status_code == 400:
        raise HTTPException(400, _detail(r, "error"))
    if r.status_code != 200:
        raise HTTPException(502, f"Notes service answered {r.status_code}")
    try:
        return json.loads(r.json()["result"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(502, "Notes service returned an unreadable result")


@router.post("/capture", status_code=201)
async def capture(body: CaptureBody):
    """Capture a note into the vault inbox."""
    return await _call(
        "capture",
        settings.vault_capture_key,
        {"content": body.content, "source": body.source},
    )


@router.get("/notes")
async def notes(
    folder: list[Literal["inbox", "notes", "claude"]] = Query(default=[]),
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List notes, filtered by folder and text."""
    return await _call(
        "notes",
        settings.vault_api_key,
        {"folders": folder, "query": q, "limit": limit, "offset": offset},
    )


@router.get("/note")
async def note(path: str = Query(min_length=1, max_length=300)):
    """Read one note with its version."""
    return await _call("note", settings.vault_api_key, {"path": path})


@router.put("/note")
async def rewrite(body: RewriteBody):
    """Replace a note's content if its version still matches."""
    return await _call(
        "rewrite",
        settings.vault_capture_key,
        {"path": body.path, "content": body.content, "version": body.version},
    )


@router.delete("/note")
async def discard(path: str = Query(min_length=1, max_length=300)):
    """Discard a note."""
    return await _call("discard", settings.vault_capture_key, {"path": path})
