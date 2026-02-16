"""Storage endpoint — Google Drive Knowledge Base folders."""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
import httpx

from app.auth.google import GoogleOAuth, TokenData
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
_KB_ROOT = "Knowledge Base"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXPORT_MIMES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": _XLSX_MIME,
    "application/vnd.google-apps.presentation": "text/plain",
}

# Known KB subfolders: lowercase category key → Drive folder name
_KB_SUBFOLDERS: dict[str, str] = {
    "general": "General",
    "projects": "Projects",
    "purdue": "Purdue",
    "career": "Career",
    "reference": "Reference",
}

# Module-level folder ID cache — Drive folder IDs are stable across requests
_folder_id_cache: dict[str, str] = {}

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
    cache_key = f"{parent_id}/{name}"
    if cache_key in _folder_id_cache:
        return _folder_id_cache[cache_key]

    q = f"name = '{name}' and mimeType = '{_FOLDER_MIME}' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    data = await _api_get(client, "files", {"q": q, "fields": "files(id)", "pageSize": 1})
    files = data.get("files", [])
    if not files:
        raise HTTPException(404, f"Drive folder '{name}' not found")

    folder_id = files[0]["id"]
    _folder_id_cache[cache_key] = folder_id
    return folder_id


async def _kb_subfolder_id(client: httpx.AsyncClient, subfolder_name: str) -> str:
    """Resolve 'Knowledge Base → {subfolder_name}' folder ID (cached after first lookup)."""
    kb_id = await _find_folder_id(client, _KB_ROOT)
    return await _find_folder_id(client, subfolder_name, parent_id=kb_id)


async def _list_files_in_folder(
    client: httpx.AsyncClient,
    folder_id: str,
    category: str,
    modified_after: str | None,
) -> list[dict]:
    """List non-folder files in a Drive folder, returning raw dicts with category set."""
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
    return [
        {**f, "category": category}
        for f in data.get("files", [])
    ]


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str
    modified_time: str
    size: int | None = None
    category: str


class DriveFilesResponse(BaseModel):
    files: list[DriveFile]
    count: int


@router.get("/files", response_model=DriveFilesResponse)
async def list_kb_files(
    category: str | None = Query(
        default=None,
        description=f"KB subfolder to list. One of: {', '.join(_KB_SUBFOLDERS)}. "
                    "Omit to list all subfolders.",
    ),
    modified_after: str | None = Query(
        default=None,
        description="ISO 8601 timestamp — return only files modified after this time",
    ),
):
    """List files in Knowledge Base subfolders.

    Specify ?category=projects to list one subfolder, or omit to get all files
    across all subfolders. Each file includes its category (subfolder name).
    """
    if category is not None:
        category = category.lower()
        if category not in _KB_SUBFOLDERS:
            raise HTTPException(
                400,
                f"Unknown category '{category}'. Valid: {', '.join(_KB_SUBFOLDERS)}",
            )

    raw_files: list[dict] = []

    async with httpx.AsyncClient() as client:
        if category:
            # Single subfolder
            folder_id = await _kb_subfolder_id(client, _KB_SUBFOLDERS[category])
            raw_files = await _list_files_in_folder(client, folder_id, category, modified_after)
        else:
            # All subfolders — skip any that don't exist in Drive
            for cat_key, folder_name in _KB_SUBFOLDERS.items():
                try:
                    folder_id = await _kb_subfolder_id(client, folder_name)
                    files = await _list_files_in_folder(client, folder_id, cat_key, modified_after)
                    raw_files.extend(files)
                except HTTPException as e:
                    if e.status_code == 404:
                        logger.warning(f"KB subfolder '{folder_name}' not found in Drive, skipping")
                    else:
                        raise

    files = [
        DriveFile(
            id=f["id"],
            name=f["name"],
            mime_type=f["mimeType"],
            modified_time=f["modifiedTime"],
            size=int(f["size"]) if f.get("size") else None,
            category=f["category"],
        )
        for f in raw_files
    ]

    return DriveFilesResponse(files=files, count=len(files))


@router.get("/files/{file_id}/content")
async def get_file_content(file_id: str):
    """Download a file's raw content from Drive.

    Google Docs/Sheets/Slides are exported as plain text or CSV.
    All other files (PDF, DOCX, TXT, etc.) are returned as-is.
    Response includes X-File-Name, X-File-Id, and X-File-Category headers.
    """
    global _cached_token
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        meta = await _api_get(
            client, f"files/{file_id}", {"fields": "id, name, mimeType, parents"}
        )
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
