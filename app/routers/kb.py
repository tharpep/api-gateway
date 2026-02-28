"""KB proxy — forwards /kb/* requests to the MY-AI KB service."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

_TIMEOUT = 120.0  # sync can be slow on large Drive folders


def _kb_url(path: str) -> str:
    if not settings.kb_service_url:
        raise HTTPException(503, "KB service not configured (KB_SERVICE_URL not set)")
    return f"{settings.kb_service_url.rstrip('/')}/v1{path}"


def _kb_headers() -> dict:
    return {"X-API-Key": settings.kb_service_key} if settings.kb_service_key else {}


async def _proxy(request: Request, method: str, path: str) -> Response:
    """Forward a request to the KB service and return the response as-is."""
    url = _kb_url(path)
    body = await request.body()
    params = dict(request.query_params)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(
                method,
                url,
                content=body,
                params=params,
                headers={**_kb_headers(), "content-type": request.headers.get("content-type", "application/json")},
            )
    except httpx.ConnectError:
        raise HTTPException(503, "KB service unreachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "KB service timed out")

    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@router.post("/search")
async def kb_search(request: Request):
    """Search the knowledge base (proxied to KB service)."""
    return await _proxy(request, "POST", "/kb/search")


@router.post("/sync")
async def kb_sync(request: Request):
    """Trigger a Drive → KB sync (proxied to KB service)."""
    return await _proxy(request, "POST", "/kb/sync")


@router.get("/index")
async def kb_index(request: Request):
    """List all active KB sources with summaries (proxied to KB service)."""
    return await _proxy(request, "GET", "/kb/index")


@router.get("/sources")
async def kb_sources(request: Request):
    """List all tracked KB source files (proxied to KB service)."""
    return await _proxy(request, "GET", "/kb/sources")


@router.get("/files")
async def kb_files(request: Request):
    """List all indexed files with chunk counts (proxied to KB service)."""
    return await _proxy(request, "GET", "/kb/files")


@router.get("/stats")
async def kb_stats(request: Request):
    """KB chunk and file counts (proxied to KB service)."""
    return await _proxy(request, "GET", "/kb/stats")


@router.delete("/files/{drive_file_id}", status_code=204)
async def kb_delete_file(drive_file_id: str, request: Request):
    """Remove a file from the KB index (proxied to KB service)."""
    return await _proxy(request, "DELETE", f"/kb/files/{drive_file_id}")


@router.delete("", status_code=204)
async def kb_clear(request: Request):
    """Clear the entire KB index (proxied to KB service)."""
    return await _proxy(request, "DELETE", "/kb")
