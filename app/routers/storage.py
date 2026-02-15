"""Storage endpoint — Google Drive KB/General folder."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
import httpx

from app.auth.google import GoogleOAuth, TokenData
from app.config import settings

router = APIRouter()

DRIVE_API = "https://www.googleapis.com/drive/v3"
_KB_ROOT = "Knowledge Base"
_KB_SUBFOLDER = "General"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_EXPORT_MIMES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_cached_token: TokenData | None = None
_oauth = GoogleOAuth()


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if needed."""
    global _cached_token
    if _cached_token is None or _oauth.is_token_expired(_cached_token):
        if not settings.google_refresh_token:
            raise HTTPException(503, "Google refresh token not configured")
        _cached_token = await _oauth.refresh_token(settings.google_refresh_token)
    return _cached_token.access_token


async def _api_get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    """Authenticated Drive API GET — auto-retries once on 401."""
    global _cached_token
    token = await _get_access_token()
    r = await client.get(
        f"{DRIVE_API}/{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 401:
        _cached_token = None
        token = await _get_access_token()
        r = await client.get(
            f"{DRIVE_API}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Drive API error: {r.text}")
    return r.json()


async def _find_folder_id(
    client: httpx.AsyncClient, name: str, parent_id: str | None = None
) -> str:
    """Find a Drive folder by name, optionally scoped to a parent. Returns folder ID."""
    q = f"name = '{name}' and mimeType = '{_FOLDER_MIME}' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    data = await _api_get(client, "files", {"q": q, "fields": "files(id)", "pageSize": 1})
    files = data.get("files", [])
    if not files:
        raise HTTPException(404, f"Drive folder '{name}' not found")
    return files[0]["id"]


async def _kb_general_id(client: httpx.AsyncClient) -> str:
    """Resolve the 'Knowledge Base → General' folder ID (2 API calls)."""
    kb_id = await _find_folder_id(client, _KB_ROOT)
    return await _find_folder_id(client, _KB_SUBFOLDER, parent_id=kb_id)


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str
    modified_time: str
    size: int | None = None


class DriveFilesResponse(BaseModel):
    files: list[DriveFile]
    count: int


@router.get("/files", response_model=DriveFilesResponse)
async def list_kb_files(
    modified_after: str | None = Query(
        default=None,
        description="ISO 8601 timestamp — return only files modified after this time",
    ),
):
    """List files in the Knowledge Base → General Google Drive folder."""
    async with httpx.AsyncClient() as client:
        folder_id = await _kb_general_id(client)
        q = f"'{folder_id}' in parents and trashed = false and mimeType != '{_FOLDER_MIME}'"
        if modified_after:
            q += f" and modifiedTime > '{modified_after}'"
        data = await _api_get(
            client,
            "files",
            {
                "q": q,
                "fields": "files(id, name, mimeType, modifiedTime, size)",
                "pageSize": 100,
                "orderBy": "modifiedTime desc",
            },
        )
    raw = data.get("files", [])
    return DriveFilesResponse(
        files=[
            DriveFile(
                id=f["id"],
                name=f["name"],
                mime_type=f["mimeType"],
                modified_time=f["modifiedTime"],
                size=int(f["size"]) if f.get("size") else None,
            )
            for f in raw
        ],
        count=len(raw),
    )


@router.get("/files/{file_id}/content")
async def get_file_content(file_id: str):
    """Download a file's raw content from Drive.

    Google Docs/Sheets/Slides are exported as plain text or CSV.
    All other files (PDF, DOCX, TXT, etc.) are returned as-is.
    Response includes X-File-Name and X-File-Id headers.
    """
    global _cached_token
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        meta = await _api_get(client, f"files/{file_id}", {"fields": "id, name, mimeType"})
        mime = meta.get("mimeType", "")
        name = meta.get("name", file_id)

        if mime in _EXPORT_MIMES:
            url = f"{DRIVE_API}/files/{file_id}/export"
            params: dict = {"mimeType": _EXPORT_MIMES[mime]}
        else:
            url = f"{DRIVE_API}/files/{file_id}"
            params = {"alt": "media"}

        token = await _get_access_token()
        r = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})

        if r.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            r = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})

        if r.status_code != 200:
            raise HTTPException(502, f"Drive download error for '{name}': {r.text}")

    content_type = r.headers.get("content-type", "application/octet-stream")
    return Response(
        content=r.content,
        media_type=content_type,
        headers={"X-File-Name": name, "X-File-Id": file_id},
    )
